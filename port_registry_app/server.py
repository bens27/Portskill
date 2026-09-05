#!/usr/bin/env python3
"""Portskill server: light HTML UI + MCP HTTP (stdlib-only).

Default: select a listen port (sticky listen.json → dogfood allocate → bind),
then serve UI + MCP. Historical default was 127.0.0.1:8765.
Also: --mcp-stdio for MCP clients; UI helpers live in this module.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import pathlib
import re
import signal
import socket
import sys
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from . import __version__
from .handoff import (
    install_help_text,
    run_codex_install,
    run_package_sh,
    status_payload,
)
from .mcp import (
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_DEFS,
    discovery_payload,
    mcp_handle,
    mcp_stdio_loop,
    run_cli,
)
from .cli import (
    apply_portskill_tailscale_serve,
    bind_host_warning,
    default_machines,
    extract_tailscale_advertise_host,
    get_machine,
    listen_path as cli_listen_path,
    normalize_machines,
    normalize_mcp_user_commands,
    probe_portskill_serve_status,
    probe_tailscale_status,
    resolve_range_host,
    resolve_range_scheme,
    resolve_range_url,
)

DEFAULT_REGISTRY_PATH = "~/.config/port-registry/registry.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765  # historical default only; launch prefers sticky/allocate
LISTEN_FILENAME = "listen.json"
PORTSKILL_NOTE = "Portskill UI+MCP"
PORTSKILL_PROJECT_ID = "portskill-app"
BIND_RETRIES = 5

# Runtime listen broadcast (set after successful bind)
_ACTIVE_LISTEN: dict | None = None

# --- Iterate Mode (UI iteration; local package writes only) -----------------
_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
_APP_DIR = pathlib.Path(__file__).resolve().parent
_STATIC_DIR = _APP_DIR / "static"
_ITERATE_TOKEN_FILE = _STATIC_DIR / "iterate-tokens.css"
_ITERATE_WORKING_SET: dict = {
    "workingSet": {},
    "preview": "working",
    "selected": "",
    "updatedAt": None,
}


def _config_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.config/port-registry"))


def listen_path() -> pathlib.Path:
    """Broadcast file beside registry.json (never wipes registry).

    Honors PORTSKILL_LISTEN_PATH or the directory of PORT_REGISTRY_PATH so
    tests can use temp dirs without touching ~/.config/port-registry/.
    """
    return cli_listen_path()


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def port_bindable(host: str, port: int) -> bool:
    """True if we can bind TCP (host, port) right now (probe only)."""
    if not isinstance(port, int) or port <= 0 or port > 65535:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR — avoid false-positive when something is already listening.
        sock.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def read_listen_file() -> dict | None:
    path = listen_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_listen_file(payload: dict) -> pathlib.Path:
    """Atomic write of listen.json under ~/.config/port-registry/."""
    path = listen_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def mark_listen_stopped() -> None:
    """Best-effort: set listening:false in listen.json (keep port/urls for sticky)."""
    global _ACTIVE_LISTEN
    data = read_listen_file() or {}
    if _ACTIVE_LISTEN and isinstance(_ACTIVE_LISTEN, dict):
        # Prefer in-memory snapshot so we do not clobber a newer writer
        if data.get("pid") and _ACTIVE_LISTEN.get("pid") and data.get("pid") != _ACTIVE_LISTEN.get("pid"):
            return
        data = dict(_ACTIVE_LISTEN)
    if not data:
        return
    data["listening"] = False
    data["stopped_at"] = utc_now_iso()
    try:
        write_listen_file(data)
    except OSError as exc:
        print(f"listen.json stop mark failed: {exc}", flush=True)
    _ACTIVE_LISTEN = None


def build_listen_payload(host: str, port: int) -> dict:
    ui = f"http://{host}:{port}/"
    mcp = f"http://{host}:{port}/mcp"
    path = listen_path()
    return {
        "version": 1,
        "listening": True,
        "host": host,
        "port": int(port),
        "ui_url": ui,
        "mcp_url": mcp,
        "mcp_post": f"POST {mcp} (JSON-RPC)",
        "mcp_get_discovery": f"GET {mcp}",
        "stdio": "python3 -m port_registry_app --mcp-stdio",
        "registry_path": str(registry_path()),
        "listen_path": str(path),
        "pid": os.getpid(),
        "started_at": utc_now_iso(),
        "setup": {
            "cursor_mcp_http_hint": (
                "Point an HTTP MCP client at mcp_url; or use stdio config in examples/mcp.stdio.json"
            ),
            "tools_endpoint": "initialize / tools/list / tools/call via JSON-RPC on /mcp",
        },
    }


def print_listen_banner(payload: dict) -> None:
    print("", flush=True)
    print("======== Portskill ========", flush=True)
    print(f"UI:            {payload.get('ui_url')}", flush=True)
    print(f"MCP POST:      {payload.get('mcp_post')}", flush=True)
    print(f"MCP discovery: {payload.get('mcp_get_discovery')}", flush=True)
    print(f"listen.json:   {payload.get('listen_path') or listen_path()}", flush=True)
    print(f"Stdio MCP:     {payload.get('stdio')}", flush=True)
    print(f"Registry:      {payload.get('registry_path') or registry_path()}", flush=True)
    print("===========================", flush=True)
    print("", flush=True)


def _portskill_project_path() -> str:
    """Stable project key for dogfood claim: package root (exists as a directory)."""
    return str(_PACKAGE_DIR.resolve())


def _iter_portskill_claim_ports(raw: dict | None = None) -> list[int]:
    """Ports from active/reserved ranges with Portskill UI+MCP note (or portskill-app project)."""
    raw = raw if isinstance(raw, dict) else load_registry()
    ports: list[int] = []
    projects = raw.get("projects") or {}
    pkg = _portskill_project_path()
    for project, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("ranges") or []:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "reserved")
            if state == "released":
                continue
            note = item.get("note")
            is_note = isinstance(note, str) and note.strip() == PORTSKILL_NOTE
            is_proj = project in (pkg, PORTSKILL_PROJECT_ID) or str(project).endswith(
                "/" + PORTSKILL_PROJECT_ID
            )
            if not (is_note or is_proj):
                continue
            try:
                start = int(item.get("start") or 0)
            except (TypeError, ValueError):
                continue
            if start > 0 and start not in ports:
                ports.append(start)
    return ports


def _dogfood_allocate_port(prefer_start: int | None = None) -> int | None:
    """Claim one port via registry allocate (dogfood). Returns start or None."""
    argv = [
        "allocate",
        "--count",
        "1",
        "--project",
        _portskill_project_path(),
        "--note",
        PORTSKILL_NOTE,
        "--tailnet",
        "none",
    ]
    if prefer_start is not None:
        argv += ["--start", str(int(prefer_start))]
    code, payload, stdout = run_cli(argv)
    if code != 0:
        print(
            f"dogfood allocate failed ({code}): {payload or stdout}",
            flush=True,
        )
        return None
    rng = (payload or {}).get("range") if isinstance(payload, dict) else None
    if isinstance(rng, dict) and rng.get("start") is not None:
        try:
            return int(rng["start"])
        except (TypeError, ValueError):
            return None
    return None


def _find_os_free_pool_port(host: str, raw: dict | None = None) -> int | None:
    """Next pool port that is registry-free and OS-bindable."""
    raw = raw if isinstance(raw, dict) else load_registry()
    pool = raw.get("pool") or {"start": 20000, "end": 29999}
    try:
        pool_start = int(pool.get("start", 20000))
        pool_end = int(pool.get("end", 29999))
    except (TypeError, ValueError):
        pool_start, pool_end = 20000, 29999
    occupied: set[int] = set()
    for _project, entry in (raw.get("projects") or {}).items():
        for item in (entry or {}).get("ranges") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("state") or "") == "released":
                continue
            try:
                s = int(item.get("start") or 0)
                e = int(item.get("end") or s)
            except (TypeError, ValueError):
                continue
            for p in range(s, e + 1):
                occupied.add(p)
    for candidate in range(pool_start, pool_end + 1):
        if candidate in occupied:
            continue
        if port_bindable(host, candidate):
            return candidate
    return None


def select_listen_port(host: str) -> tuple[int, str]:
    """Sticky listen.json → existing claim → dogfood allocate. Returns (port, source)."""
    sticky = read_listen_file()
    sticky_port = None
    if isinstance(sticky, dict) and sticky.get("port") is not None:
        try:
            sticky_port = int(sticky["port"])
        except (TypeError, ValueError):
            sticky_port = None
    # Soft-restart race: previous process may still hold the port briefly after stop.
    if sticky_port:
        for _ in range(10):
            if port_bindable(host, sticky_port):
                return sticky_port, "sticky"
            time.sleep(0.15)

    for claimed in _iter_portskill_claim_ports():
        if sticky_port and claimed == sticky_port:
            # Already waited above
            continue
        for _ in range(4):
            if port_bindable(host, claimed):
                return claimed, "claim"
            time.sleep(0.1)

    allocated = _dogfood_allocate_port()
    if allocated is not None and port_bindable(host, allocated):
        return allocated, "allocated"
    if allocated is not None:
        # Claimed in registry but OS busy — find another free port and claim it.
        alt = _find_os_free_pool_port(host)
        if alt is not None:
            claimed_alt = _dogfood_allocate_port(prefer_start=alt)
            if claimed_alt is not None and port_bindable(host, claimed_alt):
                return claimed_alt, "allocated-retry"
            if port_bindable(host, alt):
                return alt, "pool-free"

    # Last resort: any OS-free pool port (may already be claimed above failure path)
    alt = _find_os_free_pool_port(host)
    if alt is not None:
        _dogfood_allocate_port(prefer_start=alt)
        return alt, "pool-free"

    # Absolute fallback: historical default if bindable
    if port_bindable(host, DEFAULT_PORT):
        return DEFAULT_PORT, "historical-default"

    raise OSError("no bindable port found for Portskill UI+MCP")


def resolve_explicit_port(args_port: int | None) -> int | None:
    """CLI --port / PORTSKILL_PORT / PORT_REGISTRY_APP_PORT win when set."""
    if args_port is not None:
        return int(args_port)
    for key in ("PORTSKILL_PORT", "PORT_REGISTRY_APP_PORT"):
        raw = os.environ.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(str(raw).strip())
        except ValueError as exc:
            raise SystemExit(f"{key} must be an integer, got {raw!r}") from exc
    return None


def collab_inbox_path() -> pathlib.Path:
    """Prefer ~/.config/port-registry/collab/inbox.jsonl; else package .portskill-collab/."""
    preferred = _config_dir() / "collab" / "inbox.jsonl"
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        fallback = _PACKAGE_DIR / ".portskill-collab" / "inbox.jsonl"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def iterate_read_collab(limit: int = 80) -> list:
    path = collab_inbox_path()
    if not path.is_file():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def iterate_append_collab(role: str, text: str) -> dict:
    path = collab_inbox_path()
    msg = {
        "id": str(uuid.uuid4()),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "role": role or "ben",
        "text": text,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg, separators=(",", ":"), sort_keys=True) + "\n")
    return msg


def iterate_state_payload() -> dict:
    return {
        "ok": True,
        "workingSet": dict(_ITERATE_WORKING_SET.get("workingSet") or {}),
        "preview": _ITERATE_WORKING_SET.get("preview") or "working",
        "selected": _ITERATE_WORKING_SET.get("selected") or "",
        "updatedAt": _ITERATE_WORKING_SET.get("updatedAt"),
        "collabRecent": iterate_read_collab(30),
        "tokenFile": str(_ITERATE_TOKEN_FILE),
    }


_ALLOWED_TOKEN_KEYS = {
    "--paper",
    "--panel",
    "--ink",
    "--muted",
    "--line",
    "--cobalt",
    "--green",
    "--amber",
    "--red",
    "--violet",
}


def iterate_update_working_set(body: dict) -> dict:
    ws = body.get("workingSet") or body.get("working_set") or {}
    if not isinstance(ws, dict):
        return {"ok": False, "message": "workingSet must be an object"}
    cleaned = {}
    for key, val in ws.items():
        if key in _ALLOWED_TOKEN_KEYS and isinstance(val, str) and val.strip():
            cleaned[key] = val.strip()
    _ITERATE_WORKING_SET["workingSet"] = cleaned
    preview = body.get("preview")
    if preview in ("current", "working"):
        _ITERATE_WORKING_SET["preview"] = preview
    selected = body.get("selected")
    if isinstance(selected, str):
        _ITERATE_WORKING_SET["selected"] = selected
    _ITERATE_WORKING_SET["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"ok": True, "workingSet": cleaned, "preview": _ITERATE_WORKING_SET["preview"]}


def iterate_persist_tokens(body: dict | None = None) -> dict:
    """Write working set into package static/iterate-tokens.css only."""
    body = body or {}
    ws = body.get("workingSet") or body.get("working_set") or _ITERATE_WORKING_SET.get("workingSet") or {}
    if not isinstance(ws, dict) or not ws:
        # still allow writing defaults snapshot
        ws = dict(_ITERATE_WORKING_SET.get("workingSet") or {})
    cleaned = {}
    for key in _ALLOWED_TOKEN_KEYS:
        val = ws.get(key)
        if isinstance(val, str) and val.strip():
            cleaned[key] = val.strip()
    if not cleaned:
        return {"ok": False, "message": "no token values to persist"}
    # Safety: only under package static/
    target = _ITERATE_TOKEN_FILE.resolve()
    static_root = _STATIC_DIR.resolve()
    if static_root not in target.parents and target != static_root:
        return {"ok": False, "message": "refuse write outside package static/"}
    if not str(target).startswith(str(static_root)):
        return {"ok": False, "message": "refuse write outside package static/"}
    lines = [
        "/* Portskill Iterate Mode — persisted CSS tokens.",
        " * Written by POST /iterate/persist. Marked section for honest patching.",
        " * ITERATE-TOKENS-BEGIN",
        " */",
        ":root {",
    ]
    for key in sorted(cleaned.keys()):
        lines.append(f"  {key}: {cleaned[key]};")
    lines.extend(["}", "/* ITERATE-TOKENS-END */", ""])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "message": f"write failed: {exc}"}
    _ITERATE_WORKING_SET["workingSet"] = cleaned
    _ITERATE_WORKING_SET["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "ok": True,
        "path": str(target.relative_to(_PACKAGE_DIR)) if target.is_relative_to(_PACKAGE_DIR) else str(target),
        "absolutePath": str(target),
        "workingSet": cleaned,
    }


def _static_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if lower.endswith(".css"):
        return "text/css; charset=utf-8"
    if lower.endswith(".html"):
        return "text/html; charset=utf-8"
    if lower.endswith(".json"):
        return "application/json; charset=utf-8"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def serve_static_file(handler: "Handler", rel: str) -> bool:
    """Serve a file under port_registry_app/static/ only. Returns True if handled."""
    rel = unquote(rel).lstrip("/")
    if ".." in rel.split("/") or rel.startswith("/"):
        handler._send_json(400, {"ok": False, "message": "bad path"})
        return True
    target = (_STATIC_DIR / rel).resolve()
    try:
        target.relative_to(_STATIC_DIR.resolve())
    except ValueError:
        handler._send_json(400, {"ok": False, "message": "path escapes static/"})
        return True
    if not target.is_file():
        return False
    data = target.read_bytes()
    handler._send(200, data, _static_content_type(target.name))
    return True


def registry_path() -> pathlib.Path:
    configured = os.environ.get("PORT_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)
    return pathlib.Path(configured).expanduser()


def load_registry() -> dict:
    path = registry_path()
    empty = {
        "version": 1,
        "pool": {"start": 20000, "end": 29999},
        "projects": {},
        "presets": {},
        "settings": {
            "auto_apply_preset": None,
            "auto_apply_on_launch": False,
            "auto_exit_on_shutdown": False,
            "require_compat": True,
            "mcp_tools": {},
            "mcp_user_commands": {},
            "serve_portskill_on_tailscale": True,
            "handoff_enabled": False,
            "handoff_kit": None,
        },
        "machines": default_machines(),
    }
    if not path.exists():
        return empty
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return empty
    data.setdefault("version", 1)
    data.setdefault("pool", {"start": 20000, "end": 29999})
    data.setdefault("projects", {})
    if not isinstance(data.get("presets"), dict):
        data["presets"] = {}
    settings = data.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    preset = settings.get("auto_apply_preset")
    if preset is None or (isinstance(preset, str) and preset.strip().lower() in ("", "none", "off", "null")):
        preset = None
    elif isinstance(preset, str):
        preset = preset.strip()
    else:
        preset = None
    flag = settings.get("auto_apply_on_launch", False)
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("1", "true", "yes", "on")
    else:
        flag = bool(flag)
    exit_flag = settings.get("auto_exit_on_shutdown", False)
    if isinstance(exit_flag, str):
        exit_flag = exit_flag.strip().lower() in ("1", "true", "yes", "on")
    else:
        exit_flag = bool(exit_flag)
    # Always on (product rule)
    req = True
    tabs = settings.get("open_environment_tabs", [])
    if not isinstance(tabs, list):
        tabs = []
    focused = settings.get("focused_environment")
    if isinstance(focused, str) and focused.strip():
        focused = focused.strip()
    else:
        focused = None
    raw_mcp = settings.get("mcp_tools", {})
    mcp_tools = {}
    if isinstance(raw_mcp, dict):
        for key, val in raw_mcp.items():
            if not isinstance(key, str) or not key.strip():
                continue
            name = key.strip()
            if isinstance(val, bool):
                mcp_tools[name] = val
            elif isinstance(val, str):
                mcp_tools[name] = val.strip().lower() in ("1", "true", "yes", "on")
            else:
                mcp_tools[name] = bool(val)
    serve_ps = settings.get("serve_portskill_on_tailscale", True)
    if isinstance(serve_ps, str):
        serve_ps = serve_ps.strip().lower() in ("1", "true", "yes", "on")
    else:
        serve_ps = bool(serve_ps)
    mcp_user_commands = normalize_mcp_user_commands(settings.get("mcp_user_commands", {}))
    handoff_enabled = settings.get("handoff_enabled", False)
    if isinstance(handoff_enabled, str):
        handoff_enabled = handoff_enabled.strip().lower() in ("1", "true", "yes", "on")
    else:
        handoff_enabled = bool(handoff_enabled)
    handoff_kit = settings.get("handoff_kit")
    if isinstance(handoff_kit, str) and handoff_kit.strip():
        handoff_kit = handoff_kit.strip()
    else:
        handoff_kit = None
    data["settings"] = {
        "auto_apply_preset": preset,
        "auto_apply_on_launch": flag,
        "auto_exit_on_shutdown": exit_flag,
        "require_compat": req,
        "open_environment_tabs": [t for t in tabs if isinstance(t, str) and t.strip()],
        "focused_environment": focused,
        "mcp_tools": mcp_tools,
        "mcp_user_commands": mcp_user_commands,
        "serve_portskill_on_tailscale": serve_ps,
        "handoff_enabled": handoff_enabled,
        "handoff_kit": handoff_kit,
    }
    data["machines"] = normalize_machines(data.get("machines"))
    return data


def project_label(project: str) -> str:
    segments = [part for part in project.replace("\\", "/").split("/") if part]
    return segments[-1] if segments else project


def normalize_state(value: str) -> str:
    if value in ("reserved", "active", "released"):
        return value
    return "reserved"


def normalize_tailnet(value) -> str | None:
    if value in ("serve", "funnel", "none"):
        return value
    return None


def preset_labels_for_range(raw: dict, range_id: str, start: int, end: int) -> list[str]:
    """Presets that claim this range or overlapping ports (unique names)."""
    labels = []
    presets = raw.get("presets") or {}
    projects = raw.get("projects") or {}
    # Build id -> (start,end) map
    by_id = {}
    for project, entry in projects.items():
        for item in (entry or {}).get("ranges") or []:
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = (
                    int(item.get("start") or 0),
                    int(item.get("end") or 0),
                    project,
                    item.get("note"),
                )
    for name, entry in sorted(presets.items()):
        if not isinstance(entry, dict):
            continue
        for svc in entry.get("services") or []:
            if not isinstance(svc, dict):
                continue
            rid = svc.get("range_id")
            s = e = None
            if isinstance(rid, str) and rid.strip() and rid.strip() in by_id:
                if rid.strip() == range_id:
                    if name not in labels:
                        labels.append(name)
                    break
                s, e, _p, _n = by_id[rid.strip()]
            else:
                # match by project+note
                note = svc.get("note")
                proj = svc.get("project")
                for pid, (ps, pe, pproj, pnote) in by_id.items():
                    if pid == range_id:
                        continue
                    if proj and str(proj) in pproj or pproj == proj:
                        if (note or None) == (pnote or None):
                            s, e = ps, pe
                            break
            if s is not None and e is not None and not (end < s or e < start):
                if name not in labels:
                    labels.append(name)
                break
    # Also: if this range_id is listed in a preset, include that preset
    for name, entry in sorted(presets.items()):
        if not isinstance(entry, dict):
            continue
        for svc in entry.get("services") or []:
            if isinstance(svc, dict) and svc.get("range_id") == range_id:
                if name not in labels:
                    labels.append(name)
                break
    return labels


def build_view(raw: dict) -> dict:
    projects_out = []
    for project, entry in (raw.get("projects") or {}).items():
        ranges_raw = list((entry or {}).get("ranges") or [])
        ranges = []
        for item in ranges_raw:
            state = normalize_state(str(item.get("state") or "reserved"))
            tailnet = normalize_tailnet((item.get("tailnet") or {}).get("mode"))
            lifecycle = item.get("lifecycle") or {}
            default_state = str(item.get("default_state") or "off").lower()
            if default_state not in ("on", "off"):
                default_state = "off"
            rid = str(item.get("id") or "")
            r_start = int(item.get("start") or 0)
            r_end = int(item.get("end") or 0)
            also_presets = preset_labels_for_range(raw, rid, r_start, r_end)
            mid = item.get("machine_id") or item.get("machine") or item.get("host_id") or "local"
            if not isinstance(mid, str) or not mid.strip():
                mid = "local"
            else:
                mid = mid.strip()
            machine = get_machine(raw, mid) or default_machines()["local"]
            kind = machine.get("kind") or ("local" if mid == "local" else "remote")
            ts_self = raw.get("_ts_self")
            host = resolve_range_host(raw, item, tailscale_self=ts_self, allow_probe=False)
            # Let resolve_range_url / resolve_range_scheme decide scheme (serve/funnel → https)
            url = resolve_range_url(
                raw,
                {**item, "start": r_start, "machine_id": mid},
                port=r_start,
                tailscale_self=ts_self,
                allow_probe=False,
            )
            scheme = resolve_range_scheme({**item, "start": r_start, "machine_id": mid})
            if not url:
                if kind == "remote":
                    mhost = machine.get("host")
                    if isinstance(mhost, str) and mhost.strip() and mhost.strip() not in ("127.0.0.1", "localhost", "::1"):
                        host = mhost.strip()
                        url = f"{scheme}://{host}:{r_start}/"
                    else:
                        url = None
                        if host in ("127.0.0.1", "localhost", "::1"):
                            host = None
                else:
                    if not host:
                        host = "127.0.0.1"
                    url = f"{scheme}://{host}:{r_start}/"
            elif url:
                # Prefer scheme from resolved URL when present
                try:
                    parsed = urlparse(url)
                    if parsed.scheme in ("http", "https"):
                        scheme = parsed.scheme
                except Exception:
                    pass
            draft = ((raw.get("workspace_draft") or {}).get("ranges") or {}).get(rid) or {}
            if not isinstance(draft, dict):
                draft = {}
            live_start = lifecycle.get("start_script")
            live_stop = lifecycle.get("stop_script")
            live_command = item.get("command")
            live_cwd = item.get("cwd")
            start_script = draft.get("start_script", live_start)
            stop_script = draft.get("stop_script", live_stop)
            command = draft.get("command", live_command)
            cwd = draft.get("cwd", live_cwd)
            ranges.append(
                {
                    "id": rid,
                    "start": r_start,
                    "end": r_end,
                    "state": state,
                    "note": item.get("note"),
                    "tailnetMode": tailnet,
                    "pid": lifecycle.get("pid"),
                    "startedAt": lifecycle.get("started_at"),
                    "defaultState": default_state,
                    "alsoUsedInPresets": also_presets,
                    "machineId": mid,
                    "machineLabel": machine.get("label") or mid,
                    "machineKind": kind,
                    "host": host,
                    "scheme": scheme,
                    "url": url,
                    "startScript": start_script,
                    "stopScript": stop_script,
                    "command": command,
                    "cwd": cwd,
                    "liveStartScript": live_start,
                    "liveStopScript": live_stop,
                    "liveCommand": live_command,
                    "liveCwd": live_cwd,
                    "draftPending": bool(draft),
                }
            )
        ranges.sort(key=lambda r: r["start"])
        projects_out.append(
            {
                "project": project,
                "projectLabel": project_label(project),
                "ranges": ranges,
            }
        )
    projects_out.sort(key=lambda p: p["projectLabel"])

    all_ranges = [r for p in projects_out for r in p["ranges"]]
    non_released = [r for r in all_ranges if r["state"] != "released"]
    pool = raw.get("pool") or {"start": 20000, "end": 29999}
    pool_start = int(pool.get("start", 20000))
    pool_end = int(pool.get("end", 29999))
    pool_ports_used = sum((r["end"] - r["start"] + 1) for r in non_released)

    default_on = sum(1 for r in non_released if r.get("defaultState") == "on")
    presets_out = []
    for name, entry in sorted((raw.get("presets") or {}).items()):
        if not isinstance(entry, dict):
            entry = {}
        services = entry.get("services") if isinstance(entry.get("services"), list) else []
        presets_out.append({
            "name": name,
            "description": entry.get("description") or "",
            "updatedAt": entry.get("updated_at"),
            "serviceCount": len(services),
        })
    settings = raw.get("settings") or {}
    machines = normalize_machines(raw.get("machines"))
    machines_out = [
        {
            "id": m.get("id"),
            "label": m.get("label"),
            "host": m.get("host"),
            "kind": m.get("kind"),
        }
        for m in (machines[k] for k in sorted(machines.keys(), key=lambda x: (x != "local", x)))
    ]
    focused_name = settings.get("focused_environment")
    hist_key = focused_name if isinstance(focused_name, str) and focused_name.strip() else "__all__"
    env_hist_raw = raw.get("environment_history") or {}
    entry = env_hist_raw.get(hist_key) if isinstance(env_hist_raw, dict) else None
    if not isinstance(entry, dict):
        entry = {}
    try:
        cursor = int(entry.get("cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    steps_out = []
    for i, step in enumerate(entry.get("steps") or []):
        if not isinstance(step, dict):
            continue
        steps_out.append({
            "id": step.get("id") or str(i + 1),
            "label": step.get("label") or "edit",
            "at": step.get("at"),
            "index": i + 1,
        })
    environment_history = {
        "environment": hist_key,
        "modified": cursor > 0,
        "cursor": cursor,
        "steps": steps_out,
        "baselineLabel": "Original",
        "hasBaseline": entry.get("baseline") is not None,
    }
    ws_draft = raw.get("workspace_draft") if isinstance(raw.get("workspace_draft"), dict) else {}
    draft_ranges = ws_draft.get("ranges") if isinstance(ws_draft.get("ranges"), dict) else {}
    edit_mode = bool(ws_draft.get("edit_mode"))
    draft_modified = bool(draft_ranges)
    return {
        "pool": {"start": pool_start, "end": pool_end},
        "projects": projects_out,
        "presets": presets_out,
        "machines": machines_out,
        "settings": {
            "autoApplyPreset": settings.get("auto_apply_preset"),
            "autoApplyOnLaunch": bool(settings.get("auto_apply_on_launch")),
            "autoExitOnShutdown": bool(settings.get("auto_exit_on_shutdown")),
            "requireCompat": True,
            "openEnvironmentTabs": list(settings.get("open_environment_tabs") or []),
            "focusedEnvironment": settings.get("focused_environment"),
            "mcpTools": dict(settings.get("mcp_tools") or {}),
            "mcpUserCommands": dict(settings.get("mcp_user_commands") or {}),
            "servePortskillOnTailscale": bool(settings.get("serve_portskill_on_tailscale")),
            "handoffEnabled": bool(settings.get("handoff_enabled")),
            "handoffKit": settings.get("handoff_kit"),
        },
        "workspaceDraft": {
            "editMode": edit_mode,
            "modified": draft_modified,
            "rangeCount": len(draft_ranges),
        },
        "environmentHistory": environment_history,
        "stats": {
            "totalRanges": len(all_ranges),
            "activeRanges": sum(1 for r in all_ranges if r["state"] == "active"),
            "poolPortsUsed": pool_ports_used,
            "poolPortsTotal": pool_end - pool_start + 1,
            "defaultOn": default_on,
            "presetCount": len(presets_out),
        },
    }


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def state_class(state: str) -> str:
    if state == "active":
        return "b-exec"
    if state == "reserved":
        return "b-onb"
    return "b-term"


def stat_html(label: str, value: int) -> str:
    return f'<div class="stat"><span>{esc(label)}</span><b>{value}</b></div>'


def range_html(project: str, rng: dict) -> str:
    state = rng["state"]
    port_label = (
        str(rng["start"]) if rng["start"] == rng["end"] else f'{rng["start"]}–{rng["end"]}'
    )
    url = rng.get("url")
    if not url and rng.get("machineKind") != "remote":
        scheme = rng.get("scheme") or "http"
        if scheme not in ("http", "https"):
            scheme = "http"
        url = f'{scheme}://{rng.get("host") or "127.0.0.1"}:{rng["start"]}/'
    if url:
        port_link = (
            f'<a class="mono pr-port-link" href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'data-range-id="{esc(rng["id"])}" title="{esc(url)}">{esc(port_label)}</a>'
        )
    else:
        port_link = f'<span class="mono" title="No advertise host for remote">{esc(port_label)}</span>'
    tailnet = rng.get("tailnetMode")
    tailnet_badge = (
        f'<span class="badge b-onb">{esc(tailnet)}</span>'
        if tailnet and tailnet != "none"
        else ""
    )
    default_state = rng.get("defaultState") or "off"
    default_on = default_state == "on"
    default_badge = "b-exec" if default_on else "b-term"
    default_label = "Default On" if default_on else "Default Off"
    serve_on = tailnet == "serve"
    note = esc(rng["note"]) if rng.get("note") else "<em>no note</em>"
    machine_label = rng.get("machineLabel") or rng.get("machineId") or "This Mac"
    machine_kind = rng.get("machineKind") or "local"
    machine_badge = (
        f'<span class="badge b-onb" title="Machine {esc(rng.get("machineId") or "local")} '
        f'({esc(machine_kind)})">{esc(machine_label)}</span>'
    )
    host_disp = rng.get("host")
    if not host_disp:
        host_disp = "" if (rng.get("machineKind") == "remote") else "127.0.0.1"
    meta_parts = [note]
    if host_disp:
        meta_parts.append(f'<span class="pr-host">{esc(host_disp)}</span>')
    if rng.get("pid"):
        meta_parts.append(f'pid {rng["pid"]}')
    also = []
    for name in (rng.get("alsoUsedInPresets") or []):
        if isinstance(name, str) and name.strip() and name not in also:
            also.append(name)
    warn_badge = ""
    warn_banner = ""
    if also:
        label = ", ".join(also)
        # Single warn surface (badge only) — banner duplicated the same text per card
        warn_badge = (
            f'<span class="badge b-warn" title="Port also claimed by other saved environment(s)">'
            f'Also used in: {esc(label)}</span>'
        )
    is_remote = machine_kind == "remote"
    can_start = state != "active" and not is_remote
    can_stop = state == "active" and not is_remote
    can_release = state != "released"
    start_dis = "" if can_start else "disabled"
    stop_dis = "" if can_stop else "disabled"
    release_dis = "" if can_release else "disabled"
    default_checked = "checked" if default_on else ""
    serve_checked = "checked" if serve_on else ""
    remote_note = (
        '<div class="pr-range-meta"><em>Remote — links only (not started from this Mac)</em></div>'
        if is_remote else ""
    )
    editing_class = " pr-editing" if rng.get("draftPending") else ""
    draft_pending = "1" if rng.get("draftPending") else "0"
    ss = esc(rng.get("startScript") or "")
    st = esc(rng.get("stopScript") or "")
    cmd = esc(rng.get("command") or "")
    cwd_v = esc(rng.get("cwd") or "")
    # Baseline = last saved (live) values; current may include pending draft
    base_ss = esc(rng.get("liveStartScript") if rng.get("liveStartScript") is not None else rng.get("startScript") or "")
    base_st = esc(rng.get("liveStopScript") if rng.get("liveStopScript") is not None else rng.get("stopScript") or "")
    base_cmd = esc(rng.get("liveCommand") if rng.get("liveCommand") is not None else rng.get("command") or "")
    base_cwd = esc(rng.get("liveCwd") if rng.get("liveCwd") is not None else rng.get("cwd") or "")
    note_raw = rng.get("note") or ""
    port_title = (
        str(rng["start"]) if rng["start"] == rng["end"] else f'{rng["start"]}–{rng["end"]}'
    )
    modified_vis = "" if rng.get("draftPending") else " hidden"
    draft_badge = '<span class="badge b-block">draft</span>' if rng.get("draftPending") else ""
    return f"""<div class="pr-range{editing_class}" id="range-{esc(rng["id"])}" data-range-id="{esc(rng["id"])}" data-machine="{esc(rng.get("machineId") or "local")}" data-draft-pending="{draft_pending}" data-start-script="{ss}" data-stop-script="{st}" data-command="{cmd}" data-cwd="{cwd_v}" data-base-start-script="{base_ss}" data-base-stop-script="{base_st}" data-base-command="{base_cmd}" data-base-cwd="{base_cwd}" data-note="{esc(note_raw)}" data-port-label="{esc(port_title)}">
  <button type="button" class="pr-card-pencil" data-pr-action="card-edit" data-range-id="{esc(rng["id"])}" aria-label="Edit" title="Edit service settings">✎</button>
  <div class="pr-range-top">
    {port_link}
    <span class="badge {state_class(state)}">{esc(state)}</span>
    {tailnet_badge}
    {machine_badge}
    <span class="badge {default_badge}">{esc(default_label)}</span>
    {warn_badge}
  </div>
  {warn_banner}
  {remote_note}
  <div class="pr-range-meta">{" &middot; ".join(meta_parts)}</div>
  <div class="pr-range-lifecycle">
    <div class="pr-life-summary" data-life-view>
      <span class="tag">start</span> {esc(rng.get("startScript") or "—")}
      &middot; <span class="tag">stop</span> {esc(rng.get("stopScript") or "—")}
      &middot; <span class="tag">cmd</span> {esc(rng.get("command") or "—")}
      &middot; <span class="tag">cwd</span> {esc(rng.get("cwd") or "—")}
      {draft_badge}
    </div>
    <div class="pr-life-edit" data-life-edit>
      <label>start_script <input type="text" data-draft-field="start_script" data-range-id="{esc(rng["id"])}" value="{ss}" autocomplete="off"></label>
      <label>stop_script <input type="text" data-draft-field="stop_script" data-range-id="{esc(rng["id"])}" value="{st}" autocomplete="off"></label>
      <label>command <input type="text" data-draft-field="command" data-range-id="{esc(rng["id"])}" value="{cmd}" autocomplete="off"></label>
      <label>cwd <input type="text" data-draft-field="cwd" data-range-id="{esc(rng["id"])}" value="{cwd_v}" autocomplete="off"></label>
    </div>
  </div>
  <div class="pr-card-edit-bar">
    <span class="pr-badge-modified" data-card-modified data-edit-show="editing"{modified_vis}>Modified</span>
    <button type="button" class="pr-btn" data-pr-action="card-save" data-edit-show="editing" data-range-id="{esc(rng["id"])}">Save</button>
    <button type="button" class="pr-btn" data-pr-action="card-revert" data-edit-show="editing" data-range-id="{esc(rng["id"])}">Revert</button>
  </div>
  <div class="pr-range-actions">
    <button type="button" class="pr-btn" data-pr-action="start" data-project="{esc(project)}" data-range-id="{esc(rng["id"])}" {start_dis}>Start</button>
    <button type="button" class="pr-btn" data-pr-action="stop" data-project="{esc(project)}" data-range-id="{esc(rng["id"])}" {stop_dis}>Stop</button>
    <button type="button" class="pr-btn pr-btn-danger" data-pr-action="release" data-project="{esc(project)}" data-range-id="{esc(rng["id"])}" {release_dis}>Release</button>
  </div>
  <div class="pr-range-toggles">
    <label class="pr-switch" title="Default on or off for Start Default Services">
      <span class="pr-switch-label">Default</span>
      <input type="checkbox" role="switch" aria-checked="{'true' if default_on else 'false'}" {default_checked}
        data-pr-switch="set-default" data-pr-action="set-default"
        data-project="{esc(project)}" data-range-id="{esc(rng["id"])}"
        data-state-on="on" data-state-off="off">
      <span class="pr-switch-track" aria-hidden="true"><span class="pr-switch-thumb"></span></span>
    </label>
    <label class="pr-switch" title="Tailscale Serve on this range (Funnel stays CLI-only)">
      <span class="pr-switch-label">Tailscale Serve</span>
      <input type="checkbox" role="switch" aria-checked="{'true' if serve_on else 'false'}" {serve_checked}
        data-pr-switch="set-tailnet" data-pr-action="set-tailnet"
        data-project="{esc(project)}" data-range-id="{esc(rng["id"])}"
        data-mode-on="serve" data-mode-off="none">
      <span class="pr-switch-track" aria-hidden="true"><span class="pr-switch-thumb"></span></span>
    </label>
  </div>
</div>"""


def project_html(project: dict) -> str:
    ranges = "".join(range_html(project["project"], r) for r in project["ranges"])
    n = len(project.get("ranges") or [])
    label = esc(project["projectLabel"])
    full = esc(project["project"])
    return (
        f'<details class="pr-project pr-disclose" data-project="{full}">'
        f'<summary class="pr-project-name" title="{full}">'
        f'{label} <span class="tag">{n}</span>'
        f'<span class="pr-disclose-hint" aria-hidden="true">Show</span>'
        f"</summary>"
        f'<div class="pr-ranges">{ranges}</div>'
        "</details>"
    )


def console_css() -> str:
    return (
        ":root{--paper:#f3f4f1;--panel:#fff;--ink:#15181d;--muted:#5d6572;--line:#dde0da;"
        "--cobalt:#2743d6;--green:#188a5e;--amber:#b97303;--red:#bf3b3b;--violet:#6c46c8}"
        "*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);"
        "font:14px/1.5 system-ui,sans-serif}.app{display:flex;min-height:100vh}"
        ".sidebar{width:212px;background:var(--ink);color:#c9cdd6}"
        ".brand{padding:22px 20px;border-bottom:1px solid rgba(255,255,255,.1)}"
        ".brand h1{margin:0;color:white;letter-spacing:.14em}"
        ".tag,.mono{font-family:ui-monospace,Menlo,monospace}.tag{font-size:11px;color:#7e8694}"
        ".nav{display:grid;gap:4px;padding:14px 10px}"
        ".nav a{color:#c9cdd6;text-decoration:none;padding:8px 10px;border-radius:7px}"
        ".nav a.active{background:var(--cobalt);color:white}"
        ".main{flex:1;min-width:0;overflow-x:hidden}""body{overflow-x:hidden}"".content,.pr-panel,.pr-range,.pr-mcp-tool,.pr-mcp-composer,.pr-settings-row,.pr-topbar-right,.pr-defaults-item{min-width:0}"
        ".topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:14px 28px;border-bottom:1px solid var(--line);background:#fafbf9;position:sticky;top:0;z-index:20}"
        ".pr-topbar-right{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-left:auto}"
        ".pr-topbar-right .pr-btn{flex:0 0 auto;padding:6px 10px;font-size:12px}"
        ".pr-bind-chip{flex:0 0 auto;font-size:11px;font-weight:700;letter-spacing:.02em;"
        "color:#5a3a00;background:#fde7c2;border:1px solid #e0b15a;border-radius:999px;"
        "padding:3px 10px;font-family:ui-monospace,Menlo,monospace}"
        ".pr-bind-banner{margin:0 28px 12px;padding:10px 14px;border-radius:8px;"
        "background:#fde7c2;border:1px solid #e0b15a;color:#5a3a00;font-size:13px;font-weight:600}"
        ".live{display:flex;gap:7px;align-items:center;color:var(--muted);"
        "font-family:ui-monospace,Menlo,monospace;font-size:11px}"
        ".live i{width:7px;height:7px;border-radius:50%;background:var(--green)}"
        ".content{padding:26px 28px;max-width:1180px;margin:auto}"
        ".stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:20px}"
        ".stat,.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px}"
        ".stat{padding:13px 16px}.stat span{display:block;color:var(--muted);font-size:11px;"
        "text-transform:uppercase}.stat b{font-size:24px}"
        ".view-head{margin:0 0 16px}.view-head h2{margin:0;font-size:27px}"
        ".eyebrow{margin:0;color:var(--cobalt);font-size:11px;text-transform:uppercase;"
        "letter-spacing:.14em}"
        ".badge{font-size:11px;border-radius:999px;padding:2px 8px}"
        ".b-exec{background:#e2f2eb;color:var(--green)}"
        ".b-onb{background:#eee8fa;color:var(--violet)}"
        ".b-block{background:#f8eeda;color:var(--amber)}"
        ".b-term{background:#f8e6e6;color:var(--red)}"
        ".empty{padding:18px;color:var(--muted)}"
        ".pr-panel{padding:18px}.pr-project{margin-bottom:18px;border:1px solid var(--line);border-radius:8px;background:var(--panel);overflow:hidden}"
        ".pr-defaults{margin:0 0 16px}"
        ".pr-mcp{margin:0 0 16px}"
        ".pr-mcp-head{display:flex;align-items:baseline;gap:10px;margin:0 0 8px}"
        ".pr-mcp-head h3{margin:0;font-size:15px}"
        ".pr-mcp-meta{margin:0 0 12px;color:var(--muted);font-size:12px}"
        ".pr-mcp-list{list-style:none;margin:0;padding:0;display:grid;gap:6px;max-height:280px;overflow:auto}"
        ".pr-mcp-section{margin:14px 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}"
        ".pr-mcp-user{border-color:#c9b8f0;background:#f7f3ff}"
        ".pr-mcp-composer{margin-top:12px;padding:10px;border:1px dashed #c9b8f0;border-radius:8px;background:#fbf9ff;display:grid;gap:8px}"
        ".pr-mcp-composer h4{margin:0;font-size:13px}"
        ".pr-mcp-composer-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}"
        ".pr-mcp-composer input[type=text],.pr-mcp-composer select,.pr-mcp-composer input[type=number],.pr-mcp-composer textarea{font-size:12px;padding:4px 6px;border:1px solid var(--line);border-radius:6px}"
        ".pr-mcp-composer-steps{font-size:12px;color:var(--muted);display:grid;gap:8px}"
        ".pr-mcp-uc-summary{font-size:12px;color:#4a3f6b;background:#efe8ff;border:1px solid #d4c4f5;border-radius:6px;padding:6px 8px;line-height:1.4}"
        ".pr-mcp-uc-summary:empty{display:none}"
        ".pr-mcp-uc-group{display:grid;gap:6px;padding:6px;border-radius:8px;border:1px solid transparent}"
        ".pr-mcp-uc-group.is-parallel{background:#eef6ff;border-color:#b7d4f5}"
        ".pr-mcp-uc-group-label{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#3a6ea5;font-weight:600}"
        ".pr-mcp-uc-card{border:1px solid #d8ccee;border-radius:8px;background:#fff;padding:8px;display:grid;gap:6px}"
        ".pr-mcp-uc-card-head{display:flex;flex-wrap:wrap;gap:6px;align-items:center}"
        ".pr-mcp-uc-num{font-size:11px;font-weight:700;color:var(--muted);min-width:1.5rem}"
        ".pr-mcp-uc-card-actions{display:flex;flex-wrap:wrap;gap:4px;margin-left:auto}"
        ".pr-mcp-uc-card-actions .pr-btn{padding:2px 7px;font-size:11px}"
        ".pr-mcp-uc-args{display:grid;gap:4px;padding:6px;background:#fafbf9;border:1px solid var(--line);border-radius:6px}"
        ".pr-mcp-uc-args-row{display:grid;grid-template-columns:minmax(6rem,9rem) 1fr;gap:6px;align-items:center}"
        ".pr-mcp-uc-args-row label{font-size:11px;color:var(--muted)}"
        ".pr-mcp-uc-args-row label .req{color:var(--red)}"
        ".pr-mcp-uc-args-empty{font-size:11px;color:var(--muted);font-style:italic}"
        ".pr-mcp-uc-json{width:100%;min-height:4.5rem;font-family:ui-monospace,Menlo,monospace;font-size:11px}"
        ".pr-mcp-uc-dry{margin-top:4px;padding:8px;border:1px solid #cde3d4;border-radius:6px;background:#f3faf5;font-size:12px;display:none;white-space:pre-wrap}"
        ".pr-mcp-uc-dry.is-open{display:block}"
        ".pr-mcp-uc-dry.is-err{border-color:#e8bcbc;background:#fff5f5}"
        ".pr-mcp-tool-actions{display:flex;flex-wrap:wrap;gap:4px;justify-self:end}"
        ".pr-mcp-tool{grid-template-columns:minmax(8rem,12rem) 1fr auto auto}"
        ".pr-ps-serve-chip{font-size:11px;border-radius:999px;padding:2px 8px;background:#eee8fa;color:var(--violet);max-width:42ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".pr-ps-serve-chip.is-on{background:#e2f2eb;color:var(--green)}"
        ".pr-ps-serve-chip.is-warn{background:#f8eeda;color:var(--amber)}"
        ".pr-ps-serve-chip.is-missing{background:#f8e6e6;color:var(--red)}"
        ".pr-topbar-right .pr-switch-label{min-width:0;font-size:11px}"
        ".pr-mcp-tool{display:grid;grid-template-columns:minmax(8rem,12rem) 1fr auto;gap:8px 12px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:#fafbf9;align-items:center}"
        ".pr-mcp-tool.is-disabled{opacity:.55;background:#f0f1ef}"
        ".pr-mcp-name{font-size:12px;font-weight:600}"
        ".pr-mcp-desc{font-size:12px;color:var(--muted)}"
        ".pr-mcp-toggle{justify-self:end;white-space:nowrap}"
        ".pr-mcp-toggle .pr-switch-label{font-size:11px;color:var(--muted)}"
        ".pr-defaults-head{display:flex;align-items:baseline;gap:10px;margin:0 0 10px}"
        ".pr-defaults-head h3{margin:0;font-size:15px}"
        ".pr-defaults-list{list-style:none;margin:0;padding:0;display:grid;gap:6px}"
        ".pr-defaults-item{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:7px;background:#fafbf9}"
        ".pr-defaults-note{font-weight:600}"
        ".pr-defaults-proj{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:28ch}"
        ".pr-defaults-jump{margin-left:auto;font-size:12px;color:var(--cobalt);text-decoration:none}"
        ".pr-defaults-jump:hover{text-decoration:underline}"
        ".pr-project:last-child{margin-bottom:0}"
        ".pr-project>summary.pr-project-name{margin:0;color:var(--muted);text-transform:uppercase;"
        "letter-spacing:.08em;font-size:11px;cursor:pointer;list-style:none;"
        "display:flex;align-items:center;gap:8px;padding:10px 12px;user-select:none;font-weight:600}"
        ".pr-project>summary.pr-project-name::-webkit-details-marker{display:none}"
        ".pr-project[open]>summary.pr-project-name{border-bottom:1px solid var(--line)}"
        ".pr-project .pr-ranges{padding:12px}"
        ".pr-project>summary .pr-disclose-hint{margin-left:auto;font-size:11px;font-weight:500;"
        "text-transform:none;letter-spacing:0;color:var(--cobalt);opacity:.85}"
        ".pr-project[open]>summary .pr-disclose-hint{font-size:0}"
        ".pr-project>summary .pr-disclose-hint::after{content:\" services\"}"
        ".pr-project[open]>summary .pr-disclose-hint::after{content:\"Hide\";font-size:11px;opacity:.55}"
        ".pr-ranges{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}"
        ".pr-range{position:relative;border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fafbf9}"
        ".pr-card-pencil{position:absolute;top:8px;right:8px;z-index:2;border:1px solid var(--line);"
        "background:#fff;border-radius:6px;width:28px;height:28px;padding:0;cursor:pointer;"
        "color:var(--muted);font-size:14px;line-height:1;display:inline-flex;align-items:center;justify-content:center}"
        ".pr-card-pencil:hover{border-color:var(--cobalt);color:var(--cobalt)}"
        ".pr-range-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding-right:32px}"
        ".pr-port-link{color:var(--cobalt);text-decoration:none;font-weight:600}"
        ".pr-port-link:hover{text-decoration:underline}"
        ".pr-remote-soon{opacity:0.55;pointer-events:none;user-select:none}"
        ".pr-remote-soon-body{padding:8px 0;color:#7e8694;font-size:12px;font-style:italic}"
        ".pr-host{font-family:ui-monospace,Menlo,monospace;font-size:11px}"
        ".pr-machines{margin-top:12px}"
        ".pr-machine-row{display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--line);font-size:12px}"
        ".pr-machine-row:first-of-type{border-top:none}"
        ".pr-range-meta{margin-top:8px;color:var(--muted);font-size:12px}"
        ".pr-range-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;min-width:0}"
        ".pr-btn{flex:1;border:1px solid var(--line);background:white;border-radius:6px;"
        "padding:6px 8px;font-size:12px;cursor:pointer;color:var(--ink)}"
        ".pr-btn:hover:not(:disabled){border-color:var(--cobalt);color:var(--cobalt)}"
        ".pr-btn:disabled{opacity:.4;cursor:not-allowed}"
        ".pr-btn-danger:hover:not(:disabled){border-color:var(--red);color:var(--red)}"
        ".pr-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 16px}"
        ".pr-toolbar .pr-btn{flex:0 0 auto;padding:8px 12px}"
        ".pr-toolbar label{font-size:12px;color:var(--muted)}"
        ".pr-toolbar input[type=text],.pr-toolbar input[type=file]{font-size:12px}"
        ".pr-subpanel{margin-top:16px;padding:14px 16px;border:1px solid var(--line);"
        "border-radius:8px;background:#fafbf9}"
        ".pr-subpanel h3{margin:0 0 10px;font-size:13px;text-transform:uppercase;"
        "letter-spacing:.08em;color:var(--muted)}"
        ".pr-preset-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;"
        "padding:8px 0;border-top:1px solid var(--line)}"
        ".pr-preset-row:first-of-type{border-top:none}"
        ".pr-preset-name{font-family:ui-monospace,Menlo,monospace;font-weight:600}"
        ".pr-preset-meta{color:var(--muted);font-size:12px;flex:1}"
        ".pr-settings-row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.badge.b-warn{background:#f59e0b;color:#111;font-weight:600}.pr-warn-banner{background:#fef3c7;border:1px solid #f59e0b;color:#78350f;padding:8px 12px;border-radius:6px;margin:8px 0;font-size:13px}"
        ".pr-range-toggles{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;align-items:center}"
        ".pr-switch{display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none;"
        "font-size:12px;color:var(--ink);position:relative}"
        ".pr-switch input{position:absolute;opacity:0;width:1px;height:1px;margin:0}"
        ".pr-switch-track{width:36px;height:20px;border-radius:999px;background:#c5cad3;"
        "position:relative;transition:background .15s;flex-shrink:0;display:inline-block}"
        ".pr-switch-thumb{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;"
        "background:#fff;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.25)}"
        ".pr-switch input:checked+.pr-switch-track{background:var(--cobalt)}"
        ".pr-switch input:checked+.pr-switch-track .pr-switch-thumb{transform:translateX(16px)}"
        ".pr-switch input:focus-visible+.pr-switch-track{outline:2px solid var(--cobalt);outline-offset:2px}"
        ".pr-switch-label{min-width:4.5rem}"
        ".pr-env-rail{display:flex;flex-direction:column;gap:4px;padding:10px;border-top:1px solid rgba(255,255,255,.1);"
        "flex:1;min-height:0}"
        ".pr-env-rail-title{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#7e8694;"
        "padding:4px 8px 8px}"
        ".pr-env-tab{display:flex;align-items:center;gap:6px;border-radius:7px;padding:8px 10px;"
        "color:#c9cdd6;text-decoration:none;border:0;background:transparent;width:100%;text-align:left;"
        "cursor:pointer;font:inherit}"
        ".pr-env-tab:hover{background:rgba(255,255,255,.06)}"
        ".pr-env-tab.active{background:var(--cobalt);color:white}"
        ".pr-env-tab-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".pr-env-tab-close{border:0;background:transparent;color:inherit;opacity:.7;cursor:pointer;"
        "font-size:14px;line-height:1;padding:0 2px}"
        ".pr-env-tab-close:hover{opacity:1}"
        ".pr-env-new{margin-top:auto;width:100%;flex:0 0 auto;align-self:stretch;background:transparent;color:#c9cdd6;border:1px solid rgba(255,255,255,.18);border-radius:7px;padding:8px 10px;text-align:left;cursor:pointer;font:inherit}.pr-env-new:hover{background:rgba(255,255,255,.06);color:#fff;border-color:rgba(255,255,255,.28)}"
        ".pr-ts-chip{font-size:11px;border-radius:999px;padding:3px 10px;"
        "font-family:ui-monospace,Menlo,monospace}"
        ".pr-ts-connected{background:#e2f2eb;color:var(--green)}"
        ".pr-ts-needs{background:#f8eeda;color:var(--amber)}"
        ".pr-ts-missing{background:#f8e6e6;color:var(--red)}"
        ".pr-ts-pending{background:#eef0f3;color:var(--muted)}"
        ".pr-empty-env{padding:28px 18px;text-align:center;color:var(--muted)}"
        ".pr-empty-env h3{margin:0 0 8px;color:var(--ink)}"
        ".sidebar{display:flex;flex-direction:column}"
        ".pr-topbar-brand{display:none;font-weight:800;letter-spacing:.12em;color:var(--ink);font-size:13px;margin-right:8px}"
        ".pr-topbar-left{display:flex;align-items:center;gap:10px;min-width:0;flex:1}"
        ".pr-topbar-left .crumb{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".pr-compose-jump{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line);background:#fff;border-radius:6px;padding:6px 10px;font-size:12px;color:var(--cobalt);text-decoration:none;white-space:nowrap}"
        ".pr-compose-jump:hover{border-color:var(--cobalt)}"
        ".pr-mcp-jump-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 10px}"
        ".pr-ps-serve-wrap{display:inline-flex;flex-wrap:wrap;align-items:center;gap:6px;min-width:0;max-width:100%}"
        ".pr-ps-serve-chip{max-width:min(42ch,100%)}"
        ".pr-serve-copy,.pr-serve-url-details{flex:0 0 auto}"
        ".pr-serve-url-details{font-size:11px}"
        ".pr-serve-url-details summary{cursor:pointer;color:var(--cobalt);list-style:none;display:inline-flex;align-items:center;gap:6px}"
        ".pr-serve-url-details summary::-webkit-details-marker{display:none}"
        ".pr-serve-url-details code{display:block;margin-top:4px;padding:6px 8px;background:#fafbf9;border:1px solid var(--line);border-radius:6px;word-break:break-all;white-space:pre-wrap;max-width:min(42ch,100%)}"
        ".pr-mcp-system-details{margin:8px 0 0;border:1px solid var(--line);border-radius:8px;background:#fafbf9;padding:0}"
        ".pr-mcp-system-details>summary{cursor:pointer;padding:10px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600;list-style:none;display:flex;align-items:center;gap:8px;user-select:none}"
        ".pr-mcp-system-details>summary::-webkit-details-marker{display:none}"
        ".pr-mcp-system-details[open]>summary{border-bottom:1px solid var(--line)}"
        ".pr-disclose>summary,.pr-mcp-system-details>summary,.pr-serve-url-details>summary,.pr-subpanel>summary,.pr-history>summary{position:relative}"
        ".pr-disclose>summary::before,.pr-mcp-system-details>summary::before,.pr-serve-url-details>summary::before,.pr-subpanel>summary::before,.pr-history>summary::before{content:\"\";display:inline-block;width:0;height:0;flex:0 0 auto;border-top:5px solid transparent;border-bottom:5px solid transparent;border-left:7px solid var(--cobalt);margin-right:2px;transition:transform .15s ease;transform-origin:4px 50%}"
        ".pr-disclose[open]>summary::before,.pr-mcp-system-details[open]>summary::before,.pr-serve-url-details[open]>summary::before,.pr-subpanel[open]>summary::before,.pr-history[open]>summary::before{transform:rotate(90deg)}"
        ".pr-mcp-system-details>summary .pr-disclose-hint{margin-left:auto;font-size:11px;font-weight:500;text-transform:none;letter-spacing:0;color:var(--cobalt);opacity:.85}"
        ".pr-mcp-system-details[open]>summary .pr-disclose-hint{opacity:.55}"
        ".pr-mcp-system-details>summary .pr-disclose-hint::after{content:\" tools\"}"
        ".pr-mcp-system-details[open]>summary .pr-disclose-hint{font-size:0}"
        ".pr-mcp-system-details[open]>summary .pr-disclose-hint::after{content:\"Hide\";font-size:11px;opacity:.55}"
        ".pr-mcp-system-details .pr-mcp-list{padding:8px;max-height:none}"
        ".pr-handoff-details>summary .pr-disclose-hint::after{content:\"\"}"
        ".pr-handoff-body{padding:12px;display:flex;flex-direction:column;gap:10px;font-size:13px}"
        ".pr-handoff-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}"
        ".pr-handoff-matrix{width:100%;border-collapse:collapse;font-size:12px}"
        ".pr-handoff-matrix th,.pr-handoff-matrix td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}"
        ".pr-handoff-help{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;background:#fafbf9;border:1px solid var(--line);border-radius:6px;padding:8px 10px;overflow:auto;max-height:220px}"
        ".pr-handoff-kit{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;word-break:break-all}"
        ".pr-handoff-err{color:#b42318}"
        ".pr-handoff-manage{display:flex;flex-direction:column;align-items:flex-start;gap:6px}"
        ".pr-handoff-honesty{color:#5c5c5c;font-size:11px;line-height:1.35}"
        ".pr-mcp-composer input[type=text],.pr-mcp-composer select{min-width:0;flex:1 1 10rem;max-width:100%}"
        ".pr-mcp-uc-name,.pr-mcp-uc-desc{min-width:0!important}"
        "@media(max-width:900px){"
        ".app{display:block}"
        ".sidebar{display:none!important}"
        ".pr-topbar-brand{display:inline-block}"
        ".topbar{padding:10px 16px;gap:10px;flex-wrap:wrap;align-items:flex-start}"
        ".pr-topbar-right{width:100%;margin-left:0;gap:8px}"
        ".pr-topbar-right .pr-switch-label{font-size:0;line-height:0}"
        ".pr-topbar-right .pr-switch-label::after{content:'Serve';font-size:12px;line-height:1.4;color:var(--ink)}"
        ".pr-ts-chip{font-size:10px}"
        "#pr-ts-login-slot,#pr-ts-logout-slot{display:none}"
        ".pr-ps-serve-chip .pr-serve-url-text{display:none}"
        ".pr-ps-serve-chip.is-on::before{content:'Serving'}"
        ".content{padding:18px 16px}"
        ".stats{grid-template-columns:repeat(3,minmax(0,1fr))}"
        ".pr-mcp-tool{grid-template-columns:1fr;align-items:start}"
        ".pr-mcp-toggle{justify-self:start}"
        ".pr-mcp-tool-actions{justify-self:start}"
        ".pr-range-actions{flex-direction:column}"
        ".pr-range-actions .pr-btn{width:100%;flex:1 1 auto}"
        ".pr-settings-row{flex-direction:column;align-items:stretch}"
        ".pr-settings-row .pr-btn{width:100%}"
        ".pr-ranges{grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr))}"
        ".pr-mcp-uc-args-row{grid-template-columns:1fr}"
        "}"
        "@media(max-width:480px){"
        ".content{padding:16px}"
        ".stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}"
        ".stats .stat:nth-child(5){grid-column:1/-1}"
        ".view-head h2{font-size:22px}"
        ".topbar{padding:8px 12px}"
        ".pr-btn,.pr-defaults-jump,.pr-compose-jump,.pr-serve-copy,"
        ".pr-topbar-right .pr-btn,.pr-mcp-composer .pr-btn,"
        ".pr-range-actions .pr-btn,.pr-settings-row .pr-btn,"
        ".pr-card-edit-bar .pr-btn,.pr-mcp-tool-actions .pr-btn{"
        "min-height:44px;min-width:44px;padding:10px 12px;font-size:13px}"
        ".pr-defaults-jump{display:inline-flex;align-items:center;justify-content:center;padding:10px 12px;border:1px solid var(--line);border-radius:6px;margin-left:0}"
        ".pr-card-pencil{width:44px;height:44px;top:6px;right:6px}"
        ".pr-switch{min-height:44px;padding:6px 0}"
        ".pr-switch-track{width:44px;height:26px}"
        ".pr-switch-thumb{width:22px;height:22px;top:2px;left:2px}"
        ".pr-switch input:checked+.pr-switch-track .pr-switch-thumb{transform:translateX(18px)}"
        ".pr-ps-serve-chip{min-height:44px;display:inline-flex;align-items:center;padding:8px 12px;max-width:100%;font-size:12px}"
        ".pr-serve-url-details summary{min-height:44px;display:inline-flex;align-items:center;padding:0 8px}"
        ".pr-mcp-composer-row{align-items:stretch}"
        ".pr-mcp-composer input[type=text],.pr-mcp-composer select,.pr-mcp-composer input[type=number],.pr-mcp-composer textarea{"
        "font-size:16px;padding:10px 12px;width:100%;flex:1 1 100%}"
        ".pr-mcp-composer-row .pr-btn{flex:1 1 auto}"
        ".pr-ts-chip{min-height:44px;display:inline-flex;align-items:center}"
        "}"
        ".pr-hist-modified{display:inline-flex;align-items:center;gap:8px;margin-left:10px;vertical-align:middle}.pr-badge-modified{background:#b97303;color:#fff;font-weight:700;font-size:11px;letter-spacing:.04em;text-transform:uppercase;padding:3px 8px;border-radius:999px}.pr-history{margin:12px 0 18px;border:1px solid var(--line);border-radius:8px;background:var(--panel);overflow:hidden}.pr-history summary{cursor:pointer;padding:10px 14px;font-weight:600;list-style:none;display:flex;align-items:center;justify-content:space-between;user-select:none}.pr-history summary::-webkit-details-marker{display:none}.pr-history-body{border-top:1px solid var(--line);max-height:240px;overflow:auto;padding:6px}.pr-history-item{display:flex;align-items:center;gap:8px;width:100%;text-align:left;border:0;background:transparent;color:inherit;padding:8px 10px;border-radius:6px;cursor:pointer;font:inherit}.pr-history-item:hover{background:rgba(39,67,214,.08)}.pr-history-item.active{background:rgba(39,67,214,.14);font-weight:600}.pr-history-item .pr-hist-idx{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--muted);min-width:1.5em}.pr-history-item .pr-hist-at{margin-left:auto;font-size:11px;color:var(--muted)}"
        ".pr-env-soon{padding:14px 16px;border-top:1px solid rgba(255,255,255,.08)}"
        ".pr-env-soon-title{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#c9cdd6;font-weight:700;margin:0 0 8px}"
        ".pr-env-soon-body{color:#7e8694;font-size:13px;font-style:italic}"
        ".pr-sidebar-btn{display:block;width:100%;margin-top:8px;border:1px solid rgba(255,255,255,.18);"
        "background:transparent;color:#c9cdd6;border-radius:6px;padding:8px 10px;font-size:12px;cursor:pointer}"
        ".pr-sidebar-btn:hover{border-color:#8ea0ff;color:#fff}"
        ".pr-range-lifecycle{margin-top:8px;font-size:11px;color:var(--muted)}"
        ".pr-life-edit{display:grid;gap:6px;margin-top:8px}"
        ".pr-life-edit label{display:grid;gap:2px;font-size:11px}"
        ".pr-life-edit input{font:12px ui-monospace,Menlo,monospace;padding:4px 6px;border:1px solid var(--line);border-radius:4px}"
        ".pr-range.pr-editing{border-color:var(--cobalt);box-shadow:0 0 0 1px rgba(39,67,214,.25)}"
        ".pr-range.pr-editing [data-life-view]{display:none}"
        ".pr-range.pr-editing [data-life-edit]{display:grid}"
        ".pr-range:not(.pr-editing) [data-life-edit]{display:none}"
        ".pr-range.pr-editing [data-edit-show=idle]{display:none!important}"
        ".pr-range:not(.pr-editing) [data-edit-show=editing]{display:none!important}"
        ".pr-range:not(.pr-editing) .pr-card-edit-bar{display:none}"
        ".pr-card-edit-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}"
        ".pr-card-edit-bar .pr-btn{flex:0 0 auto}"
        ".pr-card-edit-bar .pr-badge-modified{display:inline-flex}"
        ".pr-card-edit-bar .pr-badge-modified[hidden]{display:none!important}"
        ".pr-workspace-panel{position:fixed;right:18px;top:72px;z-index:40;width:min(380px,calc(100vw - 36px));"
        "max-height:calc(100vh - 96px);overflow:auto;background:var(--panel);color:var(--ink);"
        "border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.18);padding:0}"
        ".pr-workspace-panel[hidden]{display:none!important}"
        ".pr-wsp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;"
        "padding:14px 14px 10px;border-bottom:1px solid var(--line)}"
        ".pr-wsp-head h3{margin:0;font-size:14px;line-height:1.35}"
        ".pr-wsp-close{border:0;background:transparent;color:var(--muted);font-size:20px;line-height:1;"
        "cursor:pointer;padding:0 4px}"
        ".pr-wsp-close:hover{color:var(--ink)}"
        ".pr-wsp-body{display:flex;flex-direction:column;gap:8px;padding:14px}"
        ".pr-wsp-body .pr-btn{flex:0 0 auto;width:100%;text-align:left;padding:8px 12px}"
        ".pr-wsp-body label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}"
        ".pr-wsp-body input[type=text],.pr-wsp-body input[type=file]{font-size:12px;width:100%}"
        ".pr-discover-overlay{position:fixed;inset:0;z-index:60;background:rgba(10,12,16,.55);display:flex;align-items:center;justify-content:center;padding:18px}"
        ".pr-discover-overlay[hidden]{display:none!important}"
        ".pr-discover-modal{width:min(640px,100%);max-height:min(80vh,720px);overflow:auto;background:#1c1f26;color:#e8eaed;border:1px solid #3a3f4a;border-radius:12px;box-shadow:0 18px 48px rgba(0,0,0,.45)}"
        ".pr-discover-modal h3{margin:0;font-size:15px}"
        ".pr-discover-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding:14px 16px;border-bottom:1px solid #3a3f4a}"
        ".pr-discover-body{padding:12px 16px;display:flex;flex-direction:column;gap:8px}"
        ".pr-discover-row{display:grid;grid-template-columns:auto 4.5rem 1fr auto;gap:8px 10px;align-items:start;padding:8px 10px;border:1px solid #3a3f4a;border-radius:8px;background:#15181e}"
        ".pr-discover-row.is-claimed{opacity:.55}"
        ".pr-discover-row .pr-disc-port{font-weight:700;font-variant-numeric:tabular-nums}"
        ".pr-discover-row .pr-disc-meta{font-size:12px;color:#9aa3b2;word-break:break-word}"
        ".pr-discover-row .pr-disc-badge{font-size:10px;text-transform:uppercase;letter-spacing:.06em;padding:2px 6px;border-radius:999px;border:1px solid #3a3f4a;color:#9aa3b2;white-space:nowrap}"
        ".pr-discover-row .pr-disc-badge.serve{color:#7dcea0;border-color:#2f6b4f}"
        ".pr-discover-foot{display:flex;flex-wrap:wrap;gap:8px;padding:12px 16px;border-top:1px solid #3a3f4a}"
        ".pr-discover-empty{font-size:13px;color:#9aa3b2;padding:8px 0}"
        ".pr-wsp-section{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);"
        "margin:6px 0 0}"
        "@media(prefers-color-scheme:dark){"
        ".pr-workspace-panel{background:#1c1f26;color:#e8eaed;border-color:#3a3f4a;"
        "box-shadow:0 12px 40px rgba(0,0,0,.45)}"
        ".pr-wsp-head{border-color:#3a3f4a}"
        ".pr-card-pencil{background:#1c1f26;border-color:#3a3f4a;color:#aeb4c0}"
        ".pr-card-pencil:hover{border-color:#8ea0ff;color:#8ea0ff}"
        "}"
        ".pr-compat-locked{opacity:.85}"
        ".pr-subpanel>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px}"
        ".pr-subpanel>summary::-webkit-details-marker{display:none}"
        ".pr-presets-soon .pr-preset-row button,.pr-presets-soon .pr-toolbar{display:none}"
    )


def presets_panel_html(view: dict) -> str:
    """Named presets stay CLI/MCP-only. No friend-facing Coming soon chrome."""
    return ""


def settings_panel_html(view: dict) -> str:
    settings = view.get("settings") or {}
    checked = "checked" if settings.get("autoApplyOnLaunch") else ""
    exit_checked = "checked" if settings.get("autoExitOnShutdown") else ""
    current = settings.get("autoApplyPreset") or ""
    options = ['<option value="">(none)</option>']
    for p in view.get("presets") or []:
        name = p["name"]
        sel = " selected" if name == current else ""
        options.append(f'<option value="{esc(name)}"{sel}>{esc(name)}</option>')
    # Remotes remain HOLD — do not render a Coming soon stub.
    return (
        '<div class="pr-subpanel" id="pr-settings">'
        "<h3>Settings</h3>"
        '<div class="pr-settings-row">'
        f'<label><input type="checkbox" id="pr-auto-apply-launch" {checked}> Auto-apply on launch</label>'
        f'<label>Preset <select id="pr-auto-apply-preset">{"".join(options)}</select></label>'
        f'<label><input type="checkbox" id="pr-auto-exit-shutdown" {exit_checked}> Auto-deactivate on shutdown</label>'
        '<label class="pr-compat-locked" title="Always on">'
        '<input type="checkbox" id="pr-require-compat" checked disabled> '
        "Require compatibility — on</label>"
        '<button type="button" class="pr-btn" data-pr-action="settings-save">Save settings</button>'
        "</div>"
        "</div>"
    )



def probe_tailscale_for_ui() -> dict:
    """Best-effort Tailscale chip for the toolbar (Connected / Needs login / Binary missing)."""
    code, payload, _stdout = run_cli(["tailscale", "status"])
    if isinstance(payload, dict):
        chip = payload.get("chip") or ("Connected" if payload.get("logged_in") else "Needs login")
        state = payload.get("state") or ("connected" if payload.get("logged_in") else "needs_login")
        return {
            "chip": chip,
            "state": state,
            "logged_in": bool(payload.get("logged_in")),
            "BackendState": payload.get("BackendState"),
            "AuthURL": payload.get("AuthURL"),
            "message": payload.get("message"),
            "binary": payload.get("binary"),
        }
    if code == 2:
        return {"chip": "Binary missing", "state": "binary_missing", "logged_in": False}
    return {"chip": "Needs login", "state": "needs_login", "logged_in": False}


def api_tailscale_status_payload() -> dict:
    """Run Tailscale probe once; chip + Self + port_urls for serve/funnel ranges."""
    try:
        status = probe_tailscale_status()
    except Exception as exc:
        status = {
            "ok": False,
            "chip": "Needs login",
            "state": "needs_login",
            "logged_in": False,
            "message": str(exc),
            "AuthURL": None,
            "Self": None,
        }
    if not isinstance(status, dict):
        status = {
            "ok": False,
            "chip": "Needs login",
            "state": "needs_login",
            "logged_in": False,
            "AuthURL": None,
            "Self": None,
        }
    ts_self = status.get("Self")
    raw = load_registry()
    port_urls: dict = {}
    for _project, entry in (raw.get("projects") or {}).items():
        for item in (entry or {}).get("ranges") or []:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            if not rid:
                continue
            tailnet = item.get("tailnet") if isinstance(item.get("tailnet"), dict) else {}
            mode = tailnet.get("mode") or "none"
            if mode not in ("serve", "funnel"):
                continue
            url = resolve_range_url(
                raw,
                item,
                port=item.get("start"),
                tailscale_self=ts_self,
                allow_probe=False,
            )
            if url:
                port_urls[str(rid)] = url
    out = dict(status)
    out.setdefault("chip", "Needs login")
    out.setdefault("state", "needs_login")
    out.setdefault("logged_in", False)
    # Endpoint always returns 200 with a chip payload; ok reflects probe success-ish.
    out["ok"] = out.get("state") != "binary_missing"
    out["port_urls"] = port_urls
    try:
        out["portskill_serve"] = probe_portskill_serve_status()
    except Exception as exc:  # noqa: BLE001
        out["portskill_serve"] = {
            "ok": False,
            "chip": "Off",
            "state": "off",
            "message": str(exc),
        }
    return out


def range_matches_preset_service(project: str, rng: dict, svc: dict) -> bool:
    if not isinstance(svc, dict):
        return False
    rid = svc.get("range_id")
    if isinstance(rid, str) and rid.strip() and rid.strip() == rng.get("id"):
        return True
    svc_proj = svc.get("project")
    if svc_proj and str(svc_proj) not in project and project != svc_proj:
        # allow suffix / basename match
        if not (project.endswith("/" + str(svc_proj)) or project.endswith(str(svc_proj))):
            return False
    note = svc.get("note")
    if (note or None) == (rng.get("note") or None):
        if not svc_proj or str(svc_proj) in project or project == svc_proj:
            return True
    return False


def filter_view_for_environment(view: dict, raw: dict, focused: str | None) -> dict:
    """Filter projects/ranges to the focused preset's services; empty env ⇒ empty list."""
    if not focused:
        return view
    presets = raw.get("presets") or {}
    entry = presets.get(focused)
    if not isinstance(entry, dict):
        # Unknown focus — show empty with name
        filtered = dict(view)
        filtered["projects"] = []
        filtered["focusedEnvironment"] = focused
        filtered["focusedEmpty"] = True
        return filtered
    services = entry.get("services") if isinstance(entry.get("services"), list) else []
    if not services:
        filtered = dict(view)
        filtered["projects"] = []
        filtered["focusedEnvironment"] = focused
        filtered["focusedEmpty"] = True
        filtered["focusedDescription"] = entry.get("description") or ""
        return filtered
    projects_out = []
    for proj in view.get("projects") or []:
        ranges = []
        for rng in proj.get("ranges") or []:
            if any(range_matches_preset_service(proj["project"], rng, svc) for svc in services):
                ranges.append(rng)
        if ranges:
            projects_out.append({**proj, "ranges": ranges})
    # Drop self from "Also used in …" while viewing that environment
    projects_cleaned = []
    for proj in projects_out:
        ranges_cleaned = []
        for rng in proj.get("ranges") or []:
            also = [n for n in (rng.get("alsoUsedInPresets") or []) if n != focused]
            ranges_cleaned.append({**rng, "alsoUsedInPresets": also})
        projects_cleaned.append({**proj, "ranges": ranges_cleaned})
    filtered = dict(view)
    filtered["projects"] = projects_cleaned
    filtered["focusedEnvironment"] = focused
    filtered["focusedEmpty"] = len(projects_cleaned) == 0
    filtered["focusedDescription"] = entry.get("description") or ""
    return filtered


def env_rail_html(view: dict) -> str:
    """Single implicit workspace — no Workspaces Coming soon rail."""
    return ""


def environment_rail_html(view: dict) -> str:
    """Alias kept for callers that use the longer name."""
    return env_rail_html(view)



def portskill_serve_chip_html(ps: dict | None) -> str:
    ps = ps or {}
    state = ps.get("state") or "off"
    url = ps.get("serve_url") or ""
    chip = ps.get("chip") or "Off"
    if state == "serving" and url:
        label = url
        klass = "pr-ps-serve-chip is-on"
        title = ps.get("message") or f"Serving {url}"
    elif state == "binary_missing":
        label = "Serve: binary missing"
        klass = "pr-ps-serve-chip is-missing"
        title = ps.get("message") or label
    elif state == "needs_login":
        label = "Serve: needs login"
        klass = "pr-ps-serve-chip is-warn"
        title = ps.get("message") or label
    elif state in ("no_listen",):
        label = "Serve: no listen port"
        klass = "pr-ps-serve-chip is-warn"
        title = ps.get("message") or label
    else:
        label = f"Serve: {chip}"
        klass = "pr-ps-serve-chip"
        title = ps.get("message") or label
    short_plain = "Serving" if state == "serving" and url else (
        "missing" if state == "binary_missing" else (
            "needs login" if state == "needs_login" else (
                "no listen" if state == "no_listen" else (chip or "Off")
            )
        )
    )
    inner = esc(label)
    copy_btn = ""
    details = ""
    if state == "serving" and url:
        inner = (
            f'<a class="pr-port-link pr-serve-url-text" href="{esc(url)}" target="_blank" rel="noopener" '
            f'style="color:inherit">{esc(url)}</a>'
        )
        copy_btn = (
            f'<button type="button" class="pr-btn pr-serve-copy" id="pr-serve-copy" '
            f'data-serve-url="{esc(url)}" title="Copy Serve URL" aria-label="Copy Serve URL">Copy URL</button>'
        )
        details = (
            f'<details class="pr-serve-url-details" id="pr-serve-url-details">'
            f'<summary>Serve URL</summary><code id="pr-serve-url-full">{esc(url)}</code></details>'
        )
    return (
        f'<span class="pr-ps-serve-wrap" id="pr-ps-serve-wrap">'
        f'<span class="{klass}" id="pr-ps-serve-chip" title="{esc(title)}" '
        f'data-state="{esc(state)}" data-short="{esc(short_plain)}">{inner}</span>'
        f"{copy_btn}{details}</span>"
    )


def portskill_serve_toggle_html(view: dict, ts: dict, ps: dict | None = None) -> str:
    """Top-bar control: Tailscale Serve current Portskill listen port (never Funnel)."""
    settings = (view or {}).get("settings") or {}
    pref = bool(settings.get("servePortskillOnTailscale"))
    ps = ps or {}
    if ps.get("enabled_preference") is not None:
        pref = bool(ps.get("enabled_preference"))
    disabled = ""
    title = "Tailscale Serve the Portskill UI/MCP listen port from listen.json (private; no Funnel)"
    if (ts or {}).get("state") == "binary_missing":
        disabled = "disabled"
        title = "Install Tailscale to use Serve"
    checked = "checked" if pref else ""
    aria = "true" if pref else "false"
    return (
        f'<label class="pr-switch" title="{esc(title)}">'
        f'<span class="pr-switch-label">Serve Portskill on Tailscale</span>'
        f'<input type="checkbox" role="switch" aria-checked="{aria}" {checked} {disabled}'
        f' data-pr-switch="serve-portskill" data-pr-action="serve-portskill">'
        f'<span class="pr-switch-track" aria-hidden="true"><span class="pr-switch-thumb"></span></span>'
        f"</label>"
        f"{portskill_serve_chip_html(ps)}"
    )


def tailscale_toolbar_html(ts: dict, view: dict | None = None, ps: dict | None = None) -> str:
    chip = ts.get("chip") or "Needs login"
    state = ts.get("state") or "needs_login"
    if state == "connected" or chip == "Connected":
        klass = "pr-ts-connected"
    elif state == "binary_missing" or chip == "Binary missing":
        klass = "pr-ts-missing"
    elif state == "pending" or chip == "Checking…":
        klass = "pr-ts-pending"
    else:
        klass = "pr-ts-needs"
    login_btn = ""
    logout_btn = ""
    if state == "pending":
        login_btn = ""
    elif state == "binary_missing":
        login_btn = (
            '<span class="tag" id="pr-ts-login-hint" style="color:var(--muted)">'
            "Install Tailscale to use Serve</span>"
        )
    elif state == "connected" or chip == "Connected":
        logout_btn = (
            '<button type="button" class="pr-btn" id="pr-ts-logout" data-pr-action="tailscale-logout" '
            'title="Log out of Tailscale">Logout</button>'
        )
    else:
        login_btn = (
            '<button type="button" class="pr-btn" id="pr-ts-login" data-pr-action="tailscale-login" '
            'title="Open Tailscale sign-in in your browser">Browser Login</button>'
        )
        logout_btn = (
            '<button type="button" class="pr-btn" id="pr-ts-logout" data-pr-action="tailscale-logout" '
            'title="Log out of Tailscale">Logout</button>'
        )
    serve_ctrl = portskill_serve_toggle_html(view or {}, ts, ps)
    return (
        f'<span class="pr-ts-chip {klass}" id="pr-ts-chip" title="{esc(ts.get("message") or chip)}">'
        f"Tailscale: {esc(chip)}</span>"
        f'<span id="pr-ts-login-slot">{login_btn}</span>'
        f'<span id="pr-ts-logout-slot">{logout_btn}</span>'
        f"{serve_ctrl}"
    )



def history_panel_html(view: dict) -> str:
    """Hidden for now — avoid two Modified concepts; use draft Save/Revert."""
    return ""



def modified_badge_html(view: dict) -> str:
    """Global page Edit/Modified/Save/Revert removed — per-card controls only."""
    return ""





def dry_run_user_command_steps(steps: list) -> dict:
    """Validate a user-command chain without calling tools (no side effects)."""
    from .mcp import _group_user_command_steps  # type: ignore  # noqa: PLC0415

    system = {
        t.get("name"): t
        for t in TOOL_DEFS
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t.get("name").strip()
    }
    prefs = {}
    try:
        from .mcp import _mcp_tools_prefs  # noqa: PLC0415

        prefs = _mcp_tools_prefs() or {}
    except Exception:  # noqa: BLE001
        prefs = {}

    step_results = []
    any_error = False
    for step in steps or []:
        if not isinstance(step, dict):
            step_results.append(
                {"ok": False, "tool": None, "mode": "series", "message": "step must be an object"}
            )
            any_error = True
            continue
        tool = step.get("tool")
        mode = step.get("mode") or "series"
        if not isinstance(mode, str):
            mode = "series"
        mode = mode.strip().lower()
        if mode not in ("series", "parallel"):
            mode = "series"
        arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        if tool == "stop":
            arguments = {}
        if not isinstance(tool, str) or tool not in system:
            step_results.append(
                {
                    "ok": False,
                    "tool": tool,
                    "mode": mode,
                    "message": f"unknown or non-system tool: {tool!r}",
                }
            )
            any_error = True
            continue
        # nested user commands forbidden
        try:
            from .mcp import _load_user_commands  # noqa: PLC0415

            if tool in _load_user_commands():
                step_results.append(
                    {
                        "ok": False,
                        "tool": tool,
                        "mode": mode,
                        "message": "nested user commands forbidden",
                    }
                )
                any_error = True
                continue
        except Exception:  # noqa: BLE001
            pass
        enabled = True if tool not in prefs else bool(prefs.get(tool))
        if not enabled:
            step_results.append(
                {
                    "ok": False,
                    "tool": tool,
                    "mode": mode,
                    "message": f"tool disabled in settings.mcp_tools: {tool}",
                }
            )
            any_error = True
            continue
        schema = system[tool].get("inputSchema") if isinstance(system[tool].get("inputSchema"), dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        missing = []
        for key in required:
            if key not in arguments or arguments.get(key) in (None, ""):
                missing.append(key)
        unknown = [k for k in arguments.keys() if props and k not in props]
        msgs = []
        if missing:
            msgs.append("missing required: " + ", ".join(missing))
        if unknown:
            msgs.append("unknown args: " + ", ".join(unknown))
        # light type checks
        for key, val in arguments.items():
            if key not in props:
                continue
            p = props.get(key) or {}
            typ = p.get("type")
            if typ == "integer" and not isinstance(val, int):
                msgs.append(f"{key}: expected integer")
            elif typ == "number" and not isinstance(val, (int, float)):
                msgs.append(f"{key}: expected number")
            elif typ == "boolean" and not isinstance(val, bool):
                msgs.append(f"{key}: expected boolean")
            elif typ == "string" and not isinstance(val, str):
                msgs.append(f"{key}: expected string")
            elif typ == "array" and not isinstance(val, list):
                msgs.append(f"{key}: expected array")
            elif typ == "object" and not isinstance(val, dict):
                msgs.append(f"{key}: expected object")
            enum = p.get("enum")
            if isinstance(enum, list) and val not in enum:
                msgs.append(f"{key}: must be one of {enum}")
        ok = not msgs
        if not ok:
            any_error = True
        step_results.append(
            {
                "ok": ok,
                "tool": tool,
                "mode": mode,
                "arguments": arguments,
                "message": "; ".join(msgs) if msgs else "ok",
            }
        )

    groups_out = []
    try:
        from .mcp import _group_user_command_steps  # noqa: PLC0415

        for kind, group in _group_user_command_steps(
            [
                {
                    "tool": s.get("tool"),
                    "mode": s.get("mode") or "series",
                    "arguments": s.get("arguments") if isinstance(s.get("arguments"), dict) else {},
                }
                for s in (steps or [])
                if isinstance(s, dict)
            ]
        ):
            groups_out.append(
                {"kind": kind, "tools": [g.get("tool") for g in group], "count": len(group)}
            )
    except Exception:  # noqa: BLE001
        groups_out = []

    # sentence summary
    parts = []
    for g in groups_out:
        tools = [t for t in (g.get("tools") or []) if t]
        if g.get("kind") == "parallel" and len(tools) > 1:
            if len(tools) == 2:
                parts.append(f"{tools[0]} and {tools[1]} in parallel")
            else:
                parts.append(", ".join(tools[:-1]) + f", and {tools[-1]} in parallel")
        elif tools:
            parts.append(tools[0])
    if not parts:
        summary = ""
    elif len(parts) == 1:
        summary = f"Run {parts[0]}."
    else:
        summary = "Run " + ", then ".join(parts[:-1]) + f", then {parts[-1]}."

    return {
        "ok": not any_error and bool(step_results),
        "dry_run": True,
        "executed": False,
        "steps": step_results,
        "groups": groups_out,
        "summary": summary,
        "message": "validated without executing tools" if not any_error else "validation failed",
    }


def mcp_tools_panel_html(view: dict | None = None) -> str:
    """Live MCP surface — system TOOL_DEFS + user command chains."""
    import json as _json

    listen = _ACTIVE_LISTEN or read_listen_file() or {}
    mcp_url = listen.get("mcp_url") or "/mcp"
    stdio = listen.get("stdio") or "python3 -m port_registry_app --mcp-stdio"
    settings = (view or {}).get("settings") or {}
    prefs = settings.get("mcpTools") if isinstance(settings.get("mcpTools"), dict) else {}
    user_cmds = settings.get("mcpUserCommands") if isinstance(settings.get("mcpUserCommands"), dict) else {}

    def tool_row(name: str, desc: str, *, user: bool = False) -> str:
        desc_one = " ".join((desc or "").strip().split())
        if len(desc_one) > 140:
            desc_one = desc_one[:137] + "…"
        is_enabled = True if name not in prefs else bool(prefs.get(name))
        checked = "checked" if is_enabled else ""
        aria = "true" if is_enabled else "false"
        disabled_cls = "" if is_enabled else " is-disabled"
        user_cls = " pr-mcp-user" if user else ""
        kind = "user-command" if user else "system"
        actions = ""
        if user:
            actions = (
                '<div class="pr-mcp-tool-actions">'
                f'<button type="button" class="pr-btn" data-pr-action="mcp-user-command-edit" '
                f'data-name="{esc(name)}" title="Load into composer">Edit</button>'
                f'<button type="button" class="pr-btn pr-btn-danger" data-pr-action="mcp-user-command-delete" '
                f'data-name="{esc(name)}" title="Delete user command">Delete</button>'
                "</div>"
            )
        return (
            f'<li class="pr-mcp-tool{disabled_cls}{user_cls}" data-mcp-tool="{esc(name)}" data-kind="{kind}">'
            f'<code class="pr-mcp-name">{esc(name)}</code>'
            f'<span class="pr-mcp-desc">{esc(desc_one)}</span>'
            f'<label class="pr-switch pr-mcp-toggle" title="When off, tool is hidden from tools/list and tools/call returns tool_disabled">'
            f'<span class="pr-switch-label">Model can invoke</span>'
            f'<input type="checkbox" role="switch" aria-checked="{aria}" {checked}'
            f' data-pr-switch="mcp-tool-set" data-pr-action="mcp-tool-set"'
            f' data-name="{esc(name)}">'
            f'<span class="pr-switch-track" aria-hidden="true"><span class="pr-switch-thumb"></span></span>'
            f"</label>"
            f"{actions}"
            f"</li>"
        )

    system_rows = []
    enabled_count = 0
    for tool in TOOL_DEFS:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        desc = tool.get("description") or ""
        if not isinstance(desc, str):
            desc = ""
        is_enabled = True if name not in prefs else bool(prefs.get(name))
        if is_enabled:
            enabled_count += 1
        system_rows.append(tool_row(name, desc, user=False))

    user_rows = []
    for name, cmd in sorted(user_cmds.items()):
        if not isinstance(cmd, dict):
            continue
        steps = cmd.get("steps") or []
        step_bits = []
        for s in steps:
            if isinstance(s, dict) and s.get("tool"):
                step_bits.append(f"{s.get('tool')}[{s.get('mode') or 'series'}]")
        desc = cmd.get("description") or ""
        if step_bits:
            desc = (desc + " — " if desc else "") + " → ".join(step_bits)
        is_enabled = True if name not in prefs else bool(prefs.get(name))
        if is_enabled:
            enabled_count += 1
        user_rows.append(tool_row(name, desc, user=True))

    system_names = [
        t.get("name")
        for t in TOOL_DEFS
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t.get("name").strip()
    ]
    options = "".join(f'<option value="{esc(n)}">{esc(n)}</option>' for n in system_names)
    schemas = {}
    for t in TOOL_DEFS:
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t.get("name").strip():
            schemas[t["name"].strip()] = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object", "properties": {}}
    schemas_json = _json.dumps(schemas, separators=(",", ":")).replace("</", "<\\/")
    user_cmds_json = _json.dumps(user_cmds, separators=(",", ":")).replace("</", "<\\/")
    composer = (
        '<div class="pr-mcp-composer" id="pr-mcp-user-composer">'
        "<h4>Compose user command</h4>"
        '<p class="pr-mcp-meta" style="margin:0">v1 limits: max 12 steps; only existing system tools; '
        "no nested chains. Parallel steps in a consecutive group run concurrently; series groups run in order.</p>"
        '<div class="pr-mcp-composer-row">'
        '<input type="text" id="pr-mcp-uc-name" class="pr-mcp-uc-name" placeholder="command-name" pattern="[A-Za-z0-9_-]+">'
        '<input type="text" id="pr-mcp-uc-desc" class="pr-mcp-uc-desc" placeholder="description" style="flex:1">'
        "</div>"
        '<div class="pr-mcp-composer-row">'
        f'<select id="pr-mcp-uc-tool">{options}</select>'
        '<select id="pr-mcp-uc-mode"><option value="series">series</option><option value="parallel">parallel</option></select>'
        '<button type="button" class="pr-btn" id="pr-mcp-uc-add-step">Add step</button>'
        '<button type="button" class="pr-btn" data-pr-action="mcp-user-command-save">Save command</button>'
        '<button type="button" class="pr-btn" id="pr-mcp-uc-dry-run" title="Validate tools and args without side effects">Dry-run</button>'
        '<button type="button" class="pr-btn" id="pr-mcp-uc-clear" title="Clear composer">Clear</button>'
        "</div>"
        '<div class="pr-mcp-composer-row">'
        '<span style="font-size:11px;color:var(--muted)">Templates:</span>'
        '<button type="button" class="pr-btn" id="pr-mcp-uc-tpl-status-doctor" title="status then doctor">status → doctor</button>'
        '<button type="button" class="pr-btn" id="pr-mcp-uc-tpl-apply-defaults" title="apply_defaults">apply_defaults</button>'
        "</div>"
        '<div class="pr-mcp-uc-summary" id="pr-mcp-uc-summary" aria-live="polite"></div>'
        '<div class="pr-mcp-composer-steps" id="pr-mcp-uc-steps">No steps yet.</div>'
        '<div class="pr-mcp-uc-dry" id="pr-mcp-uc-dry" role="status"></div>'
        f'<script type="application/json" id="pr-mcp-tool-schemas">{schemas_json}</script>'
        f'<script type="application/json" id="pr-mcp-user-commands">{user_cmds_json}</script>'
        "</div>"
    )

    if system_rows:
        system_body = (
            f'<details class="pr-mcp-system-details" id="pr-mcp-system-details">'
            f'<summary>System tools <span class="tag">{len(system_rows)}</span><span class="pr-disclose-hint" aria-hidden="true">Show</span></summary>'
            f'<ul class="pr-mcp-list">{"".join(system_rows)}</ul>'
            f"</details>"
        )
    else:
        system_body = '<div class="empty">No MCP tools registered.</div>'
    user_body = (
        f'<div class="pr-mcp-section">User commands</div>'
        + (
            f'<ul class="pr-mcp-list">{"".join(user_rows)}</ul>'
            if user_rows
            else '<div class="empty">No user commands yet — compose one above.</div>'
        )
    )
    count = len(system_rows) + len(user_rows)
    return (
        f'<div class="panel pr-panel pr-mcp" id="pr-mcp-tools">'
        f'<div class="pr-mcp-head"><h3>MCP tools</h3>'
        f'<span class="tag">{esc(SERVER_NAME)} · v{esc(SERVER_VERSION)} · {enabled_count}/{count} enabled</span></div>'
        f'<div class="pr-mcp-jump-row">'
        f'<a class="pr-compose-jump" href="#pr-mcp-user-composer">Compose</a>'
        f'<span class="tag">user command composer</span></div>'
        f'<p class="pr-mcp-meta">Live surface from <code>tools/list</code> — same as '
        f'<a class="pr-port-link" href="{esc(mcp_url)}" target="_blank" rel="noopener"><code>{esc(mcp_url)}</code></a> '
        f'and stdio <code>{esc(stdio)}</code>. '
        f'Toggles filter live <code>tools/list</code> + <code>tools/call</code> (disabled tools stay listed here so you can re-enable). '
        f'User commands are marked <code>x-portskill-kind: user-command</code> and respect the same enable map.</p>'
        f"{composer}"
        f"{user_body}"
        f"{system_body}"
        f"</div>"
    )


def handoff_panel_html(view: dict | None = None) -> str:
    """First-class Session Handoff product section (collapsed). Not Coming soon."""
    settings = (view or {}).get("settings") or {}
    raw_settings = {
        "handoff_enabled": bool(settings.get("handoffEnabled")),
        "handoff_kit": settings.get("handoffKit"),
    }
    status = status_payload(settings=raw_settings)
    enabled = bool(status.get("handoff_enabled"))
    present = bool(status.get("ok"))
    checked = "checked" if enabled else ""
    aria = "true" if enabled else "false"
    kit_path = esc(status.get("kit_path") or "")
    source = esc(status.get("kit_source") or "vendored")
    skill_ok = "yes" if status.get("installed") else "no"
    err = status.get("error")
    err_html = (
        f'<p class="pr-handoff-err" id="pr-handoff-error">{esc(err)}</p>'
        if err
        else ""
    )
    count = status.get("open_count")
    if status.get("open_count_ok"):
        count_html = f"{count} open"
    elif status.get("open_count_error"):
        count_html = f"ledger: {esc(str(status.get('open_count_error')))}"
    else:
        count_html = "ledger unavailable"
    rows = []
    for item in status.get("install_matrix") or []:
        if not isinstance(item, dict):
            continue
        if item.get("installed"):
            state = "installed"
        elif item.get("packaged"):
            state = "packaged"
        elif item.get("detectable"):
            state = "not found"
        else:
            state = "see install help"
        note = item.get("note") or item.get("how") or ""
        manage = item.get("manage") if isinstance(item.get("manage"), dict) else {}
        action = str(manage.get("action") or "")
        button = str(manage.get("button") or "Manage")
        honesty = str(manage.get("honesty") or "")
        copy = str(manage.get("copy") or "")
        if action == "handoff-copy":
            btn = (
                f'<button type="button" class="pr-btn" data-pr-action="handoff-copy" '
                f'data-copy="{esc(copy)}">{esc(button)}</button>'
            )
        elif action in ("handoff-package", "handoff-codex-install"):
            btn = (
                f'<button type="button" class="pr-btn" data-pr-action="{esc(action)}" '
                f'data-surface="{esc(item.get("id") or "")}">{esc(button)}</button>'
            )
        else:
            btn = ""
        manage_html = (
            f'<div class="pr-handoff-manage">{btn}'
            f'<span class="pr-handoff-honesty">{esc(honesty)}</span></div>'
        )
        rows.append(
            "<tr>"
            f"<td>{esc(item.get('label') or item.get('id') or '')}</td>"
            f"<td>{esc(state)}</td>"
            f"<td>{manage_html}</td>"
            f"<td>{esc(note)}</td>"
            "</tr>"
        )
    matrix = (
        '<table class="pr-handoff-matrix">'
        "<thead><tr><th>Surface</th><th>Status</th><th>Add / manage</th><th>Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows
        else '<p class="empty">Install matrix unavailable.</p>'
    )
    help_text = esc(install_help_text(raw_settings))
    kit_line = (
        f'<code class="pr-handoff-kit" id="pr-handoff-kit-path">{kit_path}</code>'
        f' <span class="tag">{source}</span>'
    )
    if present:
        kit_status = "Kit present — vendored Session Handoff product (no extra checkout required)."
    else:
        kit_status = "Point at a Session Handoff kit checkout (or restore vendor/session-handoff-kit)."
    override = esc(settings.get("handoffKit") or "")
    return (
        f'<div class="panel pr-panel pr-handoff" id="pr-handoff">'
        f'<details class="pr-mcp-system-details pr-handoff-details" id="pr-handoff-details">'
        f'<summary>Session Handoff <span class="tag">{esc("on" if enabled else "off")}</span>'
        f'<span class="pr-disclose-hint" aria-hidden="true">Show</span></summary>'
        f'<div class="pr-handoff-body">'
        f'<p>{esc(kit_status)}</p>'
        f'<div class="pr-handoff-row">'
        f'<label class="pr-switch" title="Persist settings.handoff_enabled">'
        f'<span class="pr-switch-label">Enable / add</span>'
        f'<input type="checkbox" role="switch" aria-checked="{aria}" {checked}'
        f' data-pr-switch="handoff-set-enabled" data-pr-action="handoff-set-enabled">'
        f'<span class="pr-switch-track" aria-hidden="true"><span class="pr-switch-thumb"></span></span>'
        f"</label>"
        f'<span class="tag">skill readable: {esc(skill_ok)}</span>'
        f'<span class="tag" id="pr-handoff-open-count">{count_html}</span>'
        f"</div>"
        f'<div class="pr-handoff-row"><span>Kit path</span> {kit_line}</div>'
        f'<form class="pr-handoff-row" id="pr-handoff-kit-form" style="margin:0">'
        f'<input type="text" id="pr-handoff-kit-input" placeholder="optional kit override (PORTSKILL_HANDOFF_KIT)" '
        f'value="{override}" style="flex:1;min-width:16rem">'
        f'<button type="submit" class="pr-btn" data-pr-action="handoff-set-kit">Set kit path</button>'
        f"</form>"
        f"{err_html}"
        f"<h4>Install matrix</h4>"
        f"<p>Add or manage each surface from the kit README. Cowork and chat share "
        f"<code>package.sh</code>; Claude Code and Chrome are copy-the-path (Portskill "
        f"cannot run <code>/plugin</code> or Load unpacked).</p>"
        f"{matrix}"
        f"<h4>Install help</h4>"
        f'<pre class="pr-handoff-help" id="pr-handoff-help">{help_text}</pre>'
        f"</div></details></div>"
    )


def defaults_section_html(view: dict) -> str:
    """Compile Default On services for a top-of-page Defaults section."""
    rows = []
    for proj in view.get("projects") or []:
        project = proj.get("project") or ""
        label = proj.get("projectLabel") or project
        for rng in proj.get("ranges") or []:
            if (rng.get("defaultState") or "off") != "on":
                continue
            if (rng.get("state") or "") == "released":
                continue
            rid = esc(rng.get("id") or "")
            start = rng.get("start")
            end = rng.get("end")
            port = (
                str(start)
                if start == end
                else f"{start}–{end}"
            )
            note = rng.get("note") or "untitled"
            state = rng.get("state") or "reserved"
            url = rng.get("url")
            port_html = (
                f'<a class="pr-port-link" href="{esc(url)}" target="_blank" rel="noopener">{esc(port)}</a>'
                if url and start == end
                else f'<span class="mono">{esc(port)}</span>'
            )
            rows.append(
                f'<li class="pr-defaults-item" data-range-id="{rid}">'
                f'{port_html}'
                f'<span class="pr-defaults-note">{esc(note)}</span>'
                f'<span class="pr-defaults-proj" title="{esc(project)}">{esc(label)}</span>'
                f'<span class="badge {state_class(state)}">{esc(state)}</span>'
                f'<a class="pr-defaults-jump" href="#range-{rid}" title="Jump to service">Jump</a>'
                f"</li>"
            )
    if not rows:
        body = '<div class="empty">No default services yet — flip Default on a service card.</div>'
    else:
        body = f'<ul class="pr-defaults-list">{"".join(rows)}</ul>'
    count = len(rows)
    return (
        f'<div class="panel pr-panel pr-defaults" id="pr-defaults">'
        f'<div class="pr-defaults-head"><h3>Defaults</h3>'
        f'<span class="tag">{count} service{"s" if count != 1 else ""}</span></div>'
        f"{body}"
        f"</div>"
    )


def render_page(view: dict, tailscale: dict | None = None) -> str:
    stats = view["stats"]
    projects = view["projects"]
    if not projects:
        body = (
            '<div class="empty">No ports allocated yet. Allocate a range to get started '
            '(one workspace shows all services).</div>'
            '<div style="margin-top:12px"><button type="button" class="pr-btn" data-pr-action="allocate-prompt" '
            'title="Allocate a new port range">Allocate ports…</button></div>'
        )
    else:
        body = "".join(project_html(p) for p in projects)
    stats_block = f"""<div class="stats pr-stats">
    {stat_html("Allocated ranges", stats["totalRanges"])}
    {stat_html("Active ranges", stats["activeRanges"])}
    {stat_html("Default On", stats.get("defaultOn", 0))}
    {stat_html("Presets", stats.get("presetCount", 0))}
    {stat_html("Pool ports used", stats["poolPortsUsed"])}
  </div>"""
    ts = tailscale or {"chip": "Checking…", "state": "pending", "logged_in": False}
    try:
        ps_serve = probe_portskill_serve_status()
    except Exception:  # noqa: BLE001
        ps_serve = {"chip": "Off", "state": "off", "enabled_preference": bool((view.get("settings") or {}).get("servePortskillOnTailscale"))}
    ts_block = tailscale_toolbar_html(ts, view, ps_serve)
    heading = "Workspace"
    modified_block = modified_badge_html(view)
    history_block = history_panel_html(view)
    toolbar = ""  # Tailscale + Actions live in topbar
    topbar_right = f"""<div class="pr-topbar-right" data-iterate="toolbar">
    {ts_block}
    <button type="button" class="pr-btn" data-pr-action="workspace-panel-toggle" title="Workspace actions" aria-haspopup="dialog" aria-controls="pr-workspace-panel">⚙ Actions</button>
  </div>"""
    defaults_block = defaults_section_html(view)
    mcp_block = mcp_tools_panel_html(view)
    handoff_block = handoff_panel_html(view)
    presets_block = presets_panel_html(view)
    settings_block = settings_panel_html(view)
    env_rail = env_rail_html(view)
    listen = _ACTIVE_LISTEN or read_listen_file() or {}
    mcp_footer_url = esc(listen.get("mcp_url") or "/mcp")
    mcp_footer_label = esc(listen.get("mcp_url") or "/mcp")
    mcp_listen_path = esc(listen.get("listen_path") or str(listen_path()))
    bind_host = listen.get("host")
    loopback_warn = bind_host_warning(bind_host)
    bind_chip = ""
    bind_banner = ""
    if loopback_warn:
        bind_chip = (
            f'<span class="pr-bind-chip" id="pr-bind-chip" title="{esc(loopback_warn)}">'
            f"Not loopback · {esc(bind_host)}</span>"
        )
        bind_banner = (
            f'<div class="pr-bind-banner" id="pr-bind-banner" role="alert">'
            f"{esc(loopback_warn)}</div>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Portskill</title>
<style>{console_css()}</style>
<!-- Iterate menu hidden (backend /iterate routes intact)
<link rel="stylesheet" href="/static/iterate-tokens.css">
<link rel="stylesheet" href="/static/iterate.css">
-->
</head>
<body>
<div class="app">
  <aside class="sidebar" data-iterate="sidebar">
    <div class="brand" data-iterate="sidebar-brand"><h1>Portskill</h1><div class="tag">port registry</div></div>
    {env_rail}
  </aside>
  <main class="main">
    <header class="topbar" data-iterate="topbar">
      <div class="pr-topbar-left">
        <div class="pr-topbar-brand" aria-label="Portskill">Portskill</div>
        <div class="crumb">Workspace · <b>all services</b></div>
        <a class="pr-compose-jump" href="#pr-mcp-user-composer" title="Jump to Compose">Compose</a>
        {bind_chip}
      </div>
      {topbar_right}
    </header>
    {bind_banner}
    <section class="content">
      <div class="view-head"><p class="eyebrow" data-iterate="eyebrow">Workspace · Dev tooling</p><h2>{heading}{modified_block}</h2></div>
      {history_block}
      {stats_block}
      {defaults_block}
      {mcp_block}
      {handoff_block}
      {presets_block}
      {settings_block}
      <div class="panel pr-panel">{body}</div>
    </section>
  </main>
</div>
<aside id="pr-workspace-panel" class="pr-workspace-panel" hidden aria-hidden="true">
  <div class="pr-wsp-head">
    <h3 id="pr-wsp-title">Workspace actions</h3>
    <button type="button" class="pr-wsp-close" data-pr-action="workspace-panel-close" aria-label="Close">×</button>
  </div>
  <div class="pr-wsp-body">
    <div class="pr-wsp-section">Lifecycle</div>
    <button type="button" class="pr-btn" data-pr-action="apply-defaults" title="Start services marked Default On">Start Default Services</button>
    <button type="button" class="pr-btn" data-pr-action="start-all" title="Start every non-released service">Start All Services</button>
    <button type="button" class="pr-btn" data-pr-action="stop-non-default" title="Stop running services that are Default Off">Stop non-Default Services</button>
    <button type="button" class="pr-btn" data-pr-action="stop-all" title="Stop all active services">Stop All Services</button>
    <button type="button" class="pr-btn" data-pr-action="set-defaults-from-current" title="Set Default On for active services, Default Off otherwise">Set current states as default</button>
    <div class="pr-wsp-section">Workspace</div>
    <button type="button" class="pr-btn" data-pr-action="compat-check" title="Check port conflicts in this workspace">Check compatibility</button>
    <button type="button" class="pr-btn" data-pr-action="export-environment">Export Workspace</button>
    <label>Import Workspace <input type="file" id="pr-import-file" accept="application/json,.json"></label>
    <form id="pr-import-path-form" style="display:flex;flex-direction:column;gap:8px;margin:0">
      <input type="text" id="pr-import-path" placeholder="/path/to/workspace.json">
      <button type="submit" class="pr-btn">Import path</button>
    </form>
    <div class="pr-wsp-section">Ports</div>
    <button type="button" class="pr-btn" data-pr-action="allocate-prompt" title="Allocate a new port range">Allocate ports…</button>
    <button type="button" class="pr-btn" data-pr-action="discover-ports-open" title="Poll local listeners and Tailscale Serve; import missing into workspace">Import listening ports…</button>
  </div>
</aside>
<aside id="pr-discover-overlay" class="pr-discover-overlay" hidden aria-hidden="true">
  <div class="pr-discover-modal" role="dialog" aria-modal="true" aria-labelledby="pr-discover-title">
    <div class="pr-discover-head">
      <div>
        <h3 id="pr-discover-title">Import listening ports</h3>
        <p class="pr-discover-empty" id="pr-discover-subtitle" style="margin:6px 0 0">Local listeners + Tailscale Serve, diffed against this workspace. Claims the exact port (never silent 20xxx realloc). Never Funnel.</p>
      </div>
      <button type="button" class="pr-wsp-close" data-pr-action="discover-ports-close" aria-label="Close">×</button>
    </div>
    <div class="pr-discover-body" id="pr-discover-list">
      <div class="pr-discover-empty">Loading…</div>
    </div>
    <div class="pr-discover-foot">
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#9aa3b2;margin-right:auto">
        <input type="checkbox" id="pr-discover-opt-serve"> Opt in Tailscale Serve for selected (default: only if already Serving)
      </label>
      <button type="button" class="pr-btn" data-pr-action="discover-ports-refresh">Refresh</button>
      <button type="button" class="pr-btn" data-pr-action="discover-ports-import" id="pr-discover-import-btn">Import selected</button>
    </div>
  </div>
</aside>
<script>
(function(){{

  function postAction(payload){{
    return fetch('/port-registry/actions',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(payload)}})
      .then(function(res){{return res.json().then(function(body){{return {{ok:res.ok,body:body,status:res.status}};}});}});
  }}
  function pollTailscaleConnected(maxMs, onDone){{
    var started=Date.now();
    function tick(){{
      postAction({{action:'tailscale-status'}}).then(function(result){{
        var body=result.body||{{}};
        var logged=!!(body.logged_in||(body.result&&body.result.logged_in)||body.chip==='Connected');
        if(logged){{onDone(true, body);return;}}
        if(Date.now()-started>maxMs){{onDone(false, body);return;}}
        setTimeout(tick, 2000);
      }}).catch(function(){{
        if(Date.now()-started>maxMs){{onDone(false, {{}});return;}}
        setTimeout(tick, 2000);
      }});
    }}
    tick();
  }}
  function runBrowserLogin(thenServe){{
    return postAction({{action:'tailscale-login', wait:0}}).then(function(result){{
      var body=result.body||{{}};
      var url=body.auth_url||(body.result&&body.result.auth_url);
      if(url){{
        try{{window.open(url,'_blank','noopener');}}catch(e){{}}
      }}
      if(body.logged_in||(body.result&&body.result.logged_in)||body.already_logged_in){{
        if(thenServe){{return thenServe(true);}}
        window.location.reload();
        return;
      }}
      alert((body.message||'Complete Browser Login in your browser, then wait for Connected.')+(url?('\\n'+url):''));
      pollTailscaleConnected(90000, function(ok, statusBody){{
        if(!ok){{
          alert('Still Needs login after waiting. Finish Browser Login, then try again.');
          if(thenServe)thenServe(false);
          return;
        }}
        if(thenServe){{thenServe(true);}}
        else {{window.location.reload();}}
      }});
    }});
  }}
  function handleResult(result, btn){{
    if(result.ok){{
      if(result.body&&result.body.download){{
        var blob=new Blob([result.body.download],{{type:'application/json'}});
        var a=document.createElement('a');
        a.href=URL.createObjectURL(blob);
        a.download=result.body.filename||'port-registry-workspace.json';
        a.click();
        URL.revokeObjectURL(a.href);
      }}
      if(result.body&&result.body.compatReport){{
        var rep=result.body.compatReport;
        if(rep.compatible){{
          alert('Compatible: no port conflicts ('+(rep.claim_count||0)+' claims).');
        }} else {{
          var lines=(rep.conflicts||[]).map(function(c){{return c.message||c.reason;}});
          alert('Not compatible:\\n'+(lines.join('\\n')||'port conflict'));
        }}
        if(btn)btn.disabled=false;
        return;
      }}
      if(result.body&&result.body.warnings&&result.body.warnings.length){{
        var w=result.body.warnings.map(function(x){{return x.message||JSON.stringify(x);}}).join('\\n');
        alert('Completed with warnings:\\n'+w);
      }}
      if(result.body&&(result.body.backup_path||(result.body.result&&result.body.result.backup_path))){{
        var bp=result.body.backup_path||result.body.result.backup_path;
        alert('Import complete.\\nBackup saved to:\\n'+bp);
      }}
      if(result.body&&result.body.handoff_notice){{
        alert(result.body.handoff_notice);
      }}
      window.location.reload();
      return;
    }}
    if(btn)btn.disabled=false;
    if(result.body&&(result.body.needs_login||result.body.reason==='tailscale_auth_required'||result.body.reason==='tailscale_auth_pending')){{
      var msg=result.body.message||'Tailscale Serve needs Browser Login first.';
      if(confirm(msg+'\\n\\nStart Browser Login now?')){{
        var pending=result.body.pendingServe||null;
        var pendingPs=result.body.pendingPortskillServe||null;
        runBrowserLogin(function(ok){{
          if(!ok){{return;}}
          if(pending){{
            postAction({{action:'set-tailnet', project:pending.project, rangeId:pending.rangeId, mode:'serve'}})
              .then(function(r){{handleResult(r,null);}});
          }} else if(pendingPs){{
            postAction({{action:'serve-portskill', enabled:true}})
              .then(function(r){{handleResult(r,null);}});
          }} else {{
            window.location.reload();
          }}
        }});
      }}
      return;
    }}
    if(result.body&&(result.body.needs_input||result.body.status==='needs_input')){{
      alert(result.body.prompt||'Needs input');
    }} else {{
      var msg=(result.body&&result.body.message)||'Action failed';
      if(result.body&&result.body.reason==='compat_conflict'&&result.body.conflicts){{
        msg+='\\n'+(result.body.conflicts.map(function(c){{return c.message||c.reason;}}).join('\\n'));
      }}
      alert(msg);
    }}
  }}
  document.addEventListener('change', function(event){{
    var input=event.target.closest('[data-pr-switch]');
    if(!input)return;
    var action=input.getAttribute('data-pr-action')||input.getAttribute('data-pr-switch');
    var rangeId=input.getAttribute('data-range-id');
    var project=input.getAttribute('data-project');
    var payload={{action:action, project:project, rangeId:rangeId}};
    if(action==='set-default'){{
      payload.state=input.checked ? (input.getAttribute('data-state-on')||'on') : (input.getAttribute('data-state-off')||'off');
    }}
    if(action==='set-tailnet'){{
      payload.mode=input.checked ? (input.getAttribute('data-mode-on')||'serve') : (input.getAttribute('data-mode-off')||'none');
    }}
    if(action==='mcp-tool-set'){{
      payload.name=input.getAttribute('data-name');
      payload.enabled=!!input.checked;
    }}
    if(action==='handoff-set-enabled'){{
      payload.enabled=!!input.checked;
    }}
    if(action==='serve-portskill'){{
      payload.enabled=!!input.checked;
    }}
    input.disabled=true;
    postAction(payload).then(function(result){{
      if(!result.ok && (result.body&&(result.body.needs_login||result.body.reason==='tailscale_auth_required'))){{
        result.body.pendingServe={{project:project, rangeId:rangeId}};
      }}
      if(!result.ok){{
        if(action==='set-default'){{ input.checked = payload.state==='off'; }}
        if(action==='set-tailnet' && payload.mode==='serve'){{ input.checked=false; }}
        if(action==='set-tailnet' && payload.mode==='none'){{ input.checked=true; }}
        if(action==='mcp-tool-set'){{ input.checked = !payload.enabled; }}
        if(action==='handoff-set-enabled'){{ input.checked = !payload.enabled; }}
        if(action==='serve-portskill'){{ input.checked = !payload.enabled; }}
      }} else if(action==='serve-portskill'){{
        applyPortskillServeStatus(result.body||{{}});
      }} else if(action==='mcp-tool-set'){{
        var row=input.closest('.pr-mcp-tool');
        if(row){{
          if(payload.enabled){{ row.classList.remove('is-disabled'); }}
          else {{ row.classList.add('is-disabled'); }}
        }}
      }}
      input.disabled=false;
      handleResult(result, null);
    }}).catch(function(){{input.disabled=false;}});
  }});
  function workspacePanel(){{return document.getElementById('pr-workspace-panel');}}
  function discoverOverlay(){{return document.getElementById('pr-discover-overlay');}}
  function closeDiscoverPanel(){{
    var ov=discoverOverlay();
    if(!ov)return;
    ov.hidden=true;
    ov.setAttribute('aria-hidden','true');
  }}
  function openDiscoverPanel(){{
    var ov=discoverOverlay();
    if(!ov)return;
    ov.hidden=false;
    ov.setAttribute('aria-hidden','false');
    refreshDiscoverList();
  }}
  function renderDiscoverList(body){{
    var list=document.getElementById('pr-discover-list');
    if(!list)return;
    var discoveries=(body&&body.discoveries)||[];
    if(!discoveries.length){{
      list.innerHTML='<div class="pr-discover-empty">No local listeners or Serve mappings found.</div>';
      return;
    }}
    var html='';
    discoveries.forEach(function(d){{
      var claimed=!!d.claimed;
      var serve=!!d.serve;
      var port=d.port;
      var note=d.note||d.command||('port '+port);
      var meta=[];
      if(d.cmdline)meta.push(d.cmdline);
      else if(d.command)meta.push(d.command);
      if(d.bind)meta.push('bind '+d.bind);
      if(claimed&&d.claim&&d.claim.range_id)meta.push('in workspace · '+d.claim.range_id);
      var badge=serve?'<span class="pr-disc-badge serve">serve</span>':'<span class="pr-disc-badge">local</span>';
      if(claimed)badge+=' <span class="pr-disc-badge">in workspace</span>';
      var check=claimed
        ? '<input type="checkbox" disabled title="Already claimed">'
        : '<input type="checkbox" class="pr-discover-check" data-port="'+port+'" data-serve="'+(serve?'1':'0')+'" data-note="'+(String(note).replace(/"/g,'&quot;'))+'"'+(d.project_guess?(' data-project="'+String(d.project_guess).replace(/"/g,'&quot;')+'"'):'')+'>';
      html+='<label class="pr-discover-row'+(claimed?' is-claimed':'')+'">'
        +check
        +'<span class="pr-disc-port">:'+port+'</span>'
        +'<span><div>'+String(note).replace(/</g,'&lt;')+'</div><div class="pr-disc-meta">'+String(meta.join(' · ')).replace(/</g,'&lt;')+'</div></span>'
        +'<span>'+badge+'</span>'
        +'</label>';
    }});
    var counts=body.counts||{{}};
    var sub=document.getElementById('pr-discover-subtitle');
    if(sub){{
      sub.textContent='Missing '+(counts.missing||0)+' · claimed '+(counts.claimed||0)+' · Serve '+(counts.serve||0)+'. Claims exact ports; never Funnel.';
    }}
    list.innerHTML=html;
  }}
  function refreshDiscoverList(){{
    var list=document.getElementById('pr-discover-list');
    if(list)list.innerHTML='<div class="pr-discover-empty">Loading…</div>';
    postAction({{action:'discover-ports'}}).then(function(result){{
      if(!result.ok){{
        if(list)list.innerHTML='<div class="pr-discover-empty">Discover failed: '+(((result.body||{{}}).message)||'error')+'</div>';
        return;
      }}
      renderDiscoverList(result.body||{{}});
    }}).catch(function(){{
      if(list)list.innerHTML='<div class="pr-discover-empty">Discover failed.</div>';
    }});
  }}
  function importSelectedDiscoveries(){{
    var checks=document.querySelectorAll('.pr-discover-check:checked');
    var ports=[];
    var notes={{}};
    var project=null;
    checks.forEach(function(c){{
      var p=parseInt(c.getAttribute('data-port'),10);
      if(!p)return;
      ports.push(p);
      var n=c.getAttribute('data-note');
      if(n)notes[String(p)]=n;
      if(!project && c.getAttribute('data-project'))project=c.getAttribute('data-project');
    }});
    if(!ports.length){{alert('Select one or more missing ports to import.');return;}}
    var optServe=document.getElementById('pr-discover-opt-serve');
    var payload={{action:'discover-import', ports:ports, notes:notes}};
    if(optServe&&optServe.checked)payload.tailnet='serve';
    if(project)payload.project=project;
    var btn=document.getElementById('pr-discover-import-btn');
    if(btn)btn.disabled=true;
    postAction(payload).then(function(result){{
      if(btn)btn.disabled=false;
      if(result.ok){{
        var n=((result.body||{{}}).count_imported)||(((result.body||{{}}).imported)||[]).length||0;
        alert('Imported '+n+' port(s) at their exact start ports.');
        closeDiscoverPanel();
        window.location.reload();
        return;
      }}
      handleResult(result, btn);
    }}).catch(function(){{if(btn)btn.disabled=false;alert('Import failed');}});
  }}

  function openWorkspacePanel(){{
    var panel=workspacePanel();
    if(!panel)return;
    panel.removeAttribute('hidden');
    panel.setAttribute('aria-hidden','false');
  }}
  function closeWorkspacePanel(){{
    var panel=workspacePanel();
    if(!panel)return;
    panel.setAttribute('hidden','');
    panel.setAttribute('aria-hidden','true');
  }}
  function readCardValues(card){{
    var out={{start_script:'',stop_script:'',command:'',cwd:''}};
    if(!card)return out;
    card.querySelectorAll('[data-draft-field]').forEach(function(inp){{
      out[inp.getAttribute('data-draft-field')]=inp.value||'';
    }});
    return out;
  }}
  function cardBaselines(card){{
    return {{
      start_script:card.getAttribute('data-base-start-script')||'',
      stop_script:card.getAttribute('data-base-stop-script')||'',
      command:card.getAttribute('data-base-command')||'',
      cwd:card.getAttribute('data-base-cwd')||''
    }};
  }}
  function valuesEqual(a,b){{
    return (a.start_script||'')===(b.start_script||'')
      && (a.stop_script||'')===(b.stop_script||'')
      && (a.command||'')===(b.command||'')
      && (a.cwd||'')===(b.cwd||'');
  }}
  function updateCardModified(card){{
    if(!card)return;
    var mod=card.querySelector('[data-card-modified]');
    if(!mod)return;
    if(valuesEqual(readCardValues(card), cardBaselines(card)))mod.setAttribute('hidden','');
    else mod.removeAttribute('hidden');
  }}
  document.addEventListener('input',function(event){{
    var inp=event.target.closest('.pr-range [data-draft-field]');
    if(!inp)return;
    var card=inp.closest('.pr-range');
    if(!card||!card.classList.contains('pr-editing'))return;
    updateCardModified(card);
  }});
  document.addEventListener('click',function(event){{
    var closeBtn=event.target.closest('.pr-env-tab-close');
    if(closeBtn){{
      event.preventDefault();
      event.stopPropagation();
      var cname=closeBtn.getAttribute('data-name');
      postAction({{action:'close-environment-tab', name:cname}}).then(function(result){{handleResult(result,null);}});
      return;
    }}
    var btn=event.target.closest('[data-pr-action]');
    if(!btn||btn.disabled)return;
    if(btn.matches('input[type=checkbox]'))return;
    var action=btn.getAttribute('data-pr-action');
    var rangeId=btn.getAttribute('data-range-id');
    var project=btn.getAttribute('data-project');
    var state=btn.getAttribute('data-state');
    var name=btn.getAttribute('data-name');
    if(action==='mcp-user-command-save'){{
      if(typeof ucSyncStepsFromDom==='function')ucSyncStepsFromDom();
      var name=((document.getElementById('pr-mcp-uc-name')||{{}}).value||'').trim();
      var desc=((document.getElementById('pr-mcp-uc-desc')||{{}}).value||'').trim();
      if(!name){{alert('Name required');return;}}
      if(!/^[A-Za-z0-9_-]+$/.test(name)){{alert('Name: letters, numbers, _ or -');return;}}
      if(!ucSteps.length){{alert('Add at least one step');return;}}
      btn.disabled=true;
      postAction({{action:'mcp-user-command-save', name:name, description:desc, steps:ucSteps}})
        .then(function(result){{handleResult(result,btn);}})
        .catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='mcp-user-command-edit'){{
      var ename=btn.getAttribute('data-name');
      if(!ename)return;
      if(typeof ucLoadCommand==='function')ucLoadCommand(ename);
      return;
    }}
    if(action==='mcp-user-command-delete'){{
      var dname=btn.getAttribute('data-name');
      if(!dname)return;
      if(!confirm('Delete user command '+dname+'?'))return;
      btn.disabled=true;
      postAction({{action:'mcp-user-command-delete', name:dname}})
        .then(function(result){{handleResult(result,btn);}})
        .catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='tailscale-login'){{
      btn.disabled=true;
      runBrowserLogin(null).finally(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='tailscale-logout'){{
      if(!confirm('Log out of Tailscale on this Mac?'))return;
      btn.disabled=true;
      postAction({{action:'tailscale-logout'}}).then(function(result){{
        refreshTailscaleStatus();
        handleResult(result,btn);
      }}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='workspace-panel-toggle'){{
      var panel=workspacePanel();
      if(!panel)return;
      if(panel.hasAttribute('hidden'))openWorkspacePanel();
      else closeWorkspacePanel();
      return;
    }}
    if(action==='workspace-panel-close'||action==='panel-close'){{
      closeWorkspacePanel();
      return;
    }}
    if(action==='card-edit'){{
      var card=btn.closest('.pr-range')||document.querySelector('.pr-range[data-range-id="'+rangeId+'"]');
      if(!card)return;
      if(card.classList.contains('pr-editing')){{
        card.classList.remove('pr-editing');
      }} else {{
        card.classList.add('pr-editing');
        updateCardModified(card);
      }}
      return;
    }}
    if(action==='card-save'){{
      var card=btn.closest('.pr-range');
      if(!card||!rangeId)return;
      var cur=readCardValues(card);
      var base=cardBaselines(card);
      btn.disabled=true;
      if(valuesEqual(cur,base)){{
        postAction({{action:'draft-revert', rangeId:rangeId}}).then(function(result){{
          handleResult(result,btn);
        }}).catch(function(){{btn.disabled=false;}});
        return;
      }}
      var stagePayload={{action:'draft-stage', rangeId:rangeId,
        start_script:cur.start_script, stop_script:cur.stop_script,
        command:cur.command, cwd:cur.cwd}};
      postAction(stagePayload).then(function(stageResult){{
        if(!stageResult.ok){{handleResult(stageResult,btn);return;}}
        return postAction({{action:'draft-save', rangeId:rangeId}}).then(function(saveResult){{
          handleResult(saveResult,btn);
        }});
      }}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='card-revert'){{
      var card=btn.closest('.pr-range');
      if(!card||!rangeId)return;
      btn.disabled=true;
      postAction({{action:'draft-revert', rangeId:rangeId}}).then(function(result){{
        handleResult(result,btn);
      }}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='edit-mode'){{
      /* Global edit-mode removed from UI; ignore toolbar action if present. */
      return;
    }}
    if(action==='draft-stage'){{
      var card=btn.closest('.pr-range');
      if(!card||!rangeId)return;
      var cur=readCardValues(card);
      var stagePayload={{action:'draft-stage', rangeId:rangeId,
        start_script:cur.start_script, stop_script:cur.stop_script,
        command:cur.command, cwd:cur.cwd}};
      btn.disabled=true;
      postAction(stagePayload).then(function(result){{handleResult(result,btn);}}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='new-environment'){{
      var envName=prompt('New environment name','');
      if(!envName)return;
      btn.disabled=true;
      postAction({{action:'new-environment', name:envName.trim()}}).then(function(result){{handleResult(result,btn);}}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='focus-environment'||action==='open-environment-tab'){{
      postAction({{action:action, name:name}}).then(function(result){{handleResult(result,null);}});
      return;
    }}
    if(action==='discover-ports-open'){{
      openDiscoverPanel();
      return;
    }}
    if(action==='discover-ports-close'){{
      closeDiscoverPanel();
      return;
    }}
    if(action==='discover-ports-refresh'){{
      refreshDiscoverList();
      return;
    }}
    if(action==='discover-ports-import'){{
      importSelectedDiscoveries();
      return;
    }}
    if(action==='allocate-prompt'){{
      var count=prompt('How many ports to allocate?','1');
      if(!count)return;
      var note=prompt('Note for this service (optional)','')||'';
      var proj=prompt('Project path','.')||'.';
      btn.disabled=true;
      postAction({{action:'allocate', count:parseInt(count,10)||1, note:note, project:proj, tailnet:'none'}})
        .then(function(result){{handleResult(result,btn);}}).catch(function(){{btn.disabled=false;}});
      return;
    }}
    if(action==='handoff-copy'){{
      var text=btn.getAttribute('data-copy')||'';
      function copied(){{ var t=btn.textContent; btn.textContent='Copied'; setTimeout(function(){{ btn.textContent=t; }}, 1200); }}
      if(navigator.clipboard && navigator.clipboard.writeText){{
        navigator.clipboard.writeText(text).then(copied).catch(function(){{ prompt('Copy', text); }});
      }} else {{
        prompt('Copy', text);
      }}
      return;
    }}
    if(action==='handoff-codex-install'){{
      if(!confirm('Run codex/install.sh into $CODEX_HOME (default ~/.codex)? Copies hooks + skill and merges hooks.json. Does not enable hooks in config.toml or approve hook trust.'))return;
    }}
    if(action==='handoff-package'){{
      if(!confirm('Run scripts/package.sh? Writes Cowork plugin and chat/Desktop skill artifacts under the kit dist/.'))return;
    }}
    btn.disabled=true;
    var payload={{action:action}};
    if(project)payload.project=project;
    if(rangeId)payload.rangeId=rangeId;
    if(state)payload.state=state;
    if(name)payload.name=name;
    var histIndex=btn.getAttribute('data-index');
    if(histIndex!=null && histIndex!=='')payload.index=parseInt(histIndex,10);
    if(action==='export-environment'){{
      var envName=prompt('Workspace name', focusedName() || 'workspace')||'workspace';
      payload.name=envName;
    }}
    if(action==='preset-save'){{
      var presetName=prompt('Preset name', focusedName() || 'ui-work');
      if(!presetName){{btn.disabled=false;return;}}
      payload.name=presetName;
      var desc=prompt('Description (optional)','')||'';
      payload.description=desc;
    }}
    if(action==='preset-delete'){{
      if(!confirm('Delete preset '+name+'?')){{btn.disabled=false;return;}}
    }}
    if(action==='settings-save'){{
      var launch=document.getElementById('pr-auto-apply-launch');
      var sel=document.getElementById('pr-auto-apply-preset');
      var exitBox=document.getElementById('pr-auto-exit-shutdown');
      var compatBox=document.getElementById('pr-require-compat');
      payload.autoApplyOnLaunch=!!(launch&&launch.checked);
      payload.autoApplyPreset=(sel&&sel.value)||null;
      payload.autoExitOnShutdown=!!(exitBox&&exitBox.checked);
      payload.requireCompat=true;
    }}
    if(action==='compat-check'){{
      var boxes=document.querySelectorAll('.pr-compat-preset:checked');
      var names=[];
      boxes.forEach(function(b){{names.push(b.value);}});
      if(!names.length){{
        document.querySelectorAll('#pr-env-rail .pr-env-tab[data-name]').forEach(function(t){{
          var n=t.getAttribute('data-name');
          if(n&&names.indexOf(n)<0)names.push(n);
        }});
      }}
      payload.presets=names;
    }}
    postAction(payload).then(function(result){{handleResult(result,btn);}}).catch(function(){{btn.disabled=false;}});
  }});
  function focusedName(){{
    var el=document.querySelector('.pr-env-tab.active .pr-env-tab-name');
    return el?el.textContent.trim():'';
  }}
  var fileInput=document.getElementById('pr-import-file');
  if(fileInput){{
    fileInput.addEventListener('change',function(){{
      var file=fileInput.files&&fileInput.files[0];
      if(!file)return;
      var reader=new FileReader();
      reader.onload=function(){{
        postAction({{action:'import-environment',content:String(reader.result||'')}})
          .then(function(result){{handleResult(result,null);}})
          .catch(function(){{alert('Import failed');}});
      }};
      reader.readAsText(file);
    }});
  }}
  var pathForm=document.getElementById('pr-import-path-form');
  if(pathForm){{
    pathForm.addEventListener('submit',function(event){{
      event.preventDefault();
      var path=(document.getElementById('pr-import-path').value||'').trim();
      if(!path){{alert('Enter a path');return;}}
      postAction({{action:'import-environment',path:path}})
        .then(function(result){{handleResult(result,null);}})
        .catch(function(){{alert('Import failed');}});
    }});
  }}
  var kitForm=document.getElementById('pr-handoff-kit-form');
  if(kitForm){{
    kitForm.addEventListener('submit',function(event){{
      event.preventDefault();
      var path=(document.getElementById('pr-handoff-kit-input').value||'').trim();
      postAction({{action:'handoff-set-kit', path:path||'none'}})
        .then(function(result){{handleResult(result,null);}})
        .catch(function(){{alert('Set kit path failed');}});
    }});
  }}

  var ucSteps=[];
  var ucToolSchemas=(function(){{
    try{{
      var el=document.getElementById('pr-mcp-tool-schemas');
      return el?JSON.parse(el.textContent||'{{}}'):{{}};
    }}catch(e){{return {{}};}}
  }})();
  var ucSavedCommands=(function(){{
    try{{
      var el=document.getElementById('pr-mcp-user-commands');
      return el?JSON.parse(el.textContent||'{{}}'):{{}};
    }}catch(e){{return {{}};}}
  }})();
  var ucToolNames=Object.keys(ucToolSchemas);
  function ucEsc(s){{
    return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }}
  function ucGroupSteps(steps){{
    var groups=[], pending=[];
    steps.forEach(function(step, idx){{
      var mode=String(step.mode||'series').toLowerCase();
      if(mode==='parallel'){{ pending.push(idx); return; }}
      if(pending.length){{ groups.push({{kind:'parallel', indices:pending.slice()}}); pending=[]; }}
      groups.push({{kind:'series', indices:[idx]}});
    }});
    if(pending.length) groups.push({{kind:'parallel', indices:pending.slice()}});
    return groups;
  }}
  function ucSentence(){{
    if(!ucSteps.length) return '';
    var groups=ucGroupSteps(ucSteps);
    var parts=groups.map(function(g){{
      var names=g.indices.map(function(i){{ return ucSteps[i].tool; }});
      if(g.kind==='parallel' && names.length>1){{
        return names.slice(0,-1).join(', ') + (names.length>2?',':'') + ' and ' + names[names.length-1] + ' in parallel';
      }}
      return names[0] + (g.kind==='parallel'?' (parallel)':'');
    }});
    if(parts.length===1) return 'Run ' + parts[0] + '.';
    return 'Run ' + parts.slice(0,-1).join(', then ') + ', then ' + parts[parts.length-1] + '.';
  }}
  function ucDefaultArgs(tool){{
    // stop always {{}}; other tools start empty and fill via schema fields
    if(tool==='stop') return {{}};
    return {{}};
  }}
  function ucCollectArgsFromCard(card, tool){{
    if(tool==='stop') return {{}};
    var jsonMode=card.querySelector('.pr-mcp-uc-json-toggle');
    if(jsonMode && jsonMode.checked){{
      var ta=card.querySelector('.pr-mcp-uc-json');
      try{{ var parsed=JSON.parse((ta&&ta.value)||'{{}}'); return (parsed && typeof parsed==='object' && !Array.isArray(parsed))?parsed:{{}}; }}
      catch(e){{ return {{__parse_error: String(e.message||e)}}; }}
    }}
    var schema=ucToolSchemas[tool]||{{properties:{{}}}};
    var props=schema.properties||{{}};
    var out={{}};
    Object.keys(props).forEach(function(key){{
      var input=card.querySelector('[data-uc-arg="'+key+'"]');
      if(!input) return;
      var def=props[key]||{{}};
      var typ=def.type;
      if(input.type==='checkbox'){{
        if(input.checked) out[key]=true;
        return;
      }}
      var raw=(input.value||'').trim();
      if(raw==='') return;
      if(typ==='integer'||typ==='number'){{
        var n=Number(raw);
        if(!isNaN(n)) out[key]= typ==='integer'?Math.trunc(n):n;
        else out[key]=raw;
      }} else if(typ==='boolean'){{
        out[key]= raw==='true'||raw==='1'||raw==='yes';
      }} else if(typ==='array'){{
        try{{ out[key]=JSON.parse(raw); }}catch(e){{ out[key]=raw.split(',').map(function(x){{return x.trim();}}).filter(Boolean); }}
      }} else if(typ==='object'){{
        try{{ out[key]=JSON.parse(raw); }}catch(e){{ out[key]=raw; }}
      }} else {{
        out[key]=raw;
      }}
    }});
    return out;
  }}
  function ucSyncStepsFromDom(){{
    var root=document.getElementById('pr-mcp-uc-steps');
    if(!root) return;
    var cards=root.querySelectorAll('.pr-mcp-uc-card');
    var next=[];
    cards.forEach(function(card){{
      var toolSel=card.querySelector('.pr-mcp-uc-tool-sel');
      var modeSel=card.querySelector('.pr-mcp-uc-mode-sel');
      var tool=toolSel?toolSel.value:'';
      var mode=modeSel?modeSel.value:'series';
      var args=ucCollectArgsFromCard(card, tool);
      if(args && args.__parse_error){{ /* keep previous args if JSON broken */ args={{}}; }}
      if(tool==='stop') args={{}};
      next.push({{tool:tool, arguments:args, mode:mode}});
    }});
    if(cards.length) ucSteps=next;
  }}
  function ucArgsEditorHtml(tool, args){{
    args=args&&typeof args==='object'?args:{{}};
    if(tool==='stop'){{
      return '<div class="pr-mcp-uc-args"><div class="pr-mcp-uc-args-empty">stop always uses <code>{{}}</code> (no args).</div>'
        +'<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)">'
        +'<input type="checkbox" class="pr-mcp-uc-json-toggle" disabled> Edit JSON</label></div>';
    }}
    var schema=ucToolSchemas[tool]||{{type:'object',properties:{{}}}};
    var props=schema.properties||{{}};
    var required=schema.required||[];
    var keys=Object.keys(props);
    var fields='';
    if(!keys.length){{
      fields='<div class="pr-mcp-uc-args-empty">No schema fields — use Edit JSON if needed.</div>';
    }} else {{
      fields=keys.map(function(key){{
        var def=props[key]||{{}};
        var req=required.indexOf(key)>=0?' <span class="req">*</span>':'';
        var val=args.hasOwnProperty(key)?args[key]:'';
        var typ=def.type;
        var label='<label>'+ucEsc(key)+req+'</label>';
        var control='';
        if(typ==='boolean'){{
          control='<input type="checkbox" data-uc-arg="'+ucEsc(key)+'"'+(val===true||val==='true'?' checked':'')+'>';
        }} else if(def.enum && def.enum.length){{
          control='<select data-uc-arg="'+ucEsc(key)+'"><option value="">—</option>'+def.enum.map(function(opt){{
            var s=String(opt);
            return '<option value="'+ucEsc(s)+'"'+(String(val)===s?' selected':'')+'>'+ucEsc(s)+'</option>';
          }}).join('')+'</select>';
        }} else if(typ==='integer'||typ==='number'){{
          control='<input type="number" data-uc-arg="'+ucEsc(key)+'" value="'+(val===''||val==null?'':ucEsc(val))+'"'+(typ==='integer'?' step="1"':'')+'>';
        }} else if(typ==='array'||typ==='object'){{
          var shown=(typeof val==='string')?val:JSON.stringify(val|| (typ==='array'?[]:{{}}));
          if(val===''||val==null) shown='';
          control='<input type="text" data-uc-arg="'+ucEsc(key)+'" placeholder="JSON" value="'+ucEsc(shown)+'">';
        }} else {{
          control='<input type="text" data-uc-arg="'+ucEsc(key)+'" value="'+ucEsc(val==null?'':val)+'"'+(def.description?' title="'+ucEsc(def.description)+'"':'')+'>';
        }}
        return '<div class="pr-mcp-uc-args-row">'+label+control+'</div>';
      }}).join('');
    }}
    var jsonVal=ucEsc(JSON.stringify(args,null,2));
    return '<div class="pr-mcp-uc-args">'
      +'<div class="pr-mcp-uc-fields">'+fields+'</div>'
      +'<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)">'
      +'<input type="checkbox" class="pr-mcp-uc-json-toggle"> Edit JSON</label>'
      +'<textarea class="pr-mcp-uc-json" hidden>'+jsonVal+'</textarea>'
      +'</div>';
  }}
  function ucCardHtml(step, idx){{
    var tool=step.tool||'';
    var mode=step.mode||'series';
    var opts=ucToolNames.map(function(n){{
      return '<option value="'+ucEsc(n)+'"'+(n===tool?' selected':'')+'>'+ucEsc(n)+'</option>';
    }}).join('');
    return '<div class="pr-mcp-uc-card" data-uc-idx="'+idx+'">'
      +'<div class="pr-mcp-uc-card-head">'
      +'<span class="pr-mcp-uc-num">'+(idx+1)+'</span>'
      +'<select class="pr-mcp-uc-tool-sel">'+opts+'</select>'
      +'<select class="pr-mcp-uc-mode-sel">'
      +'<option value="series"'+(mode==='series'?' selected':'')+'>series</option>'
      +'<option value="parallel"'+(mode==='parallel'?' selected':'')+'>parallel</option>'
      +'</select>'
      +'<div class="pr-mcp-uc-card-actions">'
      +'<button type="button" class="pr-btn" data-uc-act="up" title="Move up">↑</button>'
      +'<button type="button" class="pr-btn" data-uc-act="down" title="Move down">↓</button>'
      +'<button type="button" class="pr-btn" data-uc-act="dup" title="Duplicate">Duplicate</button>'
      +'<button type="button" class="pr-btn pr-btn-danger" data-uc-act="rm" title="Remove">Remove</button>'
      +'</div></div>'
      +ucArgsEditorHtml(tool, step.arguments||{{}})
      +'</div>';
  }}
  function renderUcSteps(){{
    var el=document.getElementById('pr-mcp-uc-steps');
    var sum=document.getElementById('pr-mcp-uc-summary');
    if(sum) sum.textContent=ucSentence();
    if(!el) return;
    if(!ucSteps.length){{ el.textContent='No steps yet.'; return; }}
    var groups=ucGroupSteps(ucSteps);
    el.innerHTML=groups.map(function(g){{
      var cards=g.indices.map(function(i){{ return ucCardHtml(ucSteps[i], i); }}).join('');
      if(g.kind==='parallel'){{
        var label=g.indices.length>1?'Parallel group (concurrent)':'Parallel (solo — runs like series until paired)';
        return '<div class="pr-mcp-uc-group is-parallel"><div class="pr-mcp-uc-group-label">'+label+'</div>'+cards+'</div>';
      }}
      return '<div class="pr-mcp-uc-group">'+cards+'</div>';
    }}).join('');
  }}
  function ucLoadCommand(name){{
    var cmd=ucSavedCommands[name];
    if(!cmd){{ alert('Command not found: '+name); return; }}
    var nameEl=document.getElementById('pr-mcp-uc-name');
    var descEl=document.getElementById('pr-mcp-uc-desc');
    if(nameEl) nameEl.value=cmd.name||name;
    if(descEl) descEl.value=cmd.description||'';
    ucSteps=(cmd.steps||[]).map(function(s){{
      return {{tool:s.tool, arguments:(s.tool==='stop'?{{}}:(s.arguments&&typeof s.arguments==='object'?s.arguments:{{}})), mode:s.mode||'series'}};
    }});
    renderUcSteps();
    var dry=document.getElementById('pr-mcp-uc-dry');
    if(dry){{ dry.className='pr-mcp-uc-dry'; dry.textContent=''; }}
    var composer=document.getElementById('pr-mcp-user-composer');
    if(composer && composer.scrollIntoView) composer.scrollIntoView({{behavior:'smooth', block:'nearest'}});
  }}
  function ucApplyTemplate(kind){{
    if(kind==='status-doctor'){{
      var nameEl=document.getElementById('pr-mcp-uc-name');
      var descEl=document.getElementById('pr-mcp-uc-desc');
      if(nameEl && !nameEl.value.trim()) nameEl.value='status_doctor';
      if(descEl && !descEl.value.trim()) descEl.value='Status then doctor';
      ucSteps=[
        {{tool:'status', arguments:{{}}, mode:'series'}},
        {{tool:'doctor', arguments:{{}}, mode:'series'}}
      ];
    }} else if(kind==='apply-defaults'){{
      var nameEl2=document.getElementById('pr-mcp-uc-name');
      var descEl2=document.getElementById('pr-mcp-uc-desc');
      if(nameEl2 && !nameEl2.value.trim()) nameEl2.value='apply_defaults_once';
      if(descEl2 && !descEl2.value.trim()) descEl2.value='Apply default on/off states';
      ucSteps=[{{tool:'apply_defaults', arguments:{{}}, mode:'series'}}];
    }}
    renderUcSteps();
  }}
  var addStepBtn=document.getElementById('pr-mcp-uc-add-step');
  if(addStepBtn){{
    addStepBtn.addEventListener('click',function(){{
      ucSyncStepsFromDom();
      var tool=(document.getElementById('pr-mcp-uc-tool')||{{}}).value;
      var mode=(document.getElementById('pr-mcp-uc-mode')||{{}}).value||'series';
      if(!tool){{alert('Pick a system tool');return;}}
      if(ucSteps.length>=12){{alert('v1 max 12 steps');return;}}
      ucSteps.push({{tool:tool, arguments:ucDefaultArgs(tool), mode:mode}});
      renderUcSteps();
    }});
  }}
  var ucStepsRoot=document.getElementById('pr-mcp-uc-steps');
  if(ucStepsRoot){{
    ucStepsRoot.addEventListener('click',function(ev){{
      var btn=ev.target.closest('[data-uc-act]');
      if(!btn) return;
      var card=btn.closest('.pr-mcp-uc-card');
      if(!card) return;
      ucSyncStepsFromDom();
      var idx=Number(card.getAttribute('data-uc-idx'));
      if(isNaN(idx)) return;
      var act=btn.getAttribute('data-uc-act');
      if(act==='rm'){{ ucSteps.splice(idx,1); }}
      else if(act==='up' && idx>0){{ var t=ucSteps[idx-1]; ucSteps[idx-1]=ucSteps[idx]; ucSteps[idx]=t; }}
      else if(act==='down' && idx<ucSteps.length-1){{ var t2=ucSteps[idx+1]; ucSteps[idx+1]=ucSteps[idx]; ucSteps[idx]=t2; }}
      else if(act==='dup'){{
        if(ucSteps.length>=12){{alert('v1 max 12 steps');return;}}
        var copy=JSON.parse(JSON.stringify(ucSteps[idx]));
        ucSteps.splice(idx+1,0,copy);
      }}
      renderUcSteps();
    }});
    ucStepsRoot.addEventListener('change',function(ev){{
      var t=ev.target;
      if(!t) return;
      if(t.classList.contains('pr-mcp-uc-tool-sel')||t.classList.contains('pr-mcp-uc-mode-sel')||t.classList.contains('pr-mcp-uc-json-toggle')||t.hasAttribute('data-uc-arg')){{
        var card=t.closest('.pr-mcp-uc-card');
        if(t.classList.contains('pr-mcp-uc-json-toggle') && card){{
          var fields=card.querySelector('.pr-mcp-uc-fields');
          var ta=card.querySelector('.pr-mcp-uc-json');
          if(t.checked){{
            var args={{}};
            card.querySelectorAll('[data-uc-arg]').forEach(function(input){{
              var key=input.getAttribute('data-uc-arg');
              if(input.type==='checkbox'){{ if(input.checked) args[key]=true; return; }}
              var raw=(input.value||'').trim();
              if(raw==='') return;
              var n=Number(raw);
              if(input.type==='number' && raw!=='' && !isNaN(n)) args[key]=n;
              else args[key]=raw;
            }});
            if(ta) ta.value=JSON.stringify(args,null,2);
            if(fields) fields.hidden=true;
            if(ta) ta.hidden=false;
          }} else {{
            // parse JSON back into step then re-render fields
            var toolSel=card.querySelector('.pr-mcp-uc-tool-sel');
            var tool=toolSel?toolSel.value:'';
            var idx=Number(card.getAttribute('data-uc-idx'));
            var parsed={{}};
            try{{ parsed=JSON.parse((ta&&ta.value)||'{{}}'); }}catch(e){{ alert('Invalid JSON'); t.checked=true; return; }}
            if(!parsed || typeof parsed!=='object' || Array.isArray(parsed)){{ alert('JSON must be an object'); t.checked=true; return; }}
            if(tool==='stop') parsed={{}};
            if(!isNaN(idx) && ucSteps[idx]){{
              ucSteps[idx].arguments=parsed;
              ucSteps[idx].tool=tool;
              var modeSel=card.querySelector('.pr-mcp-uc-mode-sel');
              if(modeSel) ucSteps[idx].mode=modeSel.value||'series';
            }}
            renderUcSteps();
            return;
          }}
        }}
        if(t.classList.contains('pr-mcp-uc-tool-sel')){{
          ucSyncStepsFromDom();
          var idx=Number(card.getAttribute('data-uc-idx'));
          if(!isNaN(idx) && ucSteps[idx]){{
            ucSteps[idx].tool=t.value;
            ucSteps[idx].arguments=ucDefaultArgs(t.value);
          }}
          renderUcSteps();
          return;
        }}
        ucSyncStepsFromDom();
        var sum=document.getElementById('pr-mcp-uc-summary');
        if(sum) sum.textContent=ucSentence();
      }}
    }});
    ucStepsRoot.addEventListener('input',function(ev){{
      if(ev.target && (ev.target.hasAttribute('data-uc-arg')||ev.target.classList.contains('pr-mcp-uc-json'))){{
        // live summary doesn't need args; keep soft
      }}
    }});
  }}
  var tpl1=document.getElementById('pr-mcp-uc-tpl-status-doctor');
  if(tpl1) tpl1.addEventListener('click',function(){{ ucApplyTemplate('status-doctor'); }});
  var tpl2=document.getElementById('pr-mcp-uc-tpl-apply-defaults');
  if(tpl2) tpl2.addEventListener('click',function(){{ ucApplyTemplate('apply-defaults'); }});
  var clearBtn=document.getElementById('pr-mcp-uc-clear');
  if(clearBtn) clearBtn.addEventListener('click',function(){{
    ucSteps=[];
    var nameEl=document.getElementById('pr-mcp-uc-name');
    var descEl=document.getElementById('pr-mcp-uc-desc');
    if(nameEl) nameEl.value='';
    if(descEl) descEl.value='';
    renderUcSteps();
    var dry=document.getElementById('pr-mcp-uc-dry');
    if(dry){{ dry.className='pr-mcp-uc-dry'; dry.textContent=''; }}
  }});
  var dryBtn=document.getElementById('pr-mcp-uc-dry-run');
  if(dryBtn){{
    dryBtn.addEventListener('click',function(){{
      ucSyncStepsFromDom();
      if(!ucSteps.length){{alert('Add at least one step');return;}}
      dryBtn.disabled=true;
      postAction({{action:'mcp-user-command-dry-run', steps:ucSteps}})
        .then(function(result){{
          dryBtn.disabled=false;
          var body=result.body||{{}};
          var dry=document.getElementById('pr-mcp-uc-dry');
          if(!dry) return;
          var lines=[];
          lines.push(body.ok?'Dry-run OK — no tools were executed.':'Dry-run found issues — nothing was executed.');
          if(body.summary) lines.push(body.summary);
          (body.steps||[]).forEach(function(s,i){{
            var mark=s.ok?'✓':'✗';
            lines.push(mark+' '+(i+1)+'. '+s.tool+' ['+s.mode+']'+(s.message?(' — '+s.message):''));
          }});
          (body.groups||[]).forEach(function(g,i){{
            if(g.kind==='parallel' && g.tools && g.tools.length>1){{
              lines.push('Group '+(i+1)+': parallel {{'+g.tools.join(', ')+'}}');
            }}
          }});
          dry.textContent=lines.join('\\n');
          dry.className='pr-mcp-uc-dry is-open'+(body.ok?'':' is-err');
        }})
        .catch(function(){{ dryBtn.disabled=false; alert('Dry-run failed'); }});
    }});
  }}
  renderUcSteps();
  function applyPortskillServeStatus(body){{
    if(!body)return;
    var chip=document.getElementById('pr-ps-serve-chip');
    var wrap=document.getElementById('pr-ps-serve-wrap');
    var state=body.state|| (body.enabled||body.active ? 'serving' : 'off');
    var url=body.serve_url||'';
    var label=body.chip||'Off';
    var shortPlain='Off';
    if(state==='serving' && url) shortPlain='Serving';
    else if(state==='binary_missing') shortPlain='missing';
    else if(state==='needs_login') shortPlain='needs login';
    else if(state==='no_listen') shortPlain='no listen';
    else shortPlain=label;
    if(chip){{
      chip.setAttribute('data-state', state);
      chip.setAttribute('data-short', shortPlain);
      var klass='pr-ps-serve-chip';
      if(state==='serving')klass+=' is-on';
      else if(state==='binary_missing')klass+=' is-missing';
      else if(state==='needs_login'||state==='no_listen')klass+=' is-warn';
      chip.className=klass;
      chip.title=body.message||label;
      if(state==='serving' && url){{
        chip.innerHTML='<a class="pr-port-link pr-serve-url-text" href="'+url+'" target="_blank" rel="noopener" style="color:inherit">'+url+'</a>';
      }} else if(state==='binary_missing'){{
        chip.textContent='Serve: binary missing';
      }} else if(state==='needs_login'){{
        chip.textContent='Serve: needs login';
      }} else if(state==='no_listen'){{
        chip.textContent='Serve: no listen port';
      }} else {{
        chip.textContent='Serve: '+label;
      }}
    }}
    if(wrap){{
      var copy=document.getElementById('pr-serve-copy');
      var det=document.getElementById('pr-serve-url-details');
      if(state==='serving' && url){{
        if(!copy){{
          copy=document.createElement('button');
          copy.type='button';
          copy.className='pr-btn pr-serve-copy';
          copy.id='pr-serve-copy';
          copy.textContent='Copy URL';
          wrap.appendChild(copy);
        }}
        copy.setAttribute('data-serve-url', url);
        copy.setAttribute('title', 'Copy Serve URL');
        copy.setAttribute('aria-label', 'Copy Serve URL');
        if(!det){{
          det=document.createElement('details');
          det.className='pr-serve-url-details';
          det.id='pr-serve-url-details';
          det.innerHTML='<summary>Serve URL</summary><code id="pr-serve-url-full"></code>';
          wrap.appendChild(det);
        }}
        var full=document.getElementById('pr-serve-url-full');
        if(full) full.textContent=url;
      }} else {{
        if(copy) copy.remove();
        if(det) det.remove();
      }}
    }}
    var tog=document.querySelector('[data-pr-action="serve-portskill"]');
    if(tog && typeof body.enabled_preference==='boolean'){{
      tog.checked=!!body.enabled_preference;
    }} else if(tog && typeof body.enabled==='boolean'){{
      tog.checked=!!body.enabled;
    }}
  }}
  document.addEventListener('click', function(ev){{
    var btn=ev.target && ev.target.closest && ev.target.closest('#pr-serve-copy, .pr-serve-copy');
    if(!btn) return;
    var u=btn.getAttribute('data-serve-url')||'';
    if(!u) return;
    ev.preventDefault();
    function ok(){{ var t=btn.textContent; btn.textContent='Copied'; setTimeout(function(){{ btn.textContent=t; }}, 1200); }}
    if(navigator.clipboard && navigator.clipboard.writeText){{
      navigator.clipboard.writeText(u).then(ok).catch(function(){{
        prompt('Copy Serve URL', u);
      }});
    }} else {{
      prompt('Copy Serve URL', u);
    }}
  }});
  (function(){{
    /* Disclosures: default-collapsed (System tools + repo sections). */
    var d=document.getElementById('pr-mcp-system-details');
    if(d){{ d.open=false; }}
    var hd=document.getElementById('pr-handoff-details');
    if(hd){{ hd.open=false; }}
    document.querySelectorAll('details.pr-project').forEach(function(el){{ el.open=false; }});
  }})();
  function applyTailscaleStatus(body){{
    if(!body)return;
    var chip=document.getElementById('pr-ts-chip');
    var label=body.chip||'Needs login';
    var state=body.state||'needs_login';
    if(chip){{
      chip.textContent='Tailscale: '+label;
      chip.title=body.message||label;
      var klass='pr-ts-needs';
      if(state==='connected'||label==='Connected')klass='pr-ts-connected';
      else if(state==='binary_missing'||label==='Binary missing')klass='pr-ts-missing';
      else if(state==='pending'||label==='Checking…')klass='pr-ts-pending';
      chip.className='pr-ts-chip '+klass;
    }}
    var slot=document.getElementById('pr-ts-login-slot');
    if(slot){{
      if(state==='connected'||label==='Connected'||body.logged_in){{
        slot.innerHTML='';
      }} else if(state==='binary_missing'||label==='Binary missing'){{
        slot.innerHTML='<span class="tag" id="pr-ts-login-hint" style="color:var(--muted)">Install Tailscale to use Serve</span>';
      }} else if(state==='pending'){{
        slot.innerHTML='';
      }} else {{
        if(!slot.querySelector('[data-pr-action="tailscale-login"]')){{
          slot.innerHTML='<button type="button" class="pr-btn" id="pr-ts-login" data-pr-action="tailscale-login" title="Open Tailscale sign-in in your browser">Browser Login</button>';
        }}
      }}
    }}
    var outSlot=document.getElementById('pr-ts-logout-slot');
    if(outSlot){{
      if(state==='binary_missing'||label==='Binary missing'||state==='pending'){{
        outSlot.innerHTML='';
      }} else if(!outSlot.querySelector('[data-pr-action="tailscale-logout"]')){{
        outSlot.innerHTML='<button type="button" class="pr-btn" id="pr-ts-logout" data-pr-action="tailscale-logout" title="Log out of Tailscale">Logout</button>';
      }}
    }}
    var urls=body.port_urls||{{}};
    Object.keys(urls).forEach(function(rid){{
      var href=urls[rid];
      if(!href)return;
      document.querySelectorAll('a.pr-port-link[data-range-id="'+rid+'"]').forEach(function(a){{
        a.setAttribute('href', href);
        a.setAttribute('title', href);
      }});
    }});
    if(body.portskill_serve){{applyPortskillServeStatus(body.portskill_serve);}}
  }}
  function refreshTailscaleStatus(){{
    return fetch('/api/tailscale-status')
      .then(function(res){{return res.json();}})
      .then(function(body){{applyTailscaleStatus(body);return body;}})
      .catch(function(){{}});
  }}
  if(document.readyState==='loading'){{
    document.addEventListener('DOMContentLoaded', function(){{refreshTailscaleStatus();}});
  }} else {{
    refreshTailscaleStatus();
  }}

}})();
</script>
<!-- Iterate launcher hidden: <script src="/static/iterate.js" defer></script> -->
<footer class="pr-mcp-footer" style="position:fixed;bottom:0;left:0;right:0;padding:6px 14px;
background:#fafbf9;border-top:1px solid var(--line);font-size:11px;color:var(--muted);
font-family:ui-monospace,Menlo,monospace;z-index:20">
  MCP: <a href="{mcp_footer_url}" style="color:var(--cobalt)">{mcp_footer_label}</a>
  · listen: <span title="Sticky broadcast">{mcp_listen_path}</span>
</footer>
</body>
</html>"""


def run_cli_action(project: str, range_id: str, action: str) -> tuple[int, dict]:
    argv = [action, "--project", project, "--range-id", range_id]
    if action == "start":
        argv.extend(["--tailnet", "none"])
    code, payload, stdout = run_cli(argv)
    if code == 0:
        return 0, {"ok": True, "result": payload}
    if code == 3:
        body = {"ok": False, "needs_input": True}
        if isinstance(payload, dict):
            body.update(payload)
            body["ok"] = False
            body["needs_input"] = True
        else:
            body["message"] = stdout or "needs_input"
        return 3, body
    message = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("reason")
    if not message:
        message = stdout or f"CLI exited {code}"
    body = {"ok": False, "message": message}
    if isinstance(payload, dict):
        body["result"] = payload
    return code, body


def _cli_result(code: int, payload, stdout: str) -> tuple[int, dict]:
    if code == 0:
        return 0, {"ok": True, "result": payload}
    message = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("reason")
    if not message:
        message = stdout or f"CLI exited {code}"
    body = {"ok": False, "message": message}
    if isinstance(payload, dict):
        body["result"] = payload
    return code if code else 422, body


def dispatch_ui_action(body: dict) -> tuple[int, dict]:
    action = body.get("action")
    if action in ("start", "stop", "release"):
        project = body.get("project")
        range_id = body.get("rangeId")
        if not isinstance(project, str) or not isinstance(range_id, str):
            return 400, {"ok": False, "message": "expected project and rangeId"}
        return run_cli_action(project, range_id, action)
    if action == "history-restore":
        index = body.get("index")
        try:
            index = int(index)
        except (TypeError, ValueError):
            return 400, {"ok": False, "message": "expected index"}
        argv = ["history", "restore", "--index", str(index)]
        env = body.get("environment") or body.get("name")
        if isinstance(env, str) and env.strip():
            argv += ["--environment", env.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "history-reset":
        argv = ["history", "reset"]
        env = body.get("environment") or body.get("name")
        if isinstance(env, str) and env.strip():
            argv += ["--environment", env.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "history-clear":
        argv = ["history", "clear"]
        env = body.get("environment") or body.get("name")
        if isinstance(env, str) and env.strip():
            argv += ["--environment", env.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "set-default":
        project = body.get("project")
        range_id = body.get("rangeId")
        state = body.get("state")
        if not isinstance(range_id, str) or state not in ("on", "off"):
            return 400, {"ok": False, "message": "expected rangeId and state on|off"}
        argv = ["set-default", "--range-id", range_id, "--state", state]
        if isinstance(project, str) and project:
            argv += ["--project", project]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "apply-defaults":
        argv = ["apply-defaults"]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action in ("exit-house", "deactivate"):
        argv = ["deactivate"]
        preset = body.get("preset")
        if isinstance(preset, str) and preset.strip():
            argv += ["--preset", preset.strip()]
        if body.get("alsoRelease") or body.get("also_release"):
            argv.append("--also-release")
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "compat-check":
        argv = ["compat", "check"]
        presets = body.get("presets") or body.get("names") or []
        if isinstance(presets, str) and presets.strip():
            presets = [p.strip() for p in presets.split(",") if p.strip()]
        if isinstance(presets, list):
            for name in presets:
                if isinstance(name, str) and name.strip():
                    argv += ["--preset", name.strip()]
        if len(argv) == 2:
            argv.append("--include-default-on")
        code, payload, stdout = run_cli(argv)
        # Always return 200 with report so UI can show conflicts without hard fail chrome
        if isinstance(payload, dict):
            return 0, {"ok": True, "compatReport": payload, "result": payload}
        return _cli_result(code, payload, stdout)
    if action == "export-environment":
        name = body.get("name") or "environment"
        if not isinstance(name, str) or not name.strip():
            name = "environment"
        argv = ["environment", "export", "--name", name.strip()]
        code, payload, stdout = run_cli(argv)
        if code != 0:
            return _cli_result(code, payload, stdout)
        # stdout is the environment JSON document
        download = stdout if stdout else (json.dumps(payload, indent=2) if payload else "{}")
        filename = f"{name.strip().replace(' ', '-')}.json"
        return 0, {"ok": True, "download": download, "filename": filename, "result": payload}
    if action == "import-environment":
        import tempfile

        content = body.get("content")
        path = body.get("path")
        tmp_path = None
        if isinstance(content, str) and content.strip():
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            )
            tmp.write(content)
            tmp.close()
            tmp_path = tmp.name
            file_arg = tmp_path
        elif isinstance(path, str) and path.strip():
            file_arg = path.strip()
        else:
            return 400, {"ok": False, "message": "expected content or path for import"}
        try:
            argv = ["environment", "import", "--file", file_arg]
            code, payload, stdout = run_cli(argv)
            return _cli_result(code, payload, stdout)
        finally:
            if tmp_path:
                try:
                    pathlib.Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
    if action == "preset-save":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["preset", "save", "--name", name.strip()]
        desc = body.get("description")
        if isinstance(desc, str) and desc:
            argv += ["--description", desc]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "preset-apply":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["preset", "apply", "--name", name.strip()]
        if body.get("alsoStopOff"):
            argv.append("--also-stop-off")
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "preset-delete":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["preset", "delete", "--name", name.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "settings-save":
        argv = ["settings", "set"]
        if "autoApplyOnLaunch" in body:
            argv += ["--auto-apply-on-launch", "on" if body.get("autoApplyOnLaunch") else "off"]
        preset = body.get("autoApplyPreset")
        if preset is None or preset == "":
            argv += ["--auto-apply-preset", "none"]
        elif isinstance(preset, str):
            argv += ["--auto-apply-preset", preset]
        if "autoExitOnShutdown" in body:
            argv += [
                "--auto-exit-on-shutdown",
                "on" if body.get("autoExitOnShutdown") else "off",
            ]
        # Always force require_compat on
        argv += ["--require-compat", "on"]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "serve-portskill":
        enabled = body.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        else:
            enabled = bool(enabled)
        if enabled:
            code, payload, stdout = run_cli(["tailscale", "status"])
            logged = isinstance(payload, dict) and payload.get("logged_in")
            if isinstance(payload, dict) and payload.get("state") == "binary_missing":
                return 400, {
                    "ok": False,
                    "reason": "tailscale_bin_missing",
                    "message": (payload.get("message") or "Tailscale binary not found"),
                    "chip": "Binary missing",
                    "state": "binary_missing",
                }
            if not logged:
                msg = "Tailscale Serve needs Browser Login first."
                if isinstance(payload, dict) and payload.get("message"):
                    msg = payload.get("message")
                return 401, {
                    "ok": False,
                    "needs_login": True,
                    "reason": "tailscale_auth_required",
                    "message": msg,
                    "chip": (payload or {}).get("chip") if isinstance(payload, dict) else "Needs login",
                    "auth_url": (payload or {}).get("AuthURL") if isinstance(payload, dict) else None,
                    "pendingPortskillServe": True,
                }
        argv = ["tailscale", "serve-portskill", "--state", "on" if enabled else "off"]
        code, payload, stdout = run_cli(argv)
        if code != 0:
            body_out = {"ok": False, "result": payload}
            if isinstance(payload, dict):
                body_out.update(payload)
                body_out["ok"] = False
                body_out.setdefault("message", payload.get("message") or payload.get("reason") or stdout)
            else:
                body_out["message"] = stdout or f"serve-portskill exited {code}"
            return code if code else 400, body_out
        body_out = dict(payload) if isinstance(payload, dict) else {"status": "ok"}
        body_out["ok"] = True
        body_out["enabled"] = enabled
        body_out["enabled_preference"] = enabled
        try:
            status = probe_portskill_serve_status()
            for k, v in status.items():
                body_out.setdefault(k, v)
        except Exception:  # noqa: BLE001
            pass
        return 0, body_out

    if action == "mcp-user-command-dry-run":
        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            return 400, {"ok": False, "message": "expected non-empty steps"}
        if len(steps) > 12:
            return 400, {"ok": False, "message": "v1 max 12 steps"}
        result = dry_run_user_command_steps(steps)
        # dispatch treats 0 as HTTP 200
        return (0 if result.get("ok") else 422), result

    if action == "mcp-user-command-save":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            return 400, {"ok": False, "message": "name must be letters, numbers, _ or -"}
        system_names = {
            t.get("name")
            for t in TOOL_DEFS
            if isinstance(t, dict) and isinstance(t.get("name"), str)
        }
        if name in system_names:
            return 400, {"ok": False, "message": f"cannot shadow system tool: {name}"}
        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            return 400, {"ok": False, "message": "expected non-empty steps"}
        cleaned_steps = []
        for step in steps:
            if not isinstance(step, dict) or step.get("tool") not in system_names:
                return 400, {
                    "ok": False,
                    "message": f"each step.tool must be a system TOOL_DEFS name (got {step!r})",
                }
            tool = step.get("tool")
            mode = step.get("mode") or "series"
            arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            if tool == "stop":
                arguments = {}
            cleaned_steps.append({"tool": tool, "arguments": arguments, "mode": mode})
        cmd = {
            "name": name,
            "description": body.get("description") if isinstance(body.get("description"), str) else "",
            "steps": cleaned_steps,
        }
        import json as _json

        argv = [
            "settings",
            "set",
            "--mcp-user-command-upsert",
            _json.dumps(cmd),
            "--mcp-tool",
            f"{name}=on",
        ]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "mcp-user-command-delete":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        # CLI delete also drops orphan settings.mcp_tools[name]
        argv = ["settings", "set", "--mcp-user-command-delete", name.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "mcp-tool-set":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        enabled = body.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        elif enabled is None:
            return 400, {"ok": False, "message": "expected enabled bool"}
        else:
            enabled = bool(enabled)
        argv = [
            "settings",
            "set",
            "--mcp-tool",
            f"{name.strip()}={'on' if enabled else 'off'}",
        ]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "handoff-set-enabled":
        enabled = body.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        elif enabled is None:
            return 400, {"ok": False, "message": "expected enabled bool"}
        else:
            enabled = bool(enabled)
        argv = ["settings", "set", "--handoff-enabled", "on" if enabled else "off"]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "handoff-set-kit":
        path = body.get("path")
        if path is None:
            path = ""
        if not isinstance(path, str):
            return 400, {"ok": False, "message": "expected path"}
        token = path.strip() or "none"
        argv = ["settings", "set", "--handoff-kit", token]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)

    if action == "handoff-package":
        settings = load_registry().get("settings") or {}
        result = run_package_sh(settings)
        if result.get("ok"):
            out = dict(result)
            out["ok"] = True
            return 0, out
        return 422, result

    if action == "handoff-codex-install":
        settings = load_registry().get("settings") or {}
        result = run_codex_install(settings)
        if result.get("ok"):
            out = dict(result)
            out["ok"] = True
            return 0, out
        return 422, result

    if action == "set-tailnet":
        project = body.get("project")
        range_id = body.get("rangeId")
        mode = body.get("mode")
        if not isinstance(range_id, str) or mode not in ("serve", "none", "funnel"):
            return 400, {"ok": False, "message": "expected rangeId and mode serve|none|funnel"}
        if mode == "serve":
            code, payload, stdout = run_cli(["tailscale", "status"])
            logged = isinstance(payload, dict) and payload.get("logged_in")
            if not logged:
                msg = "Tailscale Serve needs Browser Login first."
                if isinstance(payload, dict) and payload.get("message"):
                    msg = payload.get("message")
                return 401, {
                    "ok": False,
                    "needs_login": True,
                    "reason": "tailscale_auth_required",
                    "message": msg,
                    "chip": (payload or {}).get("chip") if isinstance(payload, dict) else "Needs login",
                    "auth_url": (payload or {}).get("AuthURL") if isinstance(payload, dict) else None,
                    "pendingServe": {"project": project, "rangeId": range_id},
                }
        argv = ["set-tailnet", "--range-id", range_id, "--mode", mode]
        if isinstance(project, str) and project:
            argv += ["--project", project]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "tailscale-status":
        code, payload, stdout = run_cli(["tailscale", "status"])
        if isinstance(payload, dict):
            body_out = dict(payload)
            body_out["ok"] = True
            return 0, body_out
        return _cli_result(code, payload, stdout)
    if action == "tailscale-login":
        wait = body.get("wait", 0)
        try:
            wait = int(wait)
        except (TypeError, ValueError):
            wait = 0
        argv = ["tailscale", "login", "--wait", str(wait)]
        # UI opens the URL itself; avoid double-open from CLI when wait=0
        argv.append("--no-open")
        code, payload, stdout = run_cli(argv)
        if isinstance(payload, dict):
            out = dict(payload)
            out["ok"] = code == 0 or bool(out.get("logged_in") or out.get("already_logged_in"))
            if out.get("auth_url") or out.get("reason") in (
                "tailscale_auth_required",
                "tailscale_auth_pending",
                "tailscale_auth_timeout",
            ):
                out["needs_login"] = not bool(out.get("logged_in") or out.get("already_logged_in"))
            # Treat pending auth URL as non-fatal for UI poll flow
            if code == 3 and out.get("auth_url"):
                return 0, out
            if code == 0:
                return 0, out
            return code if code else 422, out
        return _cli_result(code, payload, stdout)
    if action == "new-environment":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["preset", "save", "--name", name.strip(), "--empty"]
        desc = body.get("description")
        if isinstance(desc, str) and desc:
            argv += ["--description", desc]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action in ("focus-environment", "open-environment-tab"):
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["settings", "set", "--open-tab", name.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "close-environment-tab":
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, {"ok": False, "message": "expected name"}
        argv = ["settings", "set", "--close-tab", name.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "discover-ports":
        code, payload, stdout = run_cli(["discover", "ports"])
        if code == 0 and isinstance(payload, dict):
            out = dict(payload)
            out["ok"] = True
            return 0, out
        return _cli_result(code, payload, stdout)
    if action == "discover-import":
        ports = body.get("ports")
        notes = body.get("notes") if isinstance(body.get("notes"), dict) else {}
        tailnet = body.get("tailnet")
        if tailnet == "funnel":
            tailnet = "none"
        project = body.get("project")
        results_imported = []
        results_skipped = []
        results_errors = []
        port_list = []
        if isinstance(ports, list) and ports:
            for p in ports:
                try:
                    port_list.append(int(p))
                except (TypeError, ValueError):
                    results_errors.append({"port": p, "reason": "invalid_port"})
        elif body.get("port") is not None:
            try:
                port_list.append(int(body.get("port")))
            except (TypeError, ValueError):
                return 400, {"ok": False, "message": "invalid port"}
        elif body.get("all_missing"):
            code, payload, stdout = run_cli(
                ["discover", "import", "--all-missing"]
                + (["--tailnet", str(tailnet)] if tailnet in ("serve", "none") else [])
                + (["--project", str(project)] if isinstance(project, str) and project.strip() else [])
            )
            return _cli_result(code, payload, stdout)
        else:
            return 400, {"ok": False, "message": "expected ports[] or port"}
        for p in port_list:
            argv = ["discover", "import", "--port", str(p)]
            if isinstance(notes, dict):
                note = notes.get(str(p)) or notes.get(p) or body.get("note")
            else:
                note = body.get("note")
            if isinstance(note, str) and note.strip():
                argv += ["--note", note.strip()]
            if tailnet in ("serve", "none"):
                argv += ["--tailnet", str(tailnet)]
            if isinstance(project, str) and project.strip():
                argv += ["--project", project.strip()]
            code, payload, stdout = run_cli(argv)
            if code == 0 and isinstance(payload, dict):
                results_imported.extend(payload.get("imported") or [])
                results_skipped.extend(payload.get("skipped") or [])
            else:
                results_errors.append({"port": p, "payload": payload, "stdout": (stdout or "")[:300]})
        out = {
            "status": "ok" if not results_errors else "partial",
            "ok": not bool(results_errors) or bool(results_imported),
            "imported": results_imported,
            "skipped": results_skipped,
            "errors": results_errors,
            "count_imported": len(results_imported),
        }
        return (0 if out["ok"] else 422), out
    if action == "allocate":
        count = body.get("count") or 1
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        project = body.get("project") or "."
        argv = ["allocate", "--count", str(count), "--project", str(project), "--tailnet", str(body.get("tailnet") or "none")]
        note = body.get("note")
        if isinstance(note, str) and note:
            argv += ["--note", note]
        machine = body.get("machine") or body.get("machineId") or body.get("machine_id")
        if isinstance(machine, str) and machine.strip():
            argv += ["--machine", machine.strip()]
        host = body.get("host")
        if isinstance(host, str) and host.strip():
            argv += ["--host", host.strip()]
        scheme = body.get("scheme")
        if isinstance(scheme, str) and scheme.strip():
            argv += ["--scheme", scheme.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "start-all":
        code, payload, stdout = run_cli(["start", "--all"])
        return _cli_result(code, payload, stdout)
    if action == "stop-all":
        code, payload, stdout = run_cli(["stop", "--all"])
        return _cli_result(code, payload, stdout)
    if action == "stop-non-default":
        code, payload, stdout = run_cli(["stop", "--non-default"])
        return _cli_result(code, payload, stdout)
    if action in ("set-defaults-from-current", "capture-defaults"):
        code, payload, stdout = run_cli(["set-defaults-from-current"])
        return _cli_result(code, payload, stdout)
    if action == "tailscale-logout":
        code, payload, stdout = run_cli(["tailscale", "logout"])
        if isinstance(payload, dict):
            out = dict(payload)
            out["ok"] = code == 0
            return (0 if code == 0 else (code or 422)), out
        return _cli_result(code, payload, stdout)
    if action == "edit-mode":
        # Global edit-mode kept for CLI; UI no longer uses it.
        return 200, {"ok": True, "ignored": True, "message": "edit-mode is CLI-only; use per-card Edit"}
    if action == "draft-stage":
        range_id = body.get("rangeId")
        if not isinstance(range_id, str) or not range_id.strip():
            return 400, {"ok": False, "message": "expected rangeId"}
        argv = ["workspace", "draft-set", "--range-id", range_id.strip()]
        mapping = [
            ("startScript", "--start-script"),
            ("start_script", "--start-script"),
            ("stopScript", "--stop-script"),
            ("stop_script", "--stop-script"),
            ("command", "--command"),
            ("cwd", "--cwd"),
        ]
        seen = set()
        for key, flag in mapping:
            if flag in seen:
                continue
            val = body.get(key)
            if isinstance(val, str):
                argv += [flag, val]
                seen.add(flag)
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "draft-save":
        argv = ["workspace", "draft-save"]
        range_id = body.get("rangeId")
        if isinstance(range_id, str) and range_id.strip():
            argv += ["--range-id", range_id.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action == "draft-revert":
        argv = ["workspace", "draft-revert"]
        range_id = body.get("rangeId")
        if isinstance(range_id, str) and range_id.strip():
            argv += ["--range-id", range_id.strip()]
        code, payload, stdout = run_cli(argv)
        return _cli_result(code, payload, stdout)
    if action in ("machine-list", "machine-add", "machine-remove"):
        return 400, {"ok": False, "message": "Remote machines — Coming soon"}
    return 400, {"ok": False, "message": f"unsupported action: {action}"}


class Handler(BaseHTTPRequestHandler):
    server_version = f"PortskillUI/{__version__}"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._send(status, raw, "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        # Static assets (keep trailing filename; do not strip extension via rstrip alone)
        raw_path = parsed.path or "/"
        if raw_path.startswith("/static/"):
            rel = raw_path[len("/static/") :]
            if serve_static_file(self, rel):
                return
            self._send_json(404, {"ok": False, "message": "static not found"})
            return
        if path == "/mcp":
            self._send_json(200, discovery_payload())
            return
        if path in ("/", "/port-registry"):
            raw = load_registry()
            # Fast first paint: no Tailscale probes; local 127.0.0.1 links OK until async refresh
            # One workspace: show all services (no focused-preset filter)
            view = build_view(raw)
            ts = {"chip": "Checking…", "state": "pending", "logged_in": False}
            page = render_page(view, tailscale=ts).encode("utf-8")
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/tailscale-status":
            self._send_json(200, api_tailscale_status_payload())
            return
        if path == "/api/state":
            self._send_json(200, load_registry())
            return
        if path == "/iterate/state":
            self._send_json(200, iterate_state_payload())
            return
        if path == "/iterate/collab/messages":
            self._send_json(200, {"ok": True, "messages": iterate_read_collab()})
            return
        self._send_json(404, {"ok": False, "message": "not found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/mcp":
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b""
            try:
                message = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(
                    400,
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}},
                )
                return
            if isinstance(message, list):
                responses = []
                for item in message:
                    resp = mcp_handle(item)
                    if resp is not None:
                        responses.append(resp)
                self._send_json(200, responses)
                return
            resp = mcp_handle(message)
            if resp is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_json(200, resp)
            return
        # Iterate Mode endpoints (local package writes / collab inbox)
        if path in (
            "/iterate/working-set",
            "/iterate/persist",
            "/iterate/collab/send",
        ):
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "message": f"invalid JSON: {exc}"})
                return
            if not isinstance(body, dict):
                self._send_json(400, {"ok": False, "message": "expected JSON object"})
                return
            if path == "/iterate/working-set":
                result = iterate_update_working_set(body)
                self._send_json(200 if result.get("ok") else 400, result)
                return
            if path == "/iterate/persist":
                result = iterate_persist_tokens(body)
                self._send_json(200 if result.get("ok") else 400, result)
                return
            if path == "/iterate/collab/send":
                text_msg = body.get("text")
                if not isinstance(text_msg, str) or not text_msg.strip():
                    self._send_json(400, {"ok": False, "message": "text required"})
                    return
                role = body.get("role") if isinstance(body.get("role"), str) else "ben"
                try:
                    msg = iterate_append_collab(role.strip() or "ben", text_msg.strip())
                except OSError as exc:
                    self._send_json(500, {"ok": False, "message": f"collab write failed: {exc}"})
                    return
                self._send_json(
                    200,
                    {"ok": True, "message": msg, "messages": iterate_read_collab()},
                )
                return

        if path != "/port-registry/actions":
            self._send_json(404, {"ok": False, "message": "not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                400,
                {
                    "ok": False,
                    "message": "expected JSON body with action",
                },
            )
            return
        if not isinstance(body, dict):
            self._send_json(400, {"ok": False, "message": "expected JSON object"})
            return
        action = body.get("action")
        if not isinstance(action, str) or not action.strip():
            self._send_json(400, {"ok": False, "message": "expected action"})
            return
        code, result = dispatch_ui_action(body)
        if code == 0:
            self._send_json(200, result)
        elif code == 400:
            self._send_json(400, result)
        elif code == 401:
            self._send_json(401, result)
        else:
            self._send_json(422, result)


# Back-compat alias used by older imports
AppHandler = Handler


def run_launch_auto_apply() -> None:
    """If settings enable it, apply the configured preset once before serving."""
    from .cli import maybe_auto_apply_on_launch

    try:
        result = maybe_auto_apply_on_launch()
    except SystemExit as exc:
        print(
            f"auto-apply: skipped due to registry error (exit {getattr(exc, 'code', '?')})",
            flush=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(f"auto-apply: error {exc}", flush=True)
        return
    if result is None:
        print("auto-apply: disabled", flush=True)
        return
    status = result.get("status")
    preset = result.get("preset") or (result.get("settings") or {}).get("auto_apply_preset")
    apply = result.get("apply_defaults") or {}
    warnings = result.get("warnings") or []
    print(
        json.dumps(
            {
                "event": "auto_apply",
                "status": status,
                "preset": preset,
                "imported": result.get("imported"),
                "failed": result.get("failed"),
                "started_count": apply.get("started_count"),
                "skipped_count": apply.get("skipped_count"),
                "error_count": apply.get("error_count"),
                "warning_count": len(warnings),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if warnings:
        print(
            f"auto-apply: {len(warnings)} warning(s) (placeholder/start issues collected)",
            flush=True,
        )


def serve_http(
    host: str,
    port: int | None = None,
    open_browser: bool = True,
    auto_apply: bool = True,
    *,
    explicit: bool = False,
) -> int:
    """Bind UI+MCP. If not explicit, sticky/allocate a port; write listen.json after bind."""
    global _ACTIVE_LISTEN
    if auto_apply:
        run_launch_auto_apply()
    else:
        print("auto-apply: skipped (--no-auto-apply)", flush=True)

    source = "explicit" if explicit and port is not None else "pending"
    if explicit and port is not None:
        candidates = [int(port)]
    else:
        try:
            chosen, source = select_listen_port(host)
        except OSError as exc:
            print(f"port selection failed: {exc}", flush=True)
            return 1
        candidates = [chosen]

    server = None
    bound_port = None
    last_err: OSError | None = None
    attempts = 0
    while attempts < BIND_RETRIES:
        attempts += 1
        if not candidates:
            try:
                nxt, source = select_listen_port(host)
                candidates = [nxt]
            except OSError as exc:
                last_err = exc  # type: ignore[assignment]
                break
        try_port = int(candidates.pop(0))
        try:
            server = ThreadingHTTPServer((host, try_port), Handler)
            bound_port = try_port
            break
        except OSError as exc:
            last_err = exc
            print(
                f"bind {host}:{try_port} failed ({exc}); "
                f"{'no retry (explicit port)' if explicit else 'selecting another port…'}",
                flush=True,
            )
            if explicit:
                break
            # Prefer another dogfood/pool port
            alt = _find_os_free_pool_port(host)
            if alt is not None and alt not in candidates:
                claimed = _dogfood_allocate_port(prefer_start=alt)
                candidates.append(claimed if claimed is not None else alt)
            else:
                try:
                    nxt, source = select_listen_port(host)
                    if nxt not in candidates:
                        candidates.append(nxt)
                except OSError:
                    pass

    if server is None or bound_port is None:
        print(f"Portskill failed to bind after {attempts} attempt(s): {last_err}", flush=True)
        return 1

    listen_payload = build_listen_payload(host, bound_port)
    listen_payload["port_source"] = source
    try:
        write_listen_file(listen_payload)
    except OSError as exc:
        print(f"listen.json write failed: {exc}", flush=True)
    _ACTIVE_LISTEN = dict(listen_payload)
    try:
        from . import mcp as mcp_mod

        if hasattr(mcp_mod, "set_listen_runtime"):
            mcp_mod.set_listen_runtime(listen_payload)
    except Exception:  # noqa: BLE001
        pass

    print_listen_banner(listen_payload)
    print(f"port_source: {source}", flush=True)
    host_warn = bind_host_warning(host)
    if host_warn:
        print(f"WARNING: {host_warn}", flush=True)
    try:
        settings = (load_registry().get("settings") or {})
        if settings.get("serve_portskill_on_tailscale"):
            print(
                f"serve_portskill_on_tailscale: re-applying Tailscale Serve for :{bound_port}",
                flush=True,
            )
            apply_portskill_tailscale_serve(True, persist=True, port=bound_port)
    except Exception as exc:  # noqa: BLE001 — soft-fail; chip will show state
        print(f"serve_portskill_on_tailscale re-apply soft-failed: {exc}", flush=True)

    url = listen_payload["ui_url"]
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — best-effort only
            pass

    shutting_down = {"done": False}

    def _maybe_auto_exit_house() -> None:
        try:
            settings = (load_registry().get("settings") or {})
            if not settings.get("auto_exit_on_shutdown"):
                return
            print("auto_exit_on_shutdown: running deactivate…", flush=True)
            code, payload, stdout = run_cli(["deactivate"])
            if code == 0:
                print(f"deactivate ok: {payload or stdout}", flush=True)
            else:
                print(f"deactivate best-effort failed ({code}): {payload or stdout}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"deactivate best-effort error: {exc}", flush=True)

    def _shutdown_handler(signum, frame):  # noqa: ARG001
        if shutting_down["done"]:
            return
        shutting_down["done"] = True
        print(f"\nSignal {signum}: shutting down.", flush=True)
        mark_listen_stopped()
        _maybe_auto_exit_house()
        raise KeyboardInterrupt

    prev_int = signal.signal(signal.SIGINT, _shutdown_handler)
    prev_term = signal.signal(signal.SIGTERM, _shutdown_handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not shutting_down["done"]:
            shutting_down["done"] = True
            print("\nShutting down.", flush=True)
            mark_listen_stopped()
            _maybe_auto_exit_house()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        try:
            mark_listen_stopped()
        except Exception:  # noqa: BLE001
            pass
        server.server_close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="portskill",
        description="Portskill: light UI + MCP port registry (stdlib)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mcp-stdio",
        action="store_true",
        help="Run MCP server over stdin/stdout (no HTTP)",
    )
    mode.add_argument(
        "--ui",
        action="store_true",
        help="HTTP UI + /mcp endpoint (default when not --mcp-stdio)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Bind port (explicit; wins over sticky/allocate). "
            "Also: env PORTSKILL_PORT or PORT_REGISTRY_APP_PORT. "
            "Default: sticky ~/.config/port-registry/listen.json or dogfood allocate."
        ),
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser tab on launch",
    )
    parser.add_argument(
        "--no-auto-apply",
        action="store_true",
        help="Skip auto-apply preset on this launch even if settings enable it",
    )
    args = parser.parse_args(argv)
    if args.mcp_stdio:
        return mcp_stdio_loop()
    explicit_port = resolve_explicit_port(args.port)
    return serve_http(
        args.host,
        explicit_port,
        open_browser=not args.no_open,
        auto_apply=not args.no_auto_apply,
        explicit=explicit_port is not None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
