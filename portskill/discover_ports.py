"""Discover local TCP listeners + Tailscale Serve mappings; diff vs registry."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlparse


LOCAL_BIND_HOSTS = {
    "127.0.0.1",
    "0.0.0.0",
    "*",
    "::",
    "[::]",
    "::1",
    "[::1]",
    "localhost",
}

# Ignore known OS / Apple noise unless already in Serve map (still shown if Serve).
NOISE_COMMAND_PREFIXES = (
    "rapportd",
    "ControlCe",
    "ControlCenter",
    "IPNExtens",  # Tailscale frontend — not the app listener
    "Tailscale",
)


def _is_local_bind(host: str) -> bool:
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in LOCAL_BIND_HOSTS:
        return True
    # bare IPv6 without brackets sometimes shows as :: or ::1 already covered
    if h.startswith("127."):
        return True
    return False


def _process_cmdline(pid: int | None) -> str | None:
    if not pid or pid <= 0:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (completed.stdout or "").strip()
    return line or None


def _guess_note(cmdline: str | None, command: str | None) -> str | None:
    text = cmdline or command or ""
    if not text:
        return None
    lower = text.lower()
    if "meetingtranscriber" in lower or "meeting-transcriber" in lower:
        return "Meeting Transcriber"
    if "mt_drop_server" in lower:
        return "Meeting Transcriber drop"
    if "meeting-output/serve.py" in lower or "serve.py --port" in lower and "meeting" in lower:
        return "Meeting prep UI"
    # Prefer last meaningful path segment from argv
    parts = text.split()
    for part in parts:
        if part.startswith("-"):
            continue
        base = pathlib.Path(part).name
        if base and base not in ("Python", "python", "python3", "python3.9", "python3.11", "node", "Node"):
            if base.endswith(".py") or base.endswith(".js") or "/" not in base:
                # Prefer script names
                if base.endswith((".py", ".js", ".mjs", ".ts")):
                    return base
    # Fall back to command name from lsof
    if command:
        return command
    return None


def _guess_project(cmdline: str | None) -> str | None:
    if not cmdline:
        return None
    # Look for absolute paths under common project roots
    for token in cmdline.split():
        if not token.startswith("/"):
            continue
        path = pathlib.Path(token)
        # Climb to a directory that looks like a project (has .git or pyproject/package.json)
        cur = path if path.is_dir() else path.parent
        for _ in range(6):
            if not cur or str(cur) in ("/", ""):
                break
            if (cur / ".git").exists() or (cur / "pyproject.toml").exists() or (cur / "package.json").exists():
                return str(cur)
            # Development/<name> heuristic
            if cur.parent.name == "Development" and cur.name:
                return str(cur)
            cur = cur.parent
    return None


def discover_local_listeners() -> list[dict[str, Any]]:
    """Poll TCP LISTEN sockets bound to localhost / all-interfaces (macOS lsof)."""
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    if not pathlib.Path(lsof).exists() and not shutil.which("lsof"):
        return []
    try:
        completed = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: dict[int, dict[str, Any]] = {}
    for line in (completed.stdout or "").splitlines():
        if not line or line.startswith("COMMAND"):
            continue
        # COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
        parts = line.split()
        if len(parts) < 9:
            continue
        command = parts[0]
        try:
            pid = int(parts[1])
        except ValueError:
            pid = None
        # lsof may leave "(LISTEN)" as its own token after split
        if parts[-1] in ("(LISTEN)", "LISTEN"):
            name = parts[-2]
        else:
            name = parts[-1]
        name = name.replace("(LISTEN)", "").strip()
        if name.upper().startswith("TCP"):
            # rare: "TCP 127.0.0.1:20012" glued — take last token already handled
            pass
        if ":" not in name:
            continue
        # Split host:port — handle IPv6 [::]:port and [addr]:port
        host, port_s = None, None
        m = re.match(r"^(\[[^\]]+\]|[^:]+):(\d+)$", name)
        if m:
            host, port_s = m.group(1), m.group(2)
        else:
            continue
        try:
            port = int(port_s)
        except ValueError:
            continue
        if port <= 0 or port > 65535:
            continue
        if not _is_local_bind(host):
            continue
        # Prefer non-IPNExtension / non-noise process for the same port
        is_noise = any(command.startswith(p) for p in NOISE_COMMAND_PREFIXES)
        existing = rows.get(port)
        if existing and existing.get("noise") and not is_noise:
            pass  # replace below
        elif existing and not existing.get("noise") and is_noise:
            continue
        elif existing and not is_noise and not existing.get("noise"):
            # Keep first app listener
            continue
        cmdline = _process_cmdline(pid) if not is_noise else None
        rows[port] = {
            "port": port,
            "bind": host,
            "pid": pid,
            "command": command,
            "cmdline": cmdline,
            "note": _guess_note(cmdline, command),
            "project_guess": _guess_project(cmdline),
            "noise": is_noise,
            "source": "listener",
        }
    # Drop pure noise unless we want them — keep them marked; UI can hide.
    return [rows[k] for k in sorted(rows.keys())]


def discover_tailscale_serve_ports(tailscale_base_command) -> dict[str, Any]:
    """Parse `tailscale serve status --json` into port → serve info. Never Funnel."""
    out: dict[str, Any] = {
        "ok": False,
        "ports": {},  # port(int) -> {https, proxy, handler_host}
        "raw_error": None,
    }
    try:
        completed = subprocess.run(
            list(tailscale_base_command()) + ["serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["raw_error"] = str(exc)
        return out
    text = (completed.stdout or "").strip()
    if completed.returncode != 0 and not text:
        out["raw_error"] = ((completed.stderr or "")[:300] or f"exit {completed.returncode}")
        return out
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        # Fallback: plain-text status — best-effort extract localhost:N
        ports = {}
        for m in re.finditer(r"localhost:(\d+)|127\.0\.0\.1:(\d+)", text):
            p = int(m.group(1) or m.group(2))
            ports[p] = {"https": True, "proxy": f"http://127.0.0.1:{p}", "source": "serve-text"}
        out["ok"] = True
        out["ports"] = ports
        out["format"] = "text"
        return out
    if not isinstance(data, dict):
        out["raw_error"] = "unexpected serve status shape"
        return out
    ports: dict[int, dict[str, Any]] = {}
    tcp = data.get("TCP") if isinstance(data.get("TCP"), dict) else {}
    for key, meta in tcp.items():
        try:
            p = int(key)
        except (TypeError, ValueError):
            continue
        entry = ports.setdefault(p, {"https": False, "proxy": None, "source": "serve"})
        if isinstance(meta, dict) and meta.get("HTTPS"):
            entry["https"] = True
    web = data.get("Web") if isinstance(data.get("Web"), dict) else {}
    for host_port, cfg in web.items():
        # host_port like "name.tailnet.ts.net:20012"
        serve_port = None
        if isinstance(host_port, str) and ":" in host_port:
            try:
                serve_port = int(host_port.rsplit(":", 1)[-1])
            except ValueError:
                serve_port = None
        handlers = (cfg or {}).get("Handlers") if isinstance(cfg, dict) else None
        if not isinstance(handlers, dict):
            continue
        for _path, handler in handlers.items():
            if not isinstance(handler, dict):
                continue
            proxy = handler.get("Proxy") or handler.get("proxy")
            local_port = None
            if isinstance(proxy, str):
                try:
                    parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
                    if parsed.port:
                        local_port = int(parsed.port)
                except ValueError:
                    local_port = None
            target = local_port or serve_port
            if target is None:
                continue
            entry = ports.setdefault(target, {"https": True, "proxy": proxy, "source": "serve"})
            if proxy:
                entry["proxy"] = proxy
            if serve_port is not None:
                entry["serve_port"] = serve_port
            entry["handler_host"] = host_port
            entry["https"] = True
    out["ok"] = True
    out["ports"] = ports
    out["format"] = "json"
    return out


def _find_claiming_range(registry: dict, port: int) -> dict | None:
    for project, entry in (registry.get("projects") or {}).items():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("ranges") or []:
            if not isinstance(item, dict):
                continue
            if item.get("state") == "released":
                continue
            try:
                start = int(item["start"])
                end = int(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= port <= end:
                return {
                    "project": project,
                    "range_id": item.get("id"),
                    "start": start,
                    "end": end,
                    "note": item.get("note"),
                    "state": item.get("state"),
                    "tailnet": ((item.get("tailnet") or {}).get("mode") if isinstance(item.get("tailnet"), dict) else None),
                }
    return None


def build_ports_discovery(registry: dict, tailscale_base_command) -> dict[str, Any]:
    """Full discovery payload: listeners, serve, claimed, missing."""
    listeners = discover_local_listeners()
    serve = discover_tailscale_serve_ports(tailscale_base_command)
    serve_ports = {int(p): info for p, info in (serve.get("ports") or {}).items()}

    # Union of interesting ports: local listeners (non-noise) + serve-backed ports
    by_port: dict[int, dict[str, Any]] = {}
    for row in listeners:
        port = int(row["port"])
        if row.get("noise") and port not in serve_ports:
            # Skip OS/Tailscale frontend noise unless Serve maps it
            continue
        by_port[port] = {
            "port": port,
            "bind": row.get("bind"),
            "pid": row.get("pid"),
            "command": row.get("command"),
            "cmdline": row.get("cmdline"),
            "note": row.get("note"),
            "project_guess": row.get("project_guess"),
            "listening": True,
            "serve": False,
            "serve_info": None,
            "claimed": False,
            "claim": None,
            "missing": False,
        }
    for port, info in serve_ports.items():
        entry = by_port.setdefault(
            port,
            {
                "port": port,
                "bind": None,
                "pid": None,
                "command": None,
                "cmdline": None,
                "note": None,
                "project_guess": None,
                "listening": False,
                "serve": False,
                "serve_info": None,
                "claimed": False,
                "claim": None,
                "missing": False,
            },
        )
        entry["serve"] = True
        entry["serve_info"] = info

    claimed = []
    missing = []
    discoveries = []
    for port in sorted(by_port.keys()):
        entry = by_port[port]
        claim = _find_claiming_range(registry, port)
        if claim:
            entry["claimed"] = True
            entry["claim"] = claim
            entry["missing"] = False
            claimed.append(entry)
        else:
            entry["claimed"] = False
            entry["missing"] = True
            missing.append(entry)
        # Prefer: listener note → claim note → serve label
        if not entry.get("note"):
            if claim and claim.get("note"):
                entry["note"] = claim.get("note")
            elif entry.get("serve"):
                entry["note"] = f"Tailscale Serve :{port}"
        elif claim and claim.get("note") and str(entry.get("note") or "").startswith("Tailscale Serve"):
            entry["note"] = claim.get("note")
        discoveries.append(entry)

    return {
        "status": "ok",
        "listeners": listeners,
        "serve": {
            "ok": serve.get("ok"),
            "format": serve.get("format"),
            "ports": {str(k): v for k, v in sorted(serve_ports.items())},
            "error": serve.get("raw_error"),
        },
        "discoveries": discoveries,
        "claimed": claimed,
        "missing": missing,
        "counts": {
            "listeners": len(listeners),
            "serve": len(serve_ports),
            "discoveries": len(discoveries),
            "claimed": len(claimed),
            "missing": len(missing),
        },
    }


def default_tailnet_for_import(port: int, serve_ports: dict, user_tailnet: str | None) -> str:
    """serve only if already Serving or user opts in; else none. Never funnel."""
    if user_tailnet in ("serve", "none"):
        return user_tailnet
    if user_tailnet == "funnel":
        return "none"  # Never Funnel from discover import
    if int(port) in serve_ports:
        return "serve"
    return "none"
