"""Callable Tailscale and set-tailnet domain.

Each ``*_result`` function returns the payload the CLI used to print and raises
:class:`DomainError` where the CLI used to exit non-zero.  A DomainError built
with ``payload=`` carries the exact legacy body (for example the exit-3
needs-input shapes, or the exit-2 ``status: error`` probe shapes), so the CLI,
MCP and HTTP adapters keep their transport contracts without a subprocess.
"""
from __future__ import annotations

import subprocess
import time
import webbrowser
from typing import Any

from .domain import DomainError, utc_now
from .lifecycle import ensure_tailscale_authenticated, read_listen_port_from_disk, run_tailnet
from .registry import normalize_settings, read_settings
from .registry_domain import extract_tailscale_advertise_host, normalize_range_record, project_path
from .registry_operations import (
    ensure_baseline,
    find_range_anywhere,
    locked_registry,
    maybe_push_history,
    mutate_registry_meta,
    require_project_directory,
    resolve_history_env_name,
)
from .tailscale import (
    extract_tailscale_auth_url,
    probe_tailscale_status,
    tailscale_base_command,
    tailscale_bin_available,
)

TAILNET_MODES = ("serve", "funnel", "none")


# --- set-tailnet -------------------------------------------------------------


def set_tailnet_result(options: dict[str, Any]) -> dict:
    """Update recorded tailnet mode; apply/tear down Serve when the range is active.

    UI toggles Serve on/off (serve|none). Funnel remains available via mode=funnel.
    Switching modes on a live range tears the previous mode down best-effort.
    """
    mode = options.get("mode")
    range_id = options.get("range_id")
    if mode not in TAILNET_MODES:
        raise DomainError("invalid_args", f"mode must be serve|funnel|none, got {mode!r}")
    project = project_path(options["project"]) if options.get("project") else None
    if project is not None:
        require_project_directory(project)
    environment = options.get("environment")

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, environment)
        ensure_baseline(registry, hist_key)
        found_project, item = find_range_anywhere(registry, range_id, project)
        if item is None:
            raise DomainError("not_found", f"range id not found: {range_id}")
        normalize_range_record(item)
        tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
        old_mode = tailnet.get("mode") or "none"
        if old_mode not in TAILNET_MODES:
            old_mode = "none"

        if mode in ("serve", "funnel"):
            ensure_tailscale_authenticated(mode)

        tailnet["mode"] = mode
        now = utc_now()
        if item.get("state") == "active":
            if mode in ("serve", "funnel"):
                port = item["start"]
                if old_mode in ("serve", "funnel") and old_mode != mode:
                    prev_port = tailnet.get("port") or item["start"]
                    try:
                        run_tailnet(old_mode, prev_port, off=True)
                    except DomainError:
                        pass  # best-effort teardown of the previous mode
                run_tailnet(mode, port, off=False)
                tailnet["port"] = port
                tailnet["configured_at"] = now
            elif mode == "none" and old_mode in ("serve", "funnel"):
                port = tailnet.get("port") or item["start"]
                run_tailnet(old_mode, port, off=True)
                tailnet["port"] = None
                tailnet["configured_at"] = None
            else:
                tailnet["port"] = None
                tailnet["configured_at"] = None
        elif mode == "none":
            tailnet["port"] = None
            tailnet["configured_at"] = None
        normalize_range_record(item)
        maybe_push_history(registry, "set-tailnet", hist_key)
        return {
            "status": "ok",
            "range": item,
            "project": found_project,
            "tailnet": mode,
            "previous_tailnet": old_mode,
        }, [found_project]

    return locked_registry(mutate)


# --- Serve Portskill itself ------------------------------------------------------


def build_portskill_serve_url(port, tailscale_self=None):
    """HTTPS Serve URL for Portskill listen port (tailscale serve --https=PORT)."""
    host = extract_tailscale_advertise_host(tailscale_self)
    if not host:
        status = probe_tailscale_status()
        host = extract_tailscale_advertise_host(status.get("Self"))
    if not host:
        return None
    return f"https://{host}:{int(port)}/"


def probe_portskill_serve_status(port=None):
    """Summarize whether Portskill listen port is Tailscale-Served (never Funnel)."""
    if port is None:
        port = read_listen_port_from_disk()
    status = probe_tailscale_status()
    try:
        pref = bool(read_settings().get("serve_portskill_on_tailscale"))
    except (OSError, TypeError, ValueError):
        pref = False
    url = build_portskill_serve_url(port, status.get("Self")) if port else None
    out = {
        "ok": True,
        "enabled_preference": pref,
        "listen_port": port,
        "serve_url": url,
        "active": bool(pref and port and status.get("logged_in")),
        "chip": "Off",
        "state": "off",
        "message": "Serve Portskill on Tailscale is off",
        "tailscale": status,
    }
    if status.get("state") == "binary_missing":
        out.update({
            "ok": False,
            "chip": "Binary missing",
            "state": "binary_missing",
            "message": status.get("message") or "Tailscale binary not found",
            "active": False,
        })
        return out
    if not port:
        out.update({
            "chip": "No listen port",
            "state": "no_listen",
            "message": "listen.json has no Portskill listen port yet",
            "active": False,
        })
        return out
    if pref:
        if status.get("logged_in"):
            out.update({
                "chip": "Serving",
                "state": "serving",
                "message": f"Tailscale Serve → localhost:{port}",
                "active": True,
                "serve_url": url,
            })
        else:
            out.update({
                "chip": "Needs login",
                "state": "needs_login",
                "message": "Serve preferred on but Tailscale Needs login",
                "active": False,
            })
    return out


def apply_portskill_tailscale_serve(enable: bool, *, persist: bool = True, port: int | None = None):
    """Configure or tear down Tailscale Serve for the Portskill listen port. Never Funnel."""
    if port is None:
        port = read_listen_port_from_disk()
    if not port:
        raise DomainError("no_listen_port", "Cannot Serve Portskill: listen.json port missing")
    available, ts_bin = tailscale_bin_available()
    if not available:
        raise DomainError(
            "tailscale_bin_missing",
            f"Tailscale binary not found: {ts_bin}",
            chip="Binary missing",
            state="binary_missing",
        )
    if enable:
        ensure_tailscale_authenticated("serve")
        run_tailnet("serve", int(port), off=False)
    else:
        try:
            run_tailnet("serve", int(port), off=True)
        except DomainError:
            pass  # teardown is best-effort when the mapping is already gone
    if persist:
        def mutate(registry):
            settings = normalize_settings(registry.get("settings"))
            settings["serve_portskill_on_tailscale"] = bool(enable)
            registry["settings"] = normalize_settings(settings)
            return {
                "status": "ok",
                "serve_portskill_on_tailscale": bool(enable),
                "listen_port": int(port),
            }, True, []

        mutate_registry_meta(mutate)
    status = probe_tailscale_status()
    url = build_portskill_serve_url(port, status.get("Self")) if enable else None
    return {
        "status": "ok",
        "enabled": bool(enable),
        "listen_port": int(port),
        "serve_url": url,
        "mode": "serve" if enable else "none",
        "funnel": False,
        "chip": "Serving" if enable else "Off",
        "state": "serving" if enable else "off",
        "message": (
            f"Tailscale Serve configured for localhost:{port}"
            if enable
            else f"Tailscale Serve torn down for localhost:{port}"
        ),
        "tailscale": status,
    }


def serve_portskill_result(options: dict[str, Any]) -> dict:
    """status|on|off for Serving the Portskill listen port (never Funnel)."""
    state = (options.get("state") or "status").strip().lower()
    persist = not bool(options.get("no_persist"))
    if state == "status":
        return {"status": "ok", **probe_portskill_serve_status()}
    if state not in ("on", "off"):
        raise DomainError("invalid_args", "--state must be on|off|status")
    return apply_portskill_tailscale_serve(state == "on", persist=persist)


# --- status / login / logout -----------------------------------------------------


def tailscale_status_result() -> dict:
    """Tailscale status. NeedsLogin is a valid shape (no error); a missing binary is exit 2."""
    status = probe_tailscale_status()
    payload = {
        "status": "ok" if status.get("logged_in") else "error",
        "reason": status.get("reason"),
        "chip": status.get("chip"),
        "state": status.get("state"),
        "message": status.get("message"),
        "BackendState": status.get("BackendState"),
        "Self": status.get("Self"),
        "AuthURL": status.get("AuthURL"),
        "binary": status.get("binary"),
        "logged_in": bool(status.get("logged_in")),
        "tailscale": status,
    }
    if status.get("state") == "binary_missing":
        raise DomainError(
            status.get("reason") or "tailscale_bin_missing",
            status.get("message"),
            payload=payload,
        )
    return payload


def obtain_tailscale_auth_url(existing=None):
    """Run `tailscale login` / `up` non-interactively to harvest a browser login URL."""
    auth_url = existing
    outputs = []
    for sub in (["login"], ["up"]):
        try:
            result = subprocess.run(
                tailscale_base_command() + sub,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12,
                stdin=subprocess.DEVNULL,
            )
            out = (result.stdout or "").strip()
            outputs.append({"cmd": " ".join(sub), "exit": result.returncode, "output": out[:2000]})
            found = extract_tailscale_auth_url(out)
            if found:
                auth_url = found
                break
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stdout:
                partial = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
            outputs.append({
                "cmd": " ".join(sub),
                "exit": None,
                "output": (partial or "")[:2000],
                "timeout": True,
            })
            found = extract_tailscale_auth_url(partial)
            if found:
                auth_url = found
                break
        except OSError as exc:
            outputs.append({"cmd": " ".join(sub), "exit": None, "output": str(exc)})
    if not auth_url:
        auth_url = extract_tailscale_auth_url('\n'.join(o.get("output") or "" for o in outputs))
    return auth_url, outputs


def poll_tailscale_running(timeout_sec=90, interval_sec=2.0):
    """Poll status --json until BackendState Running / logged in, or timeout."""
    deadline = time.time() + max(1, float(timeout_sec))
    last = None
    while time.time() < deadline:
        last = probe_tailscale_status()
        if last.get("logged_in") or last.get("BackendState") == "Running":
            return True, last
        time.sleep(max(0.5, float(interval_sec)))
    return False, last or probe_tailscale_status()


def _needs_login(payload: dict) -> DomainError:
    return DomainError(payload["reason"], payload.get("message"), exit_code=3, payload=payload)


def tailscale_login_result(options: dict[str, Any]) -> dict:
    """Browser Login flow: extract auth URL, open system browser, poll until Connected.

    Never hangs on interactive password/sudo prompts. Incomplete login raises an
    exit-3 needs-input DomainError carrying the legacy payload.
    """
    open_browser = not bool(options.get("no_open"))
    wait = options.get("wait")
    if wait is None:
        wait = 90
    try:
        wait = int(wait)
    except (TypeError, ValueError):
        wait = 90

    status = probe_tailscale_status()
    if status.get("state") == "binary_missing":
        raise DomainError(
            "tailscale_bin_missing",
            status.get("message") or "Tailscale binary not found",
            tailscale=status,
        )
    if status.get("logged_in"):
        return {
            "status": "ok",
            "already_logged_in": True,
            "logged_in": True,
            "chip": status.get("chip"),
            "BackendState": status.get("BackendState"),
            "Self": status.get("Self"),
            "browser_opened": False,
            "tailscale": status,
        }

    auth_url, outputs = obtain_tailscale_auth_url(status.get("AuthURL"))
    after_attempt = probe_tailscale_status()
    if after_attempt.get("logged_in"):
        return {
            "status": "ok",
            "logged_in": True,
            "chip": after_attempt.get("chip"),
            "BackendState": after_attempt.get("BackendState"),
            "Self": after_attempt.get("Self"),
            "attempts": outputs,
            "browser_opened": False,
            "tailscale": after_attempt,
        }
    if not auth_url:
        auth_url = after_attempt.get("AuthURL")

    browser_opened = False
    browser_error = None
    if auth_url and open_browser:
        try:
            browser_opened = bool(webbrowser.open(auth_url))
        except Exception as exc:  # noqa: BLE001 — best-effort Browser Login
            browser_error = str(exc)
            browser_opened = False

    if not auth_url:
        raise _needs_login({
            "status": "needs_input",
            "input": "tailscale_login",
            "reason": "tailscale_auth_required",
            "needs_input": True,
            "prompt": (
                "Could not get a Tailscale login URL. Open the Tailscale app, "
                "sign in, then click Browser Login again."
            ),
            "options": ["retry_browser_login"],
            "auth_url": None,
            "browser_opened": False,
            "message": (
                "Browser Login could not find a login.tailscale.com URL. "
                "Install/start Tailscale, then retry."
            ),
            "attempts": outputs,
            "tailscale": after_attempt,
            "resume_hint": "tailscale login",
        })

    if wait <= 0:
        raise _needs_login({
            "status": "needs_input",
            "input": "tailscale_login",
            "reason": "tailscale_auth_pending",
            "needs_input": True,
            "prompt": f"Complete Browser Login at {auth_url}, then retry status.",
            "options": ["retry_browser_login", "poll_status"],
            "auth_url": auth_url,
            "browser_opened": browser_opened,
            "browser_error": browser_error,
            "polled": False,
            "message": "Browser Login opened. Finish signing in, then Portskill will detect Connected.",
            "attempts": outputs,
            "tailscale": after_attempt,
            "resume_hint": "tailscale login",
        })

    ok_poll, final = poll_tailscale_running(timeout_sec=wait, interval_sec=2.0)
    if ok_poll:
        return {
            "status": "ok",
            "logged_in": True,
            "chip": final.get("chip"),
            "BackendState": final.get("BackendState"),
            "Self": final.get("Self"),
            "auth_url": auth_url,
            "browser_opened": browser_opened,
            "browser_error": browser_error,
            "polled": True,
            "attempts": outputs,
            "tailscale": final,
            "message": "Tailscale Connected after Browser Login.",
        }

    raise _needs_login({
        "status": "needs_input",
        "input": "tailscale_login",
        "reason": "tailscale_auth_timeout",
        "needs_input": True,
        "prompt": (
            f"Finish signing in at {auth_url}, then click Browser Login again "
            "(or wait and retry status)."
        ),
        "options": ["retry_browser_login", "open_url"],
        "auth_url": auth_url,
        "browser_opened": browser_opened,
        "browser_error": browser_error,
        "polled": True,
        "message": (
            f"Still Needs login after waiting {wait}s. Complete Browser Login in your "
            f"browser ({auth_url}), then retry."
        ),
        "attempts": outputs,
        "tailscale": final,
        "resume_hint": "tailscale login",
    })


def tailscale_logout_result() -> dict:
    """Log out of Tailscale. A non-zero `tailscale logout` is an exit-2 error payload."""
    # Legacy parity: the CLI tested the (available, binary) tuple, which is always
    # truthy, so a missing binary surfaces as tailscale_logout_failed below.
    try:
        result = subprocess.run(
            tailscale_base_command() + ["logout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        out = (result.stdout or "").strip()
    except subprocess.TimeoutExpired as exc:
        raise DomainError("tailscale_logout_timeout", "tailscale logout timed out") from exc
    except OSError as exc:
        raise DomainError("tailscale_logout_failed", str(exc)) from exc
    status = probe_tailscale_status()
    payload = {
        "status": "ok" if result.returncode == 0 else "error",
        "logout_exit": result.returncode,
        "logout_output": out[:2000],
        "chip": status.get("chip"),
        "state": status.get("state"),
        "logged_in": bool(status.get("logged_in")),
        "message": status.get("message") or ("Logged out" if result.returncode == 0 else out[:400]),
        "tailscale": status,
    }
    if result.returncode != 0:
        raise DomainError("tailscale_logout_failed", payload["message"], payload=payload)
    return payload
