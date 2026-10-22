"""Callable doctor domain shared by CLI, MCP, and server adapters."""
from __future__ import annotations

import json
import pathlib
import shlex
import shutil
import urllib.error
import urllib.request

from . import __version__
from .auth import http_auth_public_status, http_auth_token
from .domain import DomainError, pool_bounds_from_environment as pool_bounds
from .handoff import doctor_handoff
from .network import ALLOW_NON_LOOPBACK_FLAG, bind_host_warning, listen_allows_non_loopback
from .registry import listen_path, registry_path
from .registry_domain import (
    DEFAULT_START_SCRIPT,
    HISTORY_ALL_KEY,
    history_is_modified,
    normalize_default_state,
    normalize_registry,
    project_path,
)
from .service import script_is_placeholder
from .tailscale import probe_tailscale_status, resolve_tailscale_bin

DOCTOR_FAIL_CLOSED_EXIT = 2
DOCTOR_HARD_CHECKS = frozenset({
    "registry",
    "listen",
    "skill_files",
    "ui_reachability",
    "mcp_reachability",
    "handoff_kit",
    "bind_host",
})


def package_root():
    """Distribution root containing portskill/, skill/, scripts/, and docs/."""
    return pathlib.Path(__file__).resolve().parent.parent


def skill_dir():
    """Prefer package root; fall back to the module parent for thin wrappers."""
    root = package_root()
    if (root / "portskill").is_dir() or (root / "pyproject.toml").exists():
        return root
    return pathlib.Path(__file__).resolve().parent


def doctor_result(project="."):
    """Health check for install + live UI/MCP.

    Exit contract (idempotent, read-only — never wipes files):
    - 0 when healthy offline (absent/cold registry + listen, loopback / default bind)
    - 0 when only informational warnings fail (Tailscale missing; non-loopback
      bind_host with allow_non_loopback recorded)
    - DOCTOR_FAIL_CLOSED_EXIT (2) when a DOCTOR_HARD_CHECKS item is not ok:
      corrupt registry/listen, missing skill files, listening=true but UI/MCP
      unreachable, non-loopback bind without --allow-non-loopback, or invalid
      Session Handoff kit override
    scripts/doctor.sh must return the same codes (it execs this command).
    """
    checks = []
    path = registry_path()
    registry_ok = False
    if not path.exists():
        # Cold install: absent registry is OK (created on first write).
        checks.append({
            "name": "registry",
            "ok": True,
            "detail": f"absent (cold): {path} — created on first allocate/serve",
        })
    else:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                checks.append({
                    "name": "registry",
                    "ok": False,
                    "detail": f"corrupt_registry: not an object at {path}",
                })
            else:
                registry_ok = True
                checks.append({"name": "registry", "ok": True, "detail": str(path)})
        except json.JSONDecodeError as exc:
            checks.append({
                "name": "registry",
                "ok": False,
                "detail": (
                    f"corrupt_registry at {path}: {exc.msg} "
                    f"(line {exc.lineno} col {exc.colno}); refusing to wipe"
                ),
            })
        except OSError as exc:
            checks.append({"name": "registry", "ok": False, "detail": f"unreadable: {path}: {exc}"})

    checks.append({
        "name": "version",
        "ok": True,
        "detail": f"portskill {__version__}",
    })

    listen_file = listen_path()
    listen_payload = None
    ui_url = None
    mcp_url = None
    listening = False
    bind_host = None
    if not listen_file.is_file():
        checks.append({
            "name": "listen",
            "ok": True,
            "detail": f"absent: {listen_file} (server writes sticky listen after bind)",
        })
    else:
        try:
            listen_payload = json.loads(listen_file.read_text(encoding="utf-8"))
            if not isinstance(listen_payload, dict):
                checks.append({
                    "name": "listen",
                    "ok": False,
                    "detail": f"corrupt listen.json (not an object): {listen_file}",
                })
                listen_payload = None
            else:
                listening = bool(listen_payload.get("listening"))
                ui_url = listen_payload.get("ui_url")
                mcp_url = listen_payload.get("mcp_url")
                port = listen_payload.get("port")
                bind_host = listen_payload.get("host")
                checks.append({
                    "name": "listen",
                    "ok": True,
                    "detail": (
                        f"{listen_file} listening={listening} port={port} "
                        f"host={bind_host} ui_url={ui_url} mcp_url={mcp_url}"
                    ),
                })
        except json.JSONDecodeError as exc:
            checks.append({
                "name": "listen",
                "ok": False,
                "detail": f"corrupt listen.json at {listen_file}: {exc.msg}; refusing to wipe",
            })
        except OSError as exc:
            checks.append({"name": "listen", "ok": False, "detail": f"unreadable: {listen_file}: {exc}"})

    auth_status = http_auth_public_status()
    gate = "on" if auth_status.get("passkey_gate") else "off"
    cred_n = int(auth_status.get("passkey_credentials") or 0)
    if auth_status.get("configured"):
        checks.append({
            "name": "http_auth",
            "ok": True,
            "detail": (
                f"configured (token present) at {auth_status.get('path')}; "
                f"passkey_gate={gate} credentials={cred_n}"
            ),
        })
    else:
        checks.append({
            "name": "http_auth",
            "ok": True,  # informational — minted on first HTTP serve; doctor never writes
            "detail": (
                f"absent: {auth_status.get('path')} "
                "(minted on first HTTP serve; doctor never writes this file); "
                f"passkey_gate={gate} credentials={cred_n}"
            ),
        })

    loopback_warn = bind_host_warning(bind_host)
    allow_non_loopback = listen_allows_non_loopback(listen_payload)
    if loopback_warn:
        if allow_non_loopback:
            checks.append({
                "name": "bind_host",
                "ok": True,
                "detail": loopback_warn,
                "warning": True,
            })
        else:
            checks.append({
                "name": "bind_host",
                "ok": False,
                "detail": loopback_warn + f" doctor fails closed without {ALLOW_NON_LOOPBACK_FLAG}.",
                "warning": True,
            })
    elif bind_host:
        checks.append({
            "name": "bind_host",
            "ok": True,
            "detail": f"loopback ({bind_host})",
        })
    else:
        checks.append({
            "name": "bind_host",
            "ok": True,
            "detail": "unset (default bind is loopback 127.0.0.1)",
        })

    def _probe(name: str, url: object, *, bearer: str | None = None) -> None:
        if not listening:
            checks.append({
                "name": name,
                "ok": True,
                "detail": "skipped (not listening)",
            })
            return
        if not isinstance(url, str) or not url.strip():
            checks.append({
                "name": name,
                "ok": False,
                "detail": f"listening=true but {name} URL missing in listen.json",
            })
            return
        try:
            req = urllib.request.Request(url, method="GET")
            if bearer:
                req.add_header("Authorization", f"Bearer {bearer}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = int(getattr(resp, "status", None) or resp.getcode())
            if code != 200:
                checks.append({
                    "name": name,
                    "ok": False,
                    "detail": f"GET {url} -> HTTP {code}",
                })
            else:
                checks.append({
                    "name": name,
                    "ok": True,
                    "detail": f"GET {url} -> HTTP {code}",
                })
        except urllib.error.HTTPError as exc:
            checks.append({
                "name": name,
                "ok": False,
                "detail": f"GET {url} -> HTTP {int(exc.code)}",
            })
        except urllib.error.URLError as exc:
            checks.append({
                "name": name,
                "ok": False,
                "detail": f"GET {url}: {exc}",
            })
        except Exception as exc:  # pragma: no cover
            checks.append({
                "name": name,
                "ok": False,
                "detail": f"GET {url}: {exc}",
            })

    _probe("ui_reachability", ui_url)
    _probe("mcp_reachability", mcp_url, bearer=http_auth_token())

    ts_bin = resolve_tailscale_bin()
    ts_path = pathlib.Path(shlex.split(ts_bin)[0]) if ts_bin else None
    ts_exists = bool(ts_path and (ts_path.exists() or shutil.which(str(ts_path))))
    checks.append({
        "name": "tailscale_bin",
        "ok": True,  # informational — Serve is optional; do not fail CI/cold Linux
        "detail": f"{ts_bin}" + ("" if ts_exists else " (not found; Serve unavailable)"),
    })

    ts_status = probe_tailscale_status()
    checks.append({
        "name": "tailscale_auth",
        "ok": True,  # informational
        "detail": (
            f"{ts_status.get('chip')}: BackendState={ts_status.get('BackendState')!r}"
            + (
                ""
                if ts_status.get("logged_in")
                else f"; {ts_status.get('message') or 'login required for Serve'}"
            )
        ),
    })
    checks.append({
        "name": "tailscale_serve",
        "ok": True,
        "detail": (
            "Serve maps localhost ports onto your tailnet when logged in. "
            "UI toggle is Serve on/off; Funnel of user service ports remains "
            "available via CLI `--mode funnel`. Funnel of the Portskill listen "
            "port is refused. Tailscale is not HTTP authentication."
            + ("" if ts_status.get("logged_in") else " Log in before enabling Serve.")
        ),
    })

    skill = skill_dir()
    app_pkg = skill / "portskill"
    required_rel = [
        "portskill/__init__.py",
        "portskill/cli.py",
        "portskill/server.py",
        "portskill/mcp.py",
    ]
    missing = [name for name in required_rel if not (skill / name).exists()]
    skill_md_ok = (skill / "skill" / "SKILL.md").exists() or (skill / "SKILL.md").exists()
    ui_ok = (app_pkg / "static").is_dir()
    skill_ok = not missing and ui_ok
    detail = str(skill)
    if missing:
        detail += f"; missing {', '.join(missing)}"
    if not skill_md_ok:
        detail += "; optional agent skill sidecar not installed"
    if not ui_ok:
        detail += "; missing portskill/static/"
    checks.append({"name": "skill_files", "ok": skill_ok, "detail": detail})

    project = project_path(project)
    start_script = pathlib.Path(project) / DEFAULT_START_SCRIPT
    if start_script.exists():
        is_placeholder = script_is_placeholder(start_script)
        checks.append({
            "name": "start_script",
            "ok": True,  # informational — placeholder is common until customize
            "detail": (
                f"placeholder still present: {start_script}"
                if is_placeholder
                else f"customized: {start_script}"
            ),
        })
    else:
        checks.append({
            "name": "start_script",
            "ok": True,
            "detail": f"none at {start_script} (ok until allocate scaffolds one)",
        })

    default_on = 0
    default_off = 0
    if registry_ok:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                for project_entry in (data.get("projects") or {}).values():
                    for item in (project_entry or {}).get("ranges") or []:
                        if not isinstance(item, dict) or item.get("state") == "released":
                            continue
                        if normalize_default_state(item.get("default_state")) == "on":
                            default_on += 1
                        else:
                            default_off += 1
            checks.append({
                "name": "default_state",
                "ok": True,
                "detail": f"on={default_on} off={default_off} (non-released)",
            })
        except (OSError, json.JSONDecodeError):
            checks.append({
                "name": "default_state",
                "ok": True,
                "detail": "unavailable (registry unreadable after initial check)",
            })
    else:
        checks.append({
            "name": "default_state",
            "ok": True,
            "detail": "skipped (registry not readable)",
        })

    settings_for_handoff = {}
    if registry_ok:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict) and isinstance(data.get("settings"), dict):
                settings_for_handoff = data["settings"]
        except (OSError, json.JSONDecodeError):
            settings_for_handoff = {}
    handoff_check, handoff_info = doctor_handoff(settings_for_handoff)
    checks.append(handoff_check)

    # Hard fail-closed: corrupt registry/listen, broken install, claimed-but-unreachable UI/MCP,
    # missing Session Handoff kit or invalid kit override, non-loopback bind without allow.
    ok = all(item["ok"] for item in checks if item.get("name") in DOCTOR_HARD_CHECKS)
    focused_hist = None
    if registry_ok:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                start, end = pool_bounds()
                data = normalize_registry(data, start, end)
                focused = (data.get("settings") or {}).get("focused_environment")
                key = focused if isinstance(focused, str) and focused.strip() else HISTORY_ALL_KEY
                entry = (data.get("environment_history") or {}).get(key)
                focused_hist = {
                    "environment": key,
                    "modified": history_is_modified(entry) if entry else False,
                    "cursor": int((entry or {}).get("cursor") or 0) if isinstance(entry, dict) else 0,
                }
        except (OSError, json.JSONDecodeError):
            focused_hist = None
    payload = {
        "status": "ok" if ok else "error",
        "reason": None if ok else "doctor_failed",
        "message": loopback_warn,
        "version": __version__,
        "checks": checks,
        "registry_path": str(path),
        "registry_readable": registry_ok,
        "listen_path": str(listen_file),
        "listen": listen_payload,
        "bind_host": bind_host,
        "loopback": loopback_warn is None,
        "allow_non_loopback": allow_non_loopback,
        "http_auth": auth_status,
        "ui_url": ui_url,
        "mcp_url": mcp_url,
        "skill_dir": str(skill),
        "default_state_counts": {"on": default_on, "off": default_off},
        "tailscale": ts_status,
        "environment_history": focused_hist,
        "handoff_kit": handoff_info,
    }
    if not ok:
        raise DomainError(
            "doctor_failed",
            exit_code=DOCTOR_FAIL_CLOSED_EXIT,
            payload=payload,
        )
    return payload
