#!/usr/bin/env python3
from __future__ import annotations
import argparse
from . import __version__
from . import discover_ports as discover_ports_mod
from .handoff import doctor_handoff
import copy
import datetime
import fcntl
import json
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import time
import uuid
import urllib.error
import urllib.request
import webbrowser


VERSION = 1
DEFAULT_REGISTRY_PATH = "~/.config/port-registry/registry.json"
LISTEN_FILENAME = "listen.json"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# Doctor exit contract (main): 0 = healthy / informational warnings; 2 = fail-closed.
# Hard checks only; informational checks never flip the exit code.
# scripts/doctor.sh execs the same CLI and must return these codes.
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
DEFAULT_TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
DEFAULT_POOL_START = 20000
DEFAULT_POOL_END = 29999
DEFAULT_LIFECYCLE_DIR = ".port-registry"
DEFAULT_START_SCRIPT = ".port-registry/start.sh"
DEFAULT_STOP_SCRIPT = ".port-registry/stop.sh"
DEFAULT_START_LOG = ".port-registry/start.log"
DEFAULT_STOP_LOG = ".port-registry/stop.log"
PLACEHOLDER_SENTINEL = "port-registry: edit .port-registry/"


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        emit({"status": "error", "reason": "invalid_args", "message": message})
        raise SystemExit(2)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(payload):
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def registry_path():
    configured = os.environ.get("PORT_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)
    return pathlib.Path(configured).expanduser()


def listen_path():
    """Sticky listen.json beside the registry, or PORTSKILL_LISTEN_PATH.

    Default (~/.config/port-registry/registry.json) keeps listen.json in the
    same directory as today. Tests set PORT_REGISTRY_PATH to a temp file so
    listen.json never touches the user's home config.
    """
    configured = os.environ.get("PORTSKILL_LISTEN_PATH")
    if configured and str(configured).strip():
        return pathlib.Path(configured).expanduser()
    return registry_path().parent / LISTEN_FILENAME


def is_loopback_host(host):
    """True for 127.0.0.1 / ::1 / localhost (and 127.0.0.0/8). Missing host is loopback."""
    if not isinstance(host, str):
        return True
    h = host.strip().lower()
    if not h:
        return True
    if h in LOOPBACK_HOSTS:
        return True
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "::1":
        return True
    if h.startswith("127."):
        return True
    return False


ALLOW_NON_LOOPBACK_FLAG = "--allow-non-loopback"


def bind_host_warning(host):
    """Clear warning when --host is not loopback. None when loopback / unset."""
    if host is None or is_loopback_host(host):
        return None
    return (
        f"Bound to {host} (not loopback). "
        "The unauthenticated UI and HTTP MCP are reachable beyond this machine. "
        "Default bind remains 127.0.0.1. "
        f"This requires {ALLOW_NON_LOOPBACK_FLAG} (documented footgun). Remotes HOLD."
    )


def non_loopback_refuse_message(host):
    """Error when bind host is not loopback and the override flag is absent."""
    return (
        f"Refusing to bind {host} (not loopback). "
        "The unauthenticated UI and HTTP MCP would be reachable beyond this machine. "
        f"Default bind is 127.0.0.1. Pass {ALLOW_NON_LOOPBACK_FLAG} only if you "
        "intentionally accept that exposure (documented footgun)."
    )


def listen_allows_non_loopback(listen_payload):
    """True when listen.json records an explicit non-loopback override."""
    if not isinstance(listen_payload, dict):
        return False
    return bool(listen_payload.get("allow_non_loopback"))


def pool_bounds():
    try:
        start = int(os.environ.get("PORT_REGISTRY_POOL_START", str(DEFAULT_POOL_START)))
        end = int(os.environ.get("PORT_REGISTRY_POOL_END", str(DEFAULT_POOL_END)))
    except ValueError:
        fail("invalid_config", "pool bounds must be integers")
    if start > end:
        fail("invalid_config", "pool start must be less than or equal to pool end")
    return start, end


def project_path(value):
    return str(pathlib.Path(value).expanduser().resolve())


def local_registry_path(project):
    return pathlib.Path(project) / ".port-registry.json"


def require_project_directory(project):
    project_dir = pathlib.Path(project)
    if not project_dir.exists() or not project_dir.is_dir():
        fail("project_not_found", f"project path does not exist: {project}")


def fail(reason, message=None, exit_code=2, **extra):
    payload = {"status": "error", "reason": reason}
    if message is not None:
        payload["message"] = message
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    emit(payload)
    raise SystemExit(exit_code)


def needs_tailnet(project, count_or_range):
    if isinstance(count_or_range, int):
        subject = f"{count_or_range} port"
        if count_or_range != 1:
            subject += "s"
    else:
        subject = f"range {count_or_range}"
    return {
        "status": "needs_input",
        "input": "tailnet",
        "prompt": (
            f"Choose tailnet exposure for project {project} and {subject}: "
            "serve, funnel, or none."
        ),
        "options": ["serve", "funnel", "none"],
        "resume_hint": "--tailnet",
    }


def initial_registry(start, end):
    return {
        "version": VERSION,
        "pool": {"start": start, "end": end},
        "projects": {},
        "presets": {},
        "settings": default_settings(),
        "sessions": default_sessions(),
        "machines": default_machines(),
        "environment_history": {},
    }


def default_settings():
    return {
        "auto_apply_preset": None,
        "auto_apply_on_launch": False,
        "auto_exit_on_shutdown": False,
        "overlap_policy": "allow",  # legacy/inert; prefer require_compat
        "require_compat": True,  # always on (UI locked)
        "open_environment_tabs": [],
        "focused_environment": None,
        "mcp_tools": {},  # tool_name -> bool; missing key = enabled
        "mcp_user_commands": {},  # name -> {name, description, steps[{tool,arguments,mode}]}
        "serve_portskill_on_tailscale": True,  # default ON — Serve Portskill listen port (never Funnel)
        "handoff_enabled": False,  # Session Handoff section opt-in
        "handoff_kit": None,  # optional override; default is vendored kit
    }


SESSION_HISTORY_CAP = 50
SESSION_SOURCES = ("preset_apply", "apply_defaults", "enter_house")


def normalize_overlap_policy(value):
    if isinstance(value, str) and value.strip().lower() in ("allow", "deny"):
        return value.strip().lower()
    return "allow"


def normalize_settings(settings):
    normalized = default_settings()
    if not isinstance(settings, dict):
        return normalized
    preset = settings.get("auto_apply_preset", None)
    if preset is None or preset == "" or (isinstance(preset, str) and preset.strip().lower() in ("none", "off", "null")):
        normalized["auto_apply_preset"] = None
    elif isinstance(preset, str) and preset.strip():
        normalized["auto_apply_preset"] = preset.strip()
    else:
        normalized["auto_apply_preset"] = None
    flag = settings.get("auto_apply_on_launch", False)
    if isinstance(flag, bool):
        normalized["auto_apply_on_launch"] = flag
    elif isinstance(flag, str):
        normalized["auto_apply_on_launch"] = flag.strip().lower() in ("1", "true", "yes", "on")
    else:
        normalized["auto_apply_on_launch"] = bool(flag)
    exit_flag = settings.get("auto_exit_on_shutdown", False)
    if isinstance(exit_flag, bool):
        normalized["auto_exit_on_shutdown"] = exit_flag
    elif isinstance(exit_flag, str):
        normalized["auto_exit_on_shutdown"] = exit_flag.strip().lower() in ("1", "true", "yes", "on")
    else:
        normalized["auto_exit_on_shutdown"] = bool(exit_flag)
    normalized["overlap_policy"] = normalize_overlap_policy(settings.get("overlap_policy", "allow"))
    # Product rule: require_compat is always on (cannot deselect)
    normalized["require_compat"] = True
    # Legacy: overlap_policy=deny without explicit require_compat ⇒ require_compat
    if normalized["overlap_policy"] == "deny" and "require_compat" not in (settings or {}):
        normalized["require_compat"] = True
    # UI session: open environment tabs + focused environment (additive)
    tabs = settings.get("open_environment_tabs", [])
    clean_tabs = []
    if isinstance(tabs, list):
        for name in tabs:
            if isinstance(name, str) and name.strip() and name.strip() not in clean_tabs:
                clean_tabs.append(name.strip())
    normalized["open_environment_tabs"] = clean_tabs
    focused = settings.get("focused_environment", None)
    if isinstance(focused, str) and focused.strip():
        focused = focused.strip()
        if focused not in clean_tabs and clean_tabs:
            # Keep focus if tab list empty of it — still allow; UI may open it
            pass
        normalized["focused_environment"] = focused
    else:
        normalized["focused_environment"] = None
    # Per-tool MCP enable/disable (missing key = enabled). Unknown names kept; MCP ignores them.
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
    normalized["mcp_tools"] = mcp_tools
    # Optional preference: Tailscale Serve the Portskill UI/MCP listen port (not Funnel).
    serve_ps = settings.get("serve_portskill_on_tailscale", True)
    if isinstance(serve_ps, bool):
        normalized["serve_portskill_on_tailscale"] = serve_ps
    elif isinstance(serve_ps, str):
        normalized["serve_portskill_on_tailscale"] = serve_ps.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    else:
        normalized["serve_portskill_on_tailscale"] = bool(serve_ps)
    normalized["mcp_user_commands"] = normalize_mcp_user_commands(
        settings.get("mcp_user_commands", {})
    )
    handoff_flag = settings.get("handoff_enabled", False)
    if isinstance(handoff_flag, bool):
        normalized["handoff_enabled"] = handoff_flag
    elif isinstance(handoff_flag, str):
        normalized["handoff_enabled"] = handoff_flag.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    else:
        normalized["handoff_enabled"] = bool(handoff_flag)
    kit = settings.get("handoff_kit")
    if isinstance(kit, str) and kit.strip():
        normalized["handoff_kit"] = kit.strip()
    else:
        normalized["handoff_kit"] = None
    return normalized



MCP_USER_COMMAND_MAX_STEPS = 12
MCP_USER_COMMAND_TOOL_NAMES = None  # filled lazily from mcp.TOOL_DEFS when validating


def normalize_mcp_user_command_step(step):
    if not isinstance(step, dict):
        return None
    tool = step.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None
    mode = step.get("mode") or "series"
    if not isinstance(mode, str):
        mode = "series"
    mode = mode.strip().lower()
    if mode not in ("series", "parallel"):
        mode = "series"
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    return {"tool": tool.strip(), "arguments": arguments, "mode": mode}


def normalize_mcp_user_commands(raw):
    """Validate/normalize settings.mcp_user_commands map.

    Limits (v1):
    - max MCP_USER_COMMAND_MAX_STEPS steps per command
    - steps.tool must be an existing system TOOL_DEFS name (checked at call time too)
    - no nested chains (user-command cannot invoke another user-command)
    - mode is series|parallel only
    """
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip()
        if not isinstance(val, dict):
            continue
        cmd_name = val.get("name") if isinstance(val.get("name"), str) and val.get("name").strip() else name
        cmd_name = cmd_name.strip()
        desc = val.get("description") if isinstance(val.get("description"), str) else ""
        steps_in = val.get("steps")
        steps = []
        if isinstance(steps_in, list):
            for raw_step in steps_in[:MCP_USER_COMMAND_MAX_STEPS]:
                norm = normalize_mcp_user_command_step(raw_step)
                if norm is not None:
                    steps.append(norm)
        if not steps:
            continue
        out[cmd_name] = {
            "name": cmd_name,
            "description": desc.strip(),
            "steps": steps,
        }
    return out


def default_sessions():
    return {"active": None, "history": []}


def normalize_session_preset(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_session_source(value):
    if isinstance(value, str) and value.strip() in SESSION_SOURCES:
        return value.strip()
    return "apply_defaults"


def normalize_history_entry(item):
    if not isinstance(item, dict):
        return None
    started_at = item.get("started_at")
    if not isinstance(started_at, str) or not started_at.strip():
        return None
    ended_at = item.get("ended_at")
    if ended_at is not None and (not isinstance(ended_at, str) or not ended_at.strip()):
        ended_at = None
    return {
        "preset": normalize_session_preset(item.get("preset")),
        "started_at": started_at.strip(),
        "ended_at": ended_at.strip() if isinstance(ended_at, str) else None,
        "opaque": True,
    }


def normalize_sessions(sessions):
    out = default_sessions()
    if not isinstance(sessions, dict):
        return out
    active = sessions.get("active")
    if isinstance(active, dict):
        started_at = active.get("started_at")
        if isinstance(started_at, str) and started_at.strip():
            out["active"] = {
                "preset": normalize_session_preset(active.get("preset")),
                "started_at": started_at.strip(),
                "source": normalize_session_source(active.get("source")),
            }
    history = sessions.get("history")
    cleaned = []
    if isinstance(history, list):
        for item in history:
            entry = normalize_history_entry(item)
            if entry is not None:
                cleaned.append(entry)
    out["history"] = cleaned[-SESSION_HISTORY_CAP:]
    return out


def overlap_denied_payload(active):
    label = (active or {}).get("preset") or "(ad-hoc)"
    return {
        "status": "error",
        "reason": "overlap_denied",
        "message": (
            f"Another environment is already active ({label}). "
            "Deactivate it first, or allow overlap."
        ),
        "active": active,
        "hint": "Run deactivate first, or: settings set --overlap-policy allow",
    }


def check_overlap_denied(registry):
    """Legacy session-overlap gate — disabled. Prefer require_compat + compat_check."""
    return None


def begin_active_session(registry, preset, source):
    """Replace sessions.active and append an open history window (opaque)."""
    sessions = normalize_sessions(registry.get("sessions"))
    now = utc_now()
    previous = sessions.get("active")
    if previous and previous.get("started_at"):
        closed = False
        for entry in reversed(sessions["history"]):
            if (
                entry.get("ended_at") is None
                and entry.get("started_at") == previous.get("started_at")
                and entry.get("preset") == previous.get("preset")
            ):
                entry["ended_at"] = now
                closed = True
                break
        if not closed:
            sessions["history"].append(
                {
                    "preset": previous.get("preset"),
                    "started_at": previous.get("started_at"),
                    "ended_at": now,
                    "opaque": True,
                }
            )
    preset_name = normalize_session_preset(preset)
    source_name = normalize_session_source(source)
    sessions["active"] = {
        "preset": preset_name,
        "started_at": now,
        "source": source_name,
    }
    sessions["history"].append(
        {
            "preset": preset_name,
            "started_at": now,
            "ended_at": None,
            "opaque": True,
        }
    )
    sessions["history"] = sessions["history"][-SESSION_HISTORY_CAP:]
    registry["sessions"] = sessions
    return sessions["active"]


def end_active_session(registry):
    """Close active session and set ended_at on the matching history window."""
    sessions = normalize_sessions(registry.get("sessions"))
    now = utc_now()
    active = sessions.get("active")
    if active and active.get("started_at"):
        closed = False
        for entry in reversed(sessions["history"]):
            if (
                entry.get("ended_at") is None
                and entry.get("started_at") == active.get("started_at")
                and entry.get("preset") == active.get("preset")
            ):
                entry["ended_at"] = now
                closed = True
                break
        if not closed:
            sessions["history"].append(
                {
                    "preset": active.get("preset"),
                    "started_at": active.get("started_at"),
                    "ended_at": now,
                    "opaque": True,
                }
            )
    sessions["active"] = None
    sessions["history"] = sessions["history"][-SESSION_HISTORY_CAP:]
    registry["sessions"] = sessions
    return active


def build_schedule_windows(registry, focus=None):
    """Opaque busy windows for schedule/busy view (no services list)."""
    sessions = normalize_sessions(registry.get("sessions"))
    focus_name = normalize_session_preset(focus)
    windows = []
    active = sessions.get("active")
    active_key = None
    if active and active.get("started_at"):
        active_key = (active.get("preset"), active.get("started_at"))
    for entry in sessions.get("history") or []:
        preset = entry.get("preset")
        if focus_name is not None and preset != focus_name:
            continue
        windows.append(
            {
                "preset": preset,
                "started_at": entry.get("started_at"),
                "ended_at": entry.get("ended_at"),
                "busy": True,
                "opaque": True,
            }
        )
    # Ensure open active window is present even if history was trimmed oddly
    if active and active.get("started_at"):
        if focus_name is None or active.get("preset") == focus_name:
            found_open = any(
                w.get("started_at") == active.get("started_at")
                and w.get("preset") == active.get("preset")
                and w.get("ended_at") is None
                for w in windows
            )
            if not found_open:
                windows.append(
                    {
                        "preset": active.get("preset"),
                        "started_at": active.get("started_at"),
                        "ended_at": None,
                        "busy": True,
                        "opaque": True,
                    }
                )
    windows.sort(key=lambda w: w.get("started_at") or "")
    return {
        "status": "ok",
        "overlap_policy": normalize_settings(registry.get("settings")).get("overlap_policy"),
        "active": active,
        "focus": focus_name,
        "windows": windows,
        "count": len(windows),
    }


def normalize_preset_service(service):
    if not isinstance(service, dict):
        return None
    project = service.get("project")
    if not isinstance(project, str) or not project.strip():
        return None
    try:
        count = int(service.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    if count <= 0:
        count = 1
    tailnet = service.get("tailnet") or "none"
    if tailnet not in ("serve", "funnel", "none"):
        tailnet = "none"
    out = {
        "project": project,
        "note": service.get("note"),
        "count": count,
        "tailnet": tailnet,
        "default_state": normalize_default_state(service.get("default_state")),
    }
    range_id = service.get("range_id")
    if isinstance(range_id, str) and range_id.strip():
        out["range_id"] = range_id.strip()
    return out


def normalize_preset_entry(name, entry):
    if not isinstance(entry, dict):
        entry = {}
    services_in = entry.get("services")
    services = []
    if isinstance(services_in, list):
        for item in services_in:
            normalized = normalize_preset_service(item)
            if normalized is not None:
                services.append(normalized)
    preset_name = entry.get("name") if isinstance(entry.get("name"), str) and entry.get("name").strip() else name
    description = entry.get("description") if isinstance(entry.get("description"), str) else ""
    updated_at = entry.get("updated_at") if isinstance(entry.get("updated_at"), str) else utc_now()
    return {
        "name": preset_name,
        "description": description,
        "updated_at": updated_at,
        "services": services,
    }


def normalize_presets(presets):
    if not isinstance(presets, dict):
        return {}
    out = {}
    for key, value in presets.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip()
        out[name] = normalize_preset_entry(name, value)
    return out



def default_machines():
    return {
        "local": {
            "id": "local",
            "label": "This Mac",
            "host": "127.0.0.1",
            "kind": "local",
        }
    }


def normalize_machine_entry(machine_id, entry):
    mid = str(machine_id).strip() if machine_id is not None else ""
    if not mid:
        return None
    if not isinstance(entry, dict):
        entry = {}
    kind = entry.get("kind") or ("local" if mid == "local" else "remote")
    if kind not in ("local", "remote"):
        kind = "remote" if mid != "local" else "local"
    host = entry.get("host")
    if not isinstance(host, str) or not host.strip():
        host = "127.0.0.1" if kind == "local" else None
    else:
        host = host.strip()
    label = entry.get("label")
    if not isinstance(label, str) or not label.strip():
        label = "This Mac" if mid == "local" else mid
    else:
        label = label.strip()
    out = {"id": mid, "label": label, "host": host, "kind": kind}
    return out


def normalize_machines(machines):
    """Additive machines map: always includes local; merges remotes by id."""
    out = default_machines()
    if not isinstance(machines, dict):
        return out
    for key, entry in machines.items():
        normalized = normalize_machine_entry(key, entry)
        if normalized is None:
            continue
        mid = normalized["id"]
        if mid == "local":
            out["local"] = {
                "id": "local",
                "label": normalized.get("label") or "This Mac",
                "host": normalized.get("host") or "127.0.0.1",
                "kind": "local",
            }
        else:
            if not normalized.get("host"):
                continue
            out[mid] = normalized
    return out


def get_machine(registry, machine_id):
    machines = registry.get("machines") if isinstance(registry, dict) else None
    if not isinstance(machines, dict):
        machines = default_machines()
    mid = (machine_id or "local")
    if not isinstance(mid, str) or not mid.strip():
        mid = "local"
    mid = mid.strip()
    entry = machines.get(mid)
    if isinstance(entry, dict):
        return entry
    if mid == "local":
        return default_machines()["local"]
    return None



def is_remote_range(registry, item):
    """True when the range is owned by a remote registry machine (links only)."""
    if not isinstance(item, dict):
        return False
    mid = item.get("machine_id") or item.get("machine") or item.get("host_id") or "local"
    if not isinstance(mid, str) or not mid.strip() or mid.strip() == "local":
        return False
    machine = get_machine(registry or {}, mid.strip())
    if not isinstance(machine, dict):
        return True  # unknown non-local id → treat as remote
    return (machine.get("kind") or "remote") == "remote"


def refuse_remote_process(registry, item, action="start"):
    mid = item.get("machine_id") or item.get("machine") or item.get("host_id") or "local"
    fail(
        "remote_machine",
        f"Cannot {action} processes for remote machine {mid} from this Mac. "
        "Remotes are registry entries + links only.",
        machine_id=mid,
        range_id=item.get("id"),
        hint="Use the remote host's own Portskill/keepalive, or open the port URL.",
    )


def extract_tailscale_advertise_host(self_node):
    """Pick MagicDNS / DNSName / Tailscale IP from status --json Self."""
    if not isinstance(self_node, dict):
        return None
    for key in ("DNSName", "dnsName", "HostName", "hostname"):
        val = self_node.get(key)
        if isinstance(val, str) and val.strip():
            host = val.strip().rstrip(".")
            if host:
                return host
    ips = self_node.get("TailscaleIPs") or self_node.get("TailscaleIPs".lower()) or self_node.get("AllowedIPs")
    if isinstance(ips, list):
        for ip in ips:
            if isinstance(ip, str) and ip.strip():
                # Prefer IPv4
                if ":" not in ip.strip():
                    return ip.strip()
        for ip in ips:
            if isinstance(ip, str) and ip.strip():
                return ip.strip().split("/")[0]
    return None


def resolve_range_host(registry, item, tailscale_self=None, allow_probe=True):
    """Host resolution order for port hyperlinks / status.

    1. Explicit host / advertise_host on the range
    2. Else if tailnet serve/funnel and Tailscale Self gives MagicDNS/IP — use that
    3. Else if machine_id / machine / host_id points at a registry machine — use its host
    4. Else 127.0.0.1 for local-only
    Never link a remote service as 127.0.0.1 (returns None if remote host missing).
    """
    if not isinstance(item, dict):
        return "127.0.0.1"
    explicit = item.get("host") or item.get("advertise_host")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    mid = item.get("machine_id") or item.get("machine") or item.get("host_id") or "local"
    if not isinstance(mid, str) or not mid.strip():
        mid = "local"
    else:
        mid = mid.strip()
    machine = get_machine(registry or {}, mid)
    kind = "local"
    if isinstance(machine, dict):
        kind = machine.get("kind") or ("local" if mid == "local" else "remote")

    # 2. Tailscale advertise for local serve/funnel
    tailnet = item.get("tailnet") if isinstance(item.get("tailnet"), dict) else {}
    mode = tailnet.get("mode")
    if mode in ("serve", "funnel") and kind != "remote":
        ts_host = extract_tailscale_advertise_host(tailscale_self)
        if ts_host:
            return ts_host
        if allow_probe and tailscale_self is None:
            try:
                status = probe_tailscale_status()
                ts_host = extract_tailscale_advertise_host(status.get("Self"))
                if ts_host:
                    return ts_host
            except Exception:
                pass

    # 3. Registry machine advertise host
    if isinstance(machine, dict):
        mhost = machine.get("host")
        if isinstance(mhost, str) and mhost.strip():
            mhost = mhost.strip()
            if kind == "remote" and mhost in ("127.0.0.1", "localhost", "::1"):
                return None
            return mhost
        if kind == "remote":
            return None

    # 4. Local-only
    return "127.0.0.1"


def resolve_range_scheme(item):
    """Resolve URL scheme for a range hyperlink.

    - Explicit http|https on the item wins when set.
    - Else Tailscale Serve/Funnel advertise HTTPS.
    - Else http.

    Note: many older records defaulted scheme to "http" even for serve/funnel.
    For those modes, treat stored "http" as the legacy default and use https
    (Serve/Funnel are HTTPS). Explicit "https" is unchanged.
    """
    if not isinstance(item, dict):
        return "http"
    tailnet = item.get("tailnet") if isinstance(item.get("tailnet"), dict) else {}
    mode = tailnet.get("mode")
    if isinstance(mode, str):
        mode = mode.strip().lower()
    else:
        mode = None
    scheme = item.get("scheme")
    if isinstance(scheme, str) and scheme.strip():
        scheme = scheme.strip().lower()
        if scheme in ("http", "https"):
            if scheme == "https":
                return "https"
            # scheme == http
            if mode in ("serve", "funnel"):
                return "https"
            return "http"
    if mode in ("serve", "funnel"):
        return "https"
    return "http"


def resolve_range_url(registry, item, port=None, tailscale_self=None, allow_probe=True):
    """Build scheme://host:port/ for a range (or a specific port in the range)."""
    scheme = resolve_range_scheme(item)
    host = resolve_range_host(
        registry, item, tailscale_self=tailscale_self, allow_probe=allow_probe
    )
    if not host:
        return None
    if port is None:
        port = int(item.get("start") or 0) if isinstance(item, dict) else 0
    else:
        port = int(port)
    if port <= 0:
        return None
    return f"{scheme}://{host}:{port}/"


def normalize_range_machine_fields(item):
    if not isinstance(item, dict):
        return item
    mid = item.get("machine_id") or item.get("machine") or item.get("host_id")
    if isinstance(mid, str) and mid.strip():
        item["machine_id"] = mid.strip()
    else:
        item["machine_id"] = "local"
    host = item.get("host")
    if host is not None and (not isinstance(host, str) or not host.strip()):
        item["host"] = None
    elif isinstance(host, str):
        item["host"] = host.strip()
    advertise = item.get("advertise_host")
    if isinstance(advertise, str) and advertise.strip() and not item.get("host"):
        item["host"] = advertise.strip()
    tailnet = item.get("tailnet") if isinstance(item.get("tailnet"), dict) else {}
    mode = tailnet.get("mode")
    if isinstance(mode, str):
        mode = mode.strip().lower()
    else:
        mode = None
    scheme = item.get("scheme")
    if not isinstance(scheme, str) or not scheme.strip():
        item["scheme"] = "https" if mode in ("serve", "funnel") else "http"
    else:
        s = scheme.strip().lower()
        if s not in ("http", "https"):
            s = "https" if mode in ("serve", "funnel") else "http"
        elif mode in ("serve", "funnel") and s == "http":
            # Prefer https when serve/funnel and scheme was left at default http
            s = "https"
        item["scheme"] = s
    return item


def normalize_registry(data, start, end):
    if not isinstance(data, dict):
        data = {}
    data["version"] = VERSION
    data["pool"] = {"start": start, "end": end}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    data["projects"] = projects
    for project_entry in projects.values():
        ranges = project_entry.get("ranges")
        if isinstance(ranges, list):
            for item in ranges:
                if isinstance(item, dict):
                    normalize_range_record(item)
    # Additive: never wipe projects when presets/settings/sessions missing
    data["presets"] = normalize_presets(data.get("presets"))
    data["settings"] = normalize_settings(data.get("settings"))
    data["sessions"] = normalize_sessions(data.get("sessions"))
    data["machines"] = normalize_machines(data.get("machines"))
    data["environment_history"] = normalize_environment_history(data.get("environment_history"))
    data["workspace_draft"] = normalize_workspace_draft(data.get("workspace_draft"))
    return data



HISTORY_STEPS_CAP = 40
HISTORY_ALL_KEY = "__all__"


def normalize_history_step(step):
    if not isinstance(step, dict):
        return None
    sid = step.get("id")
    if not isinstance(sid, str) or not sid.strip():
        sid = str(uuid.uuid4())
    else:
        sid = sid.strip()
    label = step.get("label")
    if not isinstance(label, str) or not label.strip():
        label = "edit"
    else:
        label = label.strip()
    at = step.get("at")
    if not isinstance(at, str) or not at.strip():
        at = utc_now()
    snapshot = step.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    return {"id": sid, "label": label, "at": at, "snapshot": snapshot}


def normalize_history_entry(entry):
    if not isinstance(entry, dict):
        entry = {}
    baseline = entry.get("baseline")
    if baseline is not None and not isinstance(baseline, dict):
        baseline = None
    steps_in = entry.get("steps")
    steps = []
    if isinstance(steps_in, list):
        for item in steps_in:
            normalized = normalize_history_step(item)
            if normalized is not None:
                steps.append(normalized)
    if len(steps) > HISTORY_STEPS_CAP:
        steps = steps[-HISTORY_STEPS_CAP:]
    try:
        cursor = int(entry.get("cursor", 0))
    except (TypeError, ValueError):
        cursor = 0
    max_cursor = len(steps)  # 0=baseline, len(steps)=tip
    if cursor < 0:
        cursor = 0
    if cursor > max_cursor:
        cursor = max_cursor
    return {"baseline": baseline, "steps": steps, "cursor": cursor}


def normalize_environment_history(data):
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            continue
        out[key.strip()] = normalize_history_entry(value)
    return out


def history_is_modified(entry):
    entry = normalize_history_entry(entry)
    return int(entry.get("cursor") or 0) > 0


def resolve_history_env_name(registry, explicit=None):
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    settings = normalize_settings(registry.get("settings"))
    focused = settings.get("focused_environment")
    if isinstance(focused, str) and focused.strip():
        return focused.strip()
    return HISTORY_ALL_KEY


def _range_snapshot_fields(item):
    """Deep-copy restore-relevant fields from a range record."""
    if not isinstance(item, dict):
        return None
    snap = copy.deepcopy(item)
    # Drop volatile process identity on snapshot restore path later if needed;
    # keep fields so restore can prefer updating existing records.
    return snap


def _cohort_range_refs(registry, env_name):
    """Return list of (project, item) for the environment cohort (non-released)."""
    refs = []
    if env_name is None or env_name == HISTORY_ALL_KEY:
        for project, entry in (registry.get("projects") or {}).items():
            for item in (entry or {}).get("ranges") or []:
                if isinstance(item, dict) and item.get("state") != "released":
                    refs.append((project, item))
        return refs
    presets = registry.get("presets") or {}
    entry = presets.get(env_name)
    if not isinstance(entry, dict):
        # Unknown preset — treat as empty cohort (still allow history of meta)
        return refs
    services = entry.get("services") if isinstance(entry.get("services"), list) else []
    if not services:
        return refs
    # Match by range_id first, else project+note among non-released
    claimed_ids = set()
    for svc in services:
        if not isinstance(svc, dict):
            continue
        rid = svc.get("range_id")
        if isinstance(rid, str) and rid.strip():
            proj, item = find_range_anywhere(registry, rid.strip(), None)
            if item is not None and item.get("state") != "released":
                if item.get("id") not in claimed_ids:
                    refs.append((proj, item))
                    claimed_ids.add(item.get("id"))
                continue
        project_raw = svc.get("project")
        note = svc.get("note")
        if not isinstance(project_raw, str) or not project_raw.strip():
            continue
        try:
            project = project_path(project_raw)
        except SystemExit:
            project = project_raw.strip()
        item = find_matching_unreleased(registry, project, note)
        if item is not None and item.get("id") not in claimed_ids:
            refs.append((project, item))
            claimed_ids.add(item.get("id"))
    return refs


def snapshot_environment(registry, env_name):
    """Deep-copy cohort ranges (+ preset services when env is a named preset)."""
    key = HISTORY_ALL_KEY if env_name is None else env_name
    ranges_out = []
    for project, item in _cohort_range_refs(registry, key):
        snap = _range_snapshot_fields(item)
        if snap is None:
            continue
        ranges_out.append({"project": project, "range": snap})
    services = None
    if key != HISTORY_ALL_KEY:
        presets = registry.get("presets") or {}
        entry = presets.get(key)
        if isinstance(entry, dict):
            services = copy.deepcopy(entry.get("services") or [])
    return {
        "env": key,
        "captured_at": utc_now(),
        "ranges": ranges_out,
        "services": services,
    }


def ensure_baseline(registry, env_name):
    key = resolve_history_env_name(registry, env_name)
    hist = registry.setdefault("environment_history", {})
    entry = normalize_history_entry(hist.get(key))
    if entry.get("baseline") is None:
        entry["baseline"] = snapshot_environment(registry, key)
        entry["steps"] = []
        entry["cursor"] = 0
    hist[key] = entry
    registry["environment_history"] = hist
    return key, entry


def push_history_step(registry, env_name, label):
    key, entry = ensure_baseline(registry, env_name)
    cursor = int(entry.get("cursor") or 0)
    # Truncate redo ahead of cursor (steps after cursor)
    steps = list(entry.get("steps") or [])
    if cursor < len(steps):
        steps = steps[:cursor]
    step = {
        "id": str(uuid.uuid4()),
        "label": (label or "edit").strip() or "edit",
        "at": utc_now(),
        "snapshot": snapshot_environment(registry, key),
    }
    steps.append(step)
    if len(steps) > HISTORY_STEPS_CAP:
        # Drop oldest steps but keep baseline; adjust cursor
        overflow = len(steps) - HISTORY_STEPS_CAP
        steps = steps[overflow:]
    entry["steps"] = steps
    entry["cursor"] = len(steps)
    hist = registry.setdefault("environment_history", {})
    hist[key] = entry
    registry["environment_history"] = hist
    return key, entry


def maybe_push_history(registry, label, env_override=None):
    """Append post-mutation snapshot. Call ensure_baseline *before* the mutation."""
    key = resolve_history_env_name(registry, env_override)
    return push_history_step(registry, key, label)


def history_env_from_args(args, registry=None):
    explicit = getattr(args, "environment", None) if args is not None else None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if registry is not None:
        return resolve_history_env_name(registry, None)
    return None


def _snapshot_at_index(entry, index):
    entry = normalize_history_entry(entry)
    try:
        index = int(index)
    except (TypeError, ValueError):
        fail("invalid_args", "history index must be an integer")
    if index < 0:
        fail("invalid_args", "history index must be >= 0")
    if index == 0:
        snap = entry.get("baseline")
        if not isinstance(snap, dict):
            fail("history_corrupt", "baseline snapshot missing")
        return snap
    steps = entry.get("steps") or []
    if index > len(steps):
        fail("invalid_args", f"history index {index} out of range (0..{len(steps)})")
    step = steps[index - 1]
    snap = step.get("snapshot") if isinstance(step, dict) else None
    if not isinstance(snap, dict):
        fail("history_corrupt", f"snapshot missing at index {index}")
    return snap


def _ports_free_for_restore(registry, start, end, exclude_range_id=None):
    for other in non_released_ranges(registry):
        if exclude_range_id and other.get("id") == exclude_range_id:
            continue
        if overlaps(start, end, other["start"], other["end"]):
            return False
    return True


def _apply_range_fields_from_snapshot(item, snap_range):
    """Update existing range record to match snapshot (registry state, not live PIDs)."""
    keep_life = item.get("lifecycle")
    for key, value in snap_range.items():
        if key == "lifecycle":
            continue
        item[key] = copy.deepcopy(value)
    # Restore lifecycle metadata but clear live pid/pgid — do not resurrect processes
    life = copy.deepcopy(snap_range.get("lifecycle") or default_lifecycle())
    if not isinstance(life, dict):
        life = default_lifecycle()
    life["pid"] = None
    life["pgid"] = None
    # Prefer stopped_at if snapshot had active process markers
    if keep_life and isinstance(keep_life, dict):
        # If something is still live on the old record, caller should have stopped/released first
        pass
    item["lifecycle"] = normalize_lifecycle(life)
    normalize_range_record(item)
    return item


def restore_history(registry, env_name, index):
    key = resolve_history_env_name(registry, env_name)
    hist = registry.setdefault("environment_history", {})
    entry = normalize_history_entry(hist.get(key))
    if entry.get("baseline") is None and not entry.get("steps"):
        fail("history_empty", f"no history for environment {key}")
    snap = _snapshot_at_index(entry, index)
    snap_ranges = snap.get("ranges")
    if not isinstance(snap_ranges, list):
        fail("history_corrupt", "snapshot.ranges missing or not a list")

    desired = {}  # range_id -> (project, range_dict)
    for row in snap_ranges:
        if not isinstance(row, dict):
            fail("history_corrupt", "snapshot range row corrupt")
        project = row.get("project")
        rng = row.get("range")
        if not isinstance(project, str) or not project.strip():
            fail("history_corrupt", "snapshot range missing project")
        if not isinstance(rng, dict) or not isinstance(rng.get("id"), str):
            fail("history_corrupt", "snapshot range missing id")
        desired[rng["id"]] = (project.strip(), rng)

    # Current cohort (what we manage for this env)
    current_refs = _cohort_range_refs(registry, key)
    current_by_id = {item.get("id"): (proj, item) for proj, item in current_refs if item.get("id")}

    changed_projects = set()

    # Release ranges present now in cohort but not in snapshot
    for rid, (proj, item) in list(current_by_id.items()):
        if rid in desired:
            continue
        if range_has_live_process(item):
            # stop then release path
            try:
                run_stop_script_if_ready(item, proj)
            except Exception:
                pass
            try:
                terminate_lifecycle_processes(item)
            except Exception:
                pass
            if range_has_live_process(item):
                fail(
                    "process_still_running",
                    f"cannot restore: range {rid} still has a live process; stop it first",
                )
        release_item(item)
        changed_projects.add(proj)

    # Update existing or re-add missing from snapshot
    for rid, (project, snap_range) in desired.items():
        existing_proj, existing = find_range_anywhere(registry, rid, None)
        start = int(snap_range.get("start") or 0)
        end = int(snap_range.get("end") or 0)
        if existing is not None:
            # If ports differ, ensure free (excluding self)
            if not _ports_free_for_restore(registry, start, end, exclude_range_id=rid):
                fail(
                    "port_in_use",
                    f"cannot restore range {rid}: ports {start}-{end} overlap another live range",
                )
            # Move project if needed
            if existing_proj != project:
                # Remove from old project list and append to new
                old_entry = registry["projects"].get(existing_proj) or {}
                old_ranges = old_entry.get("ranges") or []
                old_entry["ranges"] = [r for r in old_ranges if r.get("id") != rid]
                registry["projects"][existing_proj] = old_entry
                changed_projects.add(existing_proj)
                ensure_project(registry, project)
                _apply_range_fields_from_snapshot(existing, snap_range)
                # Ensure reserved-like restore of registry status from snapshot
                registry["projects"][project].setdefault("ranges", []).append(existing)
                changed_projects.add(project)
            else:
                if not _ports_free_for_restore(registry, start, end, exclude_range_id=rid):
                    fail("port_in_use", f"cannot restore range {rid}: ports busy")
                _apply_range_fields_from_snapshot(existing, snap_range)
                changed_projects.add(project)
        else:
            if not _ports_free_for_restore(registry, start, end, exclude_range_id=None):
                fail(
                    "port_in_use",
                    f"cannot restore missing range {rid}: ports {start}-{end} not free",
                )
            ensure_project(registry, project)
            new_item = copy.deepcopy(snap_range)
            life = new_item.get("lifecycle") or default_lifecycle()
            if isinstance(life, dict):
                life = dict(life)
                life["pid"] = None
                life["pgid"] = None
                new_item["lifecycle"] = life
            # Prefer reserved when restoring without process resurrection
            if new_item.get("state") == "active":
                new_item["state"] = "reserved"
                new_item["activated_at"] = None
            normalize_range_record(new_item)
            registry["projects"][project]["ranges"].append(new_item)
            changed_projects.add(project)

    # Restore preset services list when present in snapshot
    services = snap.get("services")
    if key != HISTORY_ALL_KEY and isinstance(services, list):
        presets = registry.setdefault("presets", {})
        if key in presets and isinstance(presets[key], dict):
            cleaned = []
            for svc in services:
                normalized = normalize_preset_service(svc)
                if normalized is not None:
                    cleaned.append(normalized)
            presets[key]["services"] = cleaned
            presets[key]["updated_at"] = utc_now()
            presets[key] = normalize_preset_entry(key, presets[key])
        elif key not in presets:
            presets[key] = normalize_preset_entry(key, {
                "name": key,
                "description": "",
                "updated_at": utc_now(),
                "services": services,
            })

    entry["cursor"] = int(index)
    hist[key] = entry
    registry["environment_history"] = hist
    return {
        "status": "ok",
        "environment": key,
        "index": int(index),
        "modified": history_is_modified(entry),
        "cursor": entry["cursor"],
        "step_count": len(entry.get("steps") or []),
    }, sorted(changed_projects)


def reset_history(registry, env_name):
    return restore_history(registry, env_name, 0)


def clear_history_stack(registry, env_name):
    """Drop history stack for env; keep live registry state. Re-baseline from current."""
    key = resolve_history_env_name(registry, env_name)
    hist = registry.setdefault("environment_history", {})
    hist[key] = {
        "baseline": snapshot_environment(registry, key),
        "steps": [],
        "cursor": 0,
    }
    registry["environment_history"] = hist
    return {
        "status": "ok",
        "environment": key,
        "cleared": True,
        "modified": False,
        "cursor": 0,
        "step_count": 0,
    }


def history_list_payload(registry, env_name):
    key = resolve_history_env_name(registry, env_name)
    hist = normalize_environment_history(registry.get("environment_history"))
    entry = normalize_history_entry(hist.get(key))
    steps_out = []
    for i, step in enumerate(entry.get("steps") or []):
        steps_out.append({
            "id": step.get("id"),
            "label": step.get("label"),
            "at": step.get("at"),
            "index": i + 1,
        })
    return {
        "status": "ok",
        "environment": key,
        "baselineLabel": "Original",
        "baseline": entry.get("baseline") is not None,
        "cursor": int(entry.get("cursor") or 0),
        "modified": history_is_modified(entry),
        "steps": steps_out,
        "step_count": len(steps_out),
    }



def default_lifecycle():
    return {
        "start_script": DEFAULT_START_SCRIPT,
        "stop_script": DEFAULT_STOP_SCRIPT,
        "start_log": DEFAULT_START_LOG,
        "stop_log": DEFAULT_STOP_LOG,
        "pid": None,
        "pgid": None,
        "started_at": None,
        "stopped_at": None,
        "last_exit_code": None,
    }


def normalize_lifecycle(lifecycle):
    normalized = default_lifecycle()
    if isinstance(lifecycle, dict):
        for key in normalized:
            if key in lifecycle:
                normalized[key] = lifecycle[key]
    return normalized


def normalize_default_state(value):
    if isinstance(value, str) and value.strip().lower() == "on":
        return "on"
    return "off"


def normalize_range_extra(extra):
    if not isinstance(extra, dict):
        return {}
    out = {}
    for key, value in extra.items():
        if isinstance(key, str) and key.strip():
            out[key.strip()] = value
    return out


def normalize_optional_str(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value).strip() or None


def normalize_range_record(item):
    item["lifecycle"] = normalize_lifecycle(item.get("lifecycle"))
    item["default_state"] = normalize_default_state(item.get("default_state"))
    # Additive edit-mode fields (stdlib-safe normalize)
    if "command" in item or item.get("command") is not None:
        item["command"] = normalize_optional_str(item.get("command"))
    else:
        item.setdefault("command", None)
    if "cwd" in item or item.get("cwd") is not None:
        item["cwd"] = normalize_optional_str(item.get("cwd"))
    else:
        item.setdefault("cwd", None)
    item["extra"] = normalize_range_extra(item.get("extra"))
    normalize_range_machine_fields(item)
    return item


def normalize_workspace_draft(draft):
    """Pending edit-mode metadata until Save (range_id -> fields)."""
    if not isinstance(draft, dict):
        return {"ranges": {}, "edit_mode": False}
    ranges = draft.get("ranges") if isinstance(draft.get("ranges"), dict) else {}
    clean = {}
    for rid, fields in ranges.items():
        if not isinstance(rid, str) or not rid.strip() or not isinstance(fields, dict):
            continue
        entry = {}
        for key in ("start_script", "stop_script", "command", "cwd"):
            if key in fields:
                entry[key] = normalize_optional_str(fields.get(key))
        if "extra" in fields:
            entry["extra"] = normalize_range_extra(fields.get("extra"))
        clean[rid.strip()] = entry
    edit_mode = draft.get("edit_mode", False)
    if isinstance(edit_mode, str):
        edit_mode = edit_mode.strip().lower() in ("1", "true", "yes", "on")
    else:
        edit_mode = bool(edit_mode)
    return {"ranges": clean, "edit_mode": edit_mode}


def workspace_draft_is_modified(draft):
    draft = normalize_workspace_draft(draft)
    return bool(draft.get("ranges"))


def backups_dir():
    return registry_path().parent / "backups"


def export_workspace_backup(registry, label="workspace"):
    """Write timestamped workspace backup under ~/.config/port-registry/backups/.

    Returns absolute path string. Raises OSError on write failure.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    dest_dir = backups_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{label}-{stamp}.json"
    payload = build_environment_payload(
        registry,
        name=label,
        description=f"Auto-backup before import ({stamp})",
        project_filter=None,
    )
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    dest.write_text(raw, encoding="utf-8")
    return str(dest.resolve())


def locked_registry(mutator):
    path = registry_path()
    start, end = pool_bounds()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        raw_obj = None
        if raw.strip():
            try:
                registry = json.loads(raw)
                raw_obj = registry if isinstance(registry, dict) else None
            except json.JSONDecodeError as exc:
                fail(
                    "corrupt_registry",
                    f"registry JSON is corrupt at {path}: {exc.msg} (line {exc.lineno} col {exc.colno}); "
                    "fix or restore the file — refusing to wipe",
                )
        else:
            registry = initial_registry(start, end)
        needs_schema_persist = raw_obj is None or not isinstance(raw_obj.get("presets"), dict) or not isinstance(raw_obj.get("settings"), dict) or not isinstance(raw_obj.get("sessions"), dict) or not isinstance(raw_obj.get("environment_history"), dict) or not isinstance(raw_obj.get("machines"), dict) or not isinstance(raw_obj.get("environment_history"), dict)
        registry = normalize_registry(registry, start, end)
        result, changed_projects = mutator(registry)
        if changed_projects or needs_schema_persist:
            handle.seek(0)
            handle.truncate()
            json.dump(registry, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            for project in changed_projects or []:
                if project and not str(project).startswith("__"):
                    write_local_project_file(project, registry)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return result


def write_local_project_file(project, registry):
    require_project_directory(project)
    data = {
        "version": VERSION,
        "project_path": project,
        "ranges": registry["projects"].get(project, {}).get("ranges", []),
    }
    path = local_registry_path(project)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")


def range_record(range_id, start, end, note, tailnet_mode, default_state="off", machine_id="local", host=None, scheme=None):
    mid = machine_id if isinstance(machine_id, str) and machine_id.strip() else "local"
    host_val = host.strip() if isinstance(host, str) and host.strip() else None
    mode = tailnet_mode.strip().lower() if isinstance(tailnet_mode, str) and tailnet_mode.strip() else "none"
    if isinstance(scheme, str) and scheme.strip():
        scheme_val = scheme.strip().lower()
        if scheme_val not in ("http", "https"):
            scheme_val = "https" if mode in ("serve", "funnel") else "http"
        elif mode in ("serve", "funnel") and scheme_val == "http":
            scheme_val = "https"
    else:
        scheme_val = "https" if mode in ("serve", "funnel") else "http"
    return {
        "id": range_id,
        "start": start,
        "end": end,
        "state": "reserved",
        "reserved_at": utc_now(),
        "activated_at": None,
        "released_at": None,
        "tailnet": {
            "mode": tailnet_mode,
            "port": None,
            "configured_at": None,
        },
        "lifecycle": default_lifecycle(),
        "note": note,
        "default_state": normalize_default_state(default_state),
        "machine_id": mid.strip(),
        "host": host_val,
        "scheme": scheme_val,
    }


def all_ranges(registry):
    for project in registry["projects"].values():
        for item in project.get("ranges", []):
            yield item


def non_released_ranges(registry):
    for item in all_ranges(registry):
        if item.get("state") != "released":
            yield item


def overlaps(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


def _claim_summary(claim):
    return {
        "source": claim.get("source"),
        "project": claim.get("project"),
        "note": claim.get("note"),
        "range_id": claim.get("range_id"),
        "count": claim.get("count"),
        "start": claim.get("start"),
        "end": claim.get("end"),
        "resolved": bool(claim.get("resolved")),
    }


def resolve_service_claim(registry, service, source="service"):
    """Resolve a preset/environment service to a port claim when possible."""
    if not isinstance(service, dict):
        return None
    project_raw = service.get("project")
    if not isinstance(project_raw, str) or not project_raw.strip():
        return None
    try:
        project = project_path(project_raw)
    except SystemExit:
        project = project_raw.strip()
    note = service.get("note")
    try:
        count = int(service.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    if count <= 0:
        count = 1
    range_id = service.get("range_id")
    if isinstance(range_id, str) and range_id.strip():
        range_id = range_id.strip()
    else:
        range_id = None
    item = None
    if range_id:
        _proj, item = find_range_anywhere(registry, range_id, project)
        if item is None:
            _proj, item = find_range_anywhere(registry, range_id, None)
        # For compat/warn: still count released ranges that presets remember,
        # so manual re-allocate of those ports can warn. Only skip if missing.
    if item is None:
        try:
            item = find_matching_unreleased(registry, project, note)
        except Exception:  # noqa: BLE001
            item = None
    claim = {
        "source": source,
        "project": project,
        "note": note,
        "count": count,
        "range_id": range_id or (item.get("id") if item else None),
        "start": int(item["start"]) if item and item.get("start") is not None else None,
        "end": int(item["end"]) if item and item.get("end") is not None else None,
        "resolved": item is not None and item.get("start") is not None and item.get("end") is not None,
    }
    return claim


def collect_claims_from_services(registry, services, source):
    claims = []
    for index, service in enumerate(services or []):
        normalized = normalize_preset_service(service)
        if normalized is None:
            continue
        claim = resolve_service_claim(registry, normalized, source=source)
        if claim is not None:
            claim["index"] = index
            claims.append(claim)
    return claims


def collect_claims_from_presets(registry, preset_names):
    presets = registry.get("presets") or {}
    claims = []
    missing = []
    for name in preset_names or []:
        name = (name or "").strip()
        if not name:
            continue
        if name not in presets:
            missing.append(name)
            continue
        entry = normalize_preset_entry(name, presets[name])
        claims.extend(
            collect_claims_from_services(
                registry, entry.get("services") or [], source=f"preset:{name}"
            )
        )
    return claims, missing


def collect_claims_default_on(registry, project_filter=None):
    """Claims for live Default On ranges (what apply-defaults would start)."""
    filters = [project_path(p) for p in project_filter] if project_filter else None
    claims = []
    for project, item in iter_project_ranges(registry, filters):
        if normalize_default_state(item.get("default_state")) != "on":
            continue
        if item.get("state") == "released":
            continue
        claims.append({
            "source": "default_on",
            "project": project,
            "note": item.get("note"),
            "count": int(item["end"]) - int(item["start"]) + 1,
            "range_id": item.get("id"),
            "start": int(item["start"]),
            "end": int(item["end"]),
            "resolved": True,
            "index": len(claims),
        })
    return claims


def find_port_conflicts(claims):
    """Return conflicts for claims that cannot safely share a machine."""
    conflicts = []
    n = len(claims)
    for i in range(n):
        a = claims[i]
        for j in range(i + 1, n):
            b = claims[j]
            if (
                a.get("resolved")
                and b.get("resolved")
                and a.get("range_id")
                and a.get("range_id") == b.get("range_id")
            ):
                continue
            if a.get("resolved") and b.get("resolved"):
                if overlaps(a["start"], a["end"], b["start"], b["end"]):
                    conflicts.append({
                        "reason": "port_overlap",
                        "message": (
                            f"Port ranges overlap: {a.get('start')}-{a.get('end')} "
                            f"({a.get('source')}) vs {b.get('start')}-{b.get('end')} "
                            f"({b.get('source')})"
                        ),
                        "a": _claim_summary(a),
                        "b": _claim_summary(b),
                    })
                    continue
            same_identity = (
                a.get("project") == b.get("project")
                and (a.get("note") or None) == (b.get("note") or None)
            )
            if (
                same_identity
                and a.get("range_id")
                and b.get("range_id")
                and a["range_id"] != b["range_id"]
            ):
                conflicts.append({
                    "reason": "duplicate_identity_different_ranges",
                    "message": (
                        f"Same project/note maps to different ranges: "
                        f"{a['range_id']} vs {b['range_id']}"
                    ),
                    "a": _claim_summary(a),
                    "b": _claim_summary(b),
                })
            elif (
                same_identity
                and a.get("count") != b.get("count")
                and not (a.get("range_id") and a.get("range_id") == b.get("range_id"))
            ):
                conflicts.append({
                    "reason": "count_mismatch",
                    "message": (
                        f"Same project/note with different port counts: "
                        f"{a.get('count')} vs {b.get('count')}"
                    ),
                    "a": _claim_summary(a),
                    "b": _claim_summary(b),
                })
    return conflicts


def run_compat_check(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    """Conflict report for selected environments/services (plain port conflicts)."""
    claims = []
    missing = []
    sources = []
    if preset_names:
        preset_claims, missing = collect_claims_from_presets(registry, preset_names)
        claims.extend(preset_claims)
        sources.extend([f"preset:{n}" for n in preset_names if (n or "").strip()])
    if services is not None:
        claims.extend(collect_claims_from_services(registry, services, source="services"))
        sources.append("services")
    if include_default_on:
        claims.extend(collect_claims_default_on(registry, project_filter=project_filter))
        sources.append("default_on")
    conflicts = find_port_conflicts(claims)
    ok = len(conflicts) == 0 and not missing
    return {
        "status": "ok" if ok else "conflict",
        "compatible": ok,
        "sources": sources,
        "missing_presets": missing,
        "claims": [_claim_summary(c) for c in claims],
        "claim_count": len(claims),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


def presets_claiming_ports(registry, start, end, exclude_range_id=None):
    """Return preset names whose resolved services overlap [start, end]."""
    hits = []
    presets = registry.get("presets") or {}
    for name, entry in sorted(presets.items()):
        entry = normalize_preset_entry(name, entry)
        for svc in entry.get("services") or []:
            claim = resolve_service_claim(registry, svc, source=f"preset:{name}")
            if not claim or not claim.get("resolved"):
                continue
            if exclude_range_id and claim.get("range_id") == exclude_range_id:
                continue
            if overlaps(start, end, claim["start"], claim["end"]):
                hits.append({
                    "preset": name,
                    "range_id": claim.get("range_id"),
                    "project": claim.get("project"),
                    "note": claim.get("note"),
                    "start": claim.get("start"),
                    "end": claim.get("end"),
                    "message": f"Also used in preset: {name}",
                })
                break
    return hits


def warnings_for_port_range(registry, start, end, exclude_range_id=None):
    """Soft warnings when ports collide with a saved preset (enforce off)."""
    hits = presets_claiming_ports(registry, start, end, exclude_range_id=exclude_range_id)
    return [
        {
            "reason": "preset_port_overlap",
            "message": hit["message"],
            "preset": hit["preset"],
            "range_id": hit.get("range_id"),
            "project": hit.get("project"),
            "note": hit.get("note"),
            "ports": f"{hit['start']}-{hit['end']}",
        }
        for hit in hits
    ]


def enforce_compat_or_fail(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    """If require_compat is on, fail with compat_conflict when ports collide."""
    settings = normalize_settings(registry.get("settings"))
    if not settings.get("require_compat"):
        return None
    report = run_compat_check(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )
    if report.get("compatible"):
        return report
    fail(
        "compat_conflict",
        "Selected environments are not compatible (port conflict). "
        "Fix overlapping ranges, or: settings set --require-compat off",
        conflicts=report.get("conflicts"),
        conflict_count=report.get("conflict_count"),
        missing_presets=report.get("missing_presets") or None,
        hint="Run: compat check --preset NAME …  or settings set --require-compat off",
        report={
            k: report[k]
            for k in (
                "compatible",
                "sources",
                "claim_count",
                "conflict_count",
                "conflicts",
                "missing_presets",
            )
        },
    )


def find_free_range(registry, count):
    pool = registry["pool"]
    pool_start = pool["start"]
    pool_end = pool["end"]
    if count <= 0:
        fail("invalid_args", "--count must be a positive integer")
    if count > (pool_end - pool_start + 1):
        return None
    reserved = sorted((r["start"], r["end"]) for r in non_released_ranges(registry))
    candidate = pool_start
    while candidate + count - 1 <= pool_end:
        candidate_end = candidate + count - 1
        conflict = None
        for start, end in reserved:
            if overlaps(candidate, candidate_end, start, end):
                conflict = end
                break
        if conflict is None:
            return candidate, candidate_end
        candidate = conflict + 1
    return None


def next_range_id(project_entry):
    existing = {item.get("id") for item in project_entry.get("ranges", [])}
    base = datetime.datetime.now(datetime.timezone.utc).strftime("r%Y%m%d%H%M%S%f")
    pid = os.getpid()
    candidate = f"{base}-{pid}"
    suffix = 1
    while candidate in existing:
        suffix += 1
        candidate = f"{base}-{pid}-{suffix}"
    return candidate


def ensure_project(registry, project):
    return registry["projects"].setdefault(project, {"ranges": []})


def find_project_range(registry, project, range_id):
    project_entry = registry["projects"].get(project)
    if not project_entry:
        return None
    for item in project_entry.get("ranges", []):
        if item.get("id") == range_id:
            return item
    return None


def resolve_tailscale_bin():
    configured = os.environ.get("PORT_REGISTRY_TAILSCALE_BIN")
    if configured:
        return configured
    which = shutil.which("tailscale")
    if which:
        return which
    mac_default = pathlib.Path(DEFAULT_TAILSCALE_BIN)
    if mac_default.exists():
        return str(mac_default)
    return DEFAULT_TAILSCALE_BIN


def tailscale_base_command():
    return shlex.split(resolve_tailscale_bin())


def should_use_sudo():
    return os.environ.get("PORT_REGISTRY_TAILSCALE_SUDO", "1") != "0"


def tailscale_command(mode, port, off=False):
    command = tailscale_base_command()
    if mode == "funnel" and should_use_sudo():
        command = ["sudo"] + command
    command += [mode, "--bg", f"--https={port}", f"localhost:{port}"]
    if off:
        command.append("off")
    return command


def run_tailnet(mode, port, off=False):
    if mode == "none":
        return
    command = tailscale_command(mode, port, off=off)
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=20,
    )
    if result.returncode != 0:
        fail(
            "tailnet_command_failed",
            f"tailscale command exited with {result.returncode}",
            stdout=(result.stdout or "")[:500],
            stderr=(result.stderr or "")[:500],
        )


def tailscale_bin_available():
    ts_bin = resolve_tailscale_bin()
    if not ts_bin:
        return False, ts_bin
    first = shlex.split(ts_bin)[0]
    path = pathlib.Path(first)
    if path.exists() or shutil.which(first):
        return True, ts_bin
    return False, ts_bin


def extract_tailscale_auth_url(text):
    if not text:
        return None
    import re
    match = re.search(r"https://login\.tailscale\.com/\S+", text)
    if match:
        return match.group(0).rstrip(").,;'\"")
    match = re.search(r"https://[^\s]+tailscale[^\s]*", text)
    if match:
        return match.group(0).rstrip(").,;'\"")
    return None


def probe_tailscale_status():
    """Non-interactive Tailscale status summary for UI/CLI/doctor.

    Returns a dict with chip label Connected / Needs login / Binary missing,
    BackendState, Self (when present), and reason when not connected.
    """
    available, ts_bin = tailscale_bin_available()
    if not available:
        return {
            "ok": False,
            "chip": "Binary missing",
            "state": "binary_missing",
            "reason": "tailscale_bin_missing",
            "message": f"Tailscale binary not found: {ts_bin}",
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": ts_bin,
            "logged_in": False,
        }
    try:
        result = subprocess.run(
            tailscale_base_command() + ["status", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "chip": "Needs login",
            "state": "needs_login",
            "reason": "tailscale_status_timeout",
            "message": "tailscale status timed out (is the daemon running / logged in?)",
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": ts_bin,
            "logged_in": False,
        }
    except OSError as exc:
        return {
            "ok": False,
            "chip": "Binary missing",
            "state": "binary_missing",
            "reason": "tailscale_bin_exec_failed",
            "message": str(exc),
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": ts_bin,
            "logged_in": False,
        }

    raw_out = (result.stdout or "").strip()
    raw_err = (result.stderr or "").strip()
    data = {}
    if raw_out:
        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    backend = data.get("BackendState")
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else None
    auth_url = data.get("AuthURL") or extract_tailscale_auth_url(raw_out) or extract_tailscale_auth_url(raw_err)

    logged_in = False
    if backend == "Running" and self_node:
        logged_in = True
    elif backend == "Running":
        logged_in = True
    elif self_node and backend not in ("NeedsLogin", "NoState"):
        logged_in = True

    if logged_in:
        chip, state, reason = "Connected", "connected", None
        message = f"BackendState={backend}"
    elif backend == "NeedsLogin" or auth_url:
        chip, state, reason = "Needs login", "needs_login", "tailscale_auth_required"
        message = "Tailscale is installed but not logged in."
    elif result.returncode != 0 and not data:
        # Daemon missing / not running often looks like needs login on Mac-first setups
        chip, state, reason = "Needs login", "needs_login", "tailscale_auth_required"
        message = raw_err or raw_out or f"tailscale status exited {result.returncode}"
    else:
        chip, state, reason = "Needs login", "needs_login", "tailscale_auth_required"
        message = f"Tailscale BackendState={backend!r}; login required for Serve."

    return {
        "ok": logged_in,
        "chip": chip,
        "state": state,
        "reason": reason,
        "message": message,
        "BackendState": backend,
        "Self": self_node,
        "AuthURL": auth_url,
        "binary": ts_bin,
        "logged_in": logged_in,
        "raw_exit": result.returncode,
    }


def ensure_tailscale_authenticated(mode):
    """Fail fast when Serve/Funnel needs auth. Never prompts for sudo password."""
    if mode not in ("serve", "funnel"):
        return probe_tailscale_status()
    status = probe_tailscale_status()
    if status.get("state") == "binary_missing":
        fail(
            "tailscale_bin_missing",
            status.get("message") or "Tailscale binary not found",
            tailscale=status,
            hint="Install Tailscale or set PORT_REGISTRY_TAILSCALE_BIN",
        )
    if not status.get("logged_in"):
        fail(
            "tailscale_auth_required",
            "Tailscale Serve requires login. Use Browser Login in the UI, "
            "or run `portskill-cli tailscale login`.",
            tailscale=status,
            hint="tailscale login",
            AuthURL=status.get("AuthURL"),
        )
    return status


def lifecycle(item):
    return normalize_range_record(item)["lifecycle"]


def resolve_project_relative(project, value, field):
    if not isinstance(value, str) or not value:
        fail("invalid_lifecycle_path", f"{field} must be a project-relative path")
    project_dir = pathlib.Path(project).resolve()
    path = (project_dir / value).resolve()
    try:
        path.relative_to(project_dir)
    except ValueError:
        fail("invalid_lifecycle_path", f"{field} resolves outside project: {value}")
    return path


def placeholder_content(name):
    command = name[:-3] if name.endswith(".sh") else name
    return (
        "#!/bin/sh\n"
        f'echo "port-registry: edit .port-registry/{name} before using port-registry '
        f'{command}" >&2\n'
        "exit 64\n"
    )


def scaffold_lifecycle_scripts(project):
    project_dir = pathlib.Path(project).resolve()
    scripts_dir = project_dir / DEFAULT_LIFECYCLE_DIR
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("start.sh", "stop.sh"):
        path = scripts_dir / name
        if not path.exists():
            path.write_text(placeholder_content(name), encoding="utf-8")
            path.chmod(0o755)


def script_is_placeholder(path):
    try:
        return PLACEHOLDER_SENTINEL in path.read_text(encoding="utf-8")
    except OSError:
        return False


def validate_start_script(path):
    if not path.exists():
        fail("start_script_missing", f"start_script does not exist: {path}")
    if not path.is_file():
        fail("start_script_invalid", f"start_script is not a file: {path}")
    if not os.access(path, os.X_OK):
        fail("start_script_not_executable", f"start_script is not executable: {path}")
    if script_is_placeholder(path):
        fail("start_script_placeholder", f"start_script still contains the placeholder: {path}")


def pid_alive(pid):
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_group_pids(pgid):
    try:
        pgid = int(pgid)
    except (TypeError, ValueError):
        return []
    if pgid <= 0:
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    pids = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid_value = int(parts[0])
            pgid_value = int(parts[1])
        except ValueError:
            continue
        if pgid_value == pgid:
            pids.append(pid_value)
    return pids


def wait_for_processes_to_exit(pids, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return not any(pid_alive(pid) for pid in pids)


def run_stop_script_if_ready(item, project):
    life = lifecycle(item)
    stop_script = resolve_project_relative(project, life["stop_script"], "stop_script")
    if (
        stop_script.exists()
        and stop_script.is_file()
        and os.access(stop_script, os.X_OK)
        and not script_is_placeholder(stop_script)
    ):
        stop_log = resolve_project_relative(project, life["stop_log"], "stop_log")
        stop_log.parent.mkdir(parents=True, exist_ok=True)
        with stop_log.open("a", encoding="utf-8") as log:
            result = subprocess.run(
                [str(stop_script)],
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        life["last_exit_code"] = result.returncode


def terminate_lifecycle_processes(item):
    life = lifecycle(item)
    pid = life.get("pid")
    pgid = life.get("pgid")
    live_pid = pid_alive(pid)
    live_group = False
    tracked_pids = []
    try:
        pgid_int = int(pgid)
    except (TypeError, ValueError):
        pgid_int = None
    if pgid_int is not None and pgid_int > 0:
        tracked_pids = process_group_pids(pgid_int)
        live_group = any(pid_alive(value) for value in tracked_pids)
    if live_pid:
        try:
            pid_int = int(pid)
            if pid_int not in tracked_pids:
                tracked_pids.append(pid_int)
        except (TypeError, ValueError):
            pass
    if pgid_int is not None and pgid_int > 0 and (live_pid or live_group):
        try:
            os.killpg(pgid_int, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if tracked_pids and not wait_for_processes_to_exit(tracked_pids, 5.0):
            try:
                os.killpg(pgid_int, signal.SIGKILL)
            except ProcessLookupError:
                pass
            wait_for_processes_to_exit(tracked_pids, 5.0)
    life["pid"] = None
    life["pgid"] = None
    life["stopped_at"] = utc_now()


def activate_item(item, project, range_id, tailnet_arg):
    recorded_mode = item.get("tailnet", {}).get("mode")
    mode = tailnet_arg if tailnet_arg is not None else recorded_mode
    if mode is None:
        emit(needs_tailnet(project, range_id))
        raise SystemExit(3)
    item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    item["tailnet"]["mode"] = mode
    now = utc_now()
    if mode in ("serve", "funnel"):
        ensure_tailscale_authenticated(mode)
        port = item["start"]
        run_tailnet(mode, port, off=False)
        item["tailnet"]["port"] = port
        item["tailnet"]["configured_at"] = now
    else:
        item["tailnet"]["port"] = None
        item["tailnet"]["configured_at"] = None
    item["state"] = "active"
    item["activated_at"] = now


def release_item(item):
    tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    mode = tailnet.get("mode")
    if item.get("state") == "active" and mode in ("serve", "funnel"):
        port = tailnet.get("port") or item["start"]
        run_tailnet(mode, port, off=True)
    item["state"] = "released"
    item["released_at"] = utc_now()
    tailnet["configured_at"] = None


def cmd_allocate(args):
    project = project_path(args.project)
    require_project_directory(project)
    if args.tailnet is None:
        emit(needs_tailnet(project, args.count))
        raise SystemExit(3)
    scaffold_lifecycle_scripts(project)
    manual_start = getattr(args, "start", None)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        if manual_start is not None:
            start = int(manual_start)
            end = start + int(args.count) - 1
            pool = registry["pool"]
            # Allow outside-pool claims so already-served ports (e.g. Tailscale Serve via CLI) can be registered
            if start < pool["start"] or end > pool["end"]:
                # Expand pool to cover the claim so future UI/compat stay consistent
                pool["start"] = min(int(pool["start"]), start)
                pool["end"] = max(int(pool["end"]), end)
                registry["pool"] = pool
            for other in non_released_ranges(registry):
                if overlaps(start, end, other["start"], other["end"]):
                    fail(
                        "port_in_use",
                        f"ports {start}-{end} overlap existing range {other.get('id')} "
                        f"({other['start']}-{other['end']})",
                    )
        else:
            available = find_free_range(registry, args.count)
            if available is None:
                fail("pool_exhausted")
            start, end = available
        project_entry = ensure_project(registry, project)
        machine_id = getattr(args, "machine", None) or "local"
        if not isinstance(machine_id, str) or not machine_id.strip():
            machine_id = "local"
        else:
            machine_id = machine_id.strip()
        host_override = getattr(args, "host", None)
        if isinstance(host_override, str) and host_override.strip():
            host_override = host_override.strip()
        else:
            host_override = None
        scheme = getattr(args, "scheme", None)
        if isinstance(scheme, str) and scheme.strip():
            scheme = scheme.strip().lower()
        else:
            scheme = None  # range_record picks https for serve/funnel
        # Validate machine exists (except default local which is always present after normalize)
        machines = normalize_machines(registry.get("machines"))
        registry["machines"] = machines
        if machine_id not in machines:
            fail(
                "machine_not_found",
                f"unknown machine id: {machine_id}",
                hint="machine add --id ID --host HOST [--label LABEL]",
            )
        # Remote machines are registry + links only — do not require local project scripts to exist
        # on the remote; local project path still scaffolds for bookkeeping.
        item = range_record(
            next_range_id(project_entry),
            start,
            end,
            args.note,
            args.tailnet,
            machine_id=machine_id,
            host=host_override,
            scheme=scheme,
        )
        preset_warnings = warnings_for_port_range(
            registry, start, end, exclude_range_id=item.get("id")
        )
        settings = normalize_settings(registry.get("settings"))
        if preset_warnings and settings.get("require_compat"):
            fail(
                "compat_conflict",
                "Ports collide with a saved environment/preset",
                conflicts=[
                    {
                        "reason": "preset_port_overlap",
                        "message": w["message"],
                        "preset": w.get("preset"),
                        "ports": w.get("ports"),
                    }
                    for w in preset_warnings
                ],
                conflict_count=len(preset_warnings),
                hint="Pick free ports, or: settings set --require-compat off",
            )
        project_entry["ranges"].append(item)
        maybe_push_history(registry, "allocate", hist_key)
        payload = {"status": "ok", "range": item}
        if preset_warnings:
            payload["warnings"] = preset_warnings
        return payload, [project]

    emit(locked_registry(mutate))


def cmd_activate(args):
    project = project_path(args.project)
    require_project_directory(project)

    def mutate(registry):
        item = find_project_range(registry, project, args.range_id)
        if item is None:
            fail("not_found", f"range id not found for project: {args.range_id}")
        if is_remote_range(registry, item):
            refuse_remote_process(registry, item, action="activate")
        activate_item(item, project, args.range_id, args.tailnet)
        return {"status": "ok", "range": item}, [project]

    emit(locked_registry(mutate))


def range_has_live_process(item):
    life = lifecycle(item)
    if pid_alive(life.get("pid")):
        return True
    pgid = life.get("pgid")
    if pgid is None:
        return False
    return any(pid_alive(pid) for pid in process_group_pids(pgid))


def cmd_release(args):
    project = project_path(args.project)
    require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        item = find_project_range(registry, project, args.range_id)
        if item is None:
            fail("not_found", f"range id not found for project: {args.range_id}")
        if range_has_live_process(item):
            fail(
                "process_still_running",
                "range still has a live pid/pgid; use stop (not release) to terminate then free the ports",
            )
        release_item(item)
        maybe_push_history(registry, "release", hist_key)
        return {"status": "ok", "range": item}, [project]

    emit(locked_registry(mutate))


def run_start_all(also_defaults_only=False):
    """Start all non-released local ranges (or only default_state=on)."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, None)
        ensure_baseline(registry, hist_key)
        started = []
        skipped = []
        errors = []
        changed = set()
        for project, item in list(iter_project_ranges(registry, None)):
            if item.get("state") == "released":
                continue
            if also_defaults_only and normalize_default_state(item.get("default_state")) != "on":
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "not_default_on",
                })
                continue
            ok, detail = start_item_for_apply(item, project, registry=registry)
            detail = dict(detail)
            detail["project"] = project
            detail["range_id"] = item.get("id")
            if ok:
                started.append(detail)
                changed.add(project)
            elif detail.get("skipped"):
                skipped.append(detail)
            else:
                errors.append(detail)
                if "range" in detail:
                    changed.add(project)
        maybe_push_history(registry, "start-all" if not also_defaults_only else "start-defaults", hist_key)
        return {
            "status": "ok",
            "started": started,
            "skipped": skipped,
            "errors": errors,
            "started_count": len(started),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "mode": "defaults" if also_defaults_only else "all",
        }, sorted(changed)

    return locked_registry(mutate)


def run_stop_bulk(mode="all"):
    """Stop running services. mode: all | non-default."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, None)
        ensure_baseline(registry, hist_key)
        stopped = []
        skipped = []
        errors = []
        changed = set()
        for project, item in list(iter_project_ranges(registry, None)):
            if mode == "non-default" and normalize_default_state(item.get("default_state")) == "on":
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "default_on",
                })
                continue
            live = range_has_live_process(item)
            is_active = item.get("state") == "active"
            if not live and not is_active:
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "not_running",
                })
                continue
            ok, detail = park_item_for_exit(item, project, also_release=False)
            detail = dict(detail)
            detail["project"] = project
            detail["range_id"] = item.get("id")
            if ok:
                stopped.append(detail)
                changed.add(project)
            else:
                errors.append(detail)
                if "range" in detail:
                    changed.add(project)
        label = "stop-all" if mode == "all" else "stop-non-default"
        maybe_push_history(registry, label, hist_key)
        return {
            "status": "ok",
            "stopped": stopped,
            "skipped": skipped,
            "errors": errors,
            "stopped_count": len(stopped),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "mode": mode,
        }, sorted(changed)

    return locked_registry(mutate)


def cmd_start(args):
    if bool(getattr(args, "all", False)):
        emit(run_start_all(also_defaults_only=False))
        return
    if not getattr(args, "range_id", None):
        fail("invalid_args", "start requires --range-id or --all")
    project = project_path(args.project)
    require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        item = find_project_range(registry, project, args.range_id)
        if item is None:
            fail("not_found", f"range id not found for project: {args.range_id}")
        if item.get("state") == "released":
            fail("range_released", "released ranges cannot be started; allocate a fresh range")
        if is_remote_range(registry, item):
            refuse_remote_process(registry, item, action="start")
        life = lifecycle(item)
        if pid_alive(life.get("pid")):
            activate_item(item, project, args.range_id, args.tailnet)
            maybe_push_history(registry, "start", hist_key)
            return {"status": "ok", "range": item}, [project]
        if life.get("pid") is not None:
            life["pid"] = None
            life["pgid"] = None
        recorded_mode = item.get("tailnet", {}).get("mode")
        if args.tailnet is None and recorded_mode is None:
            emit(needs_tailnet(project, args.range_id))
            raise SystemExit(3)
        cwd_override = normalize_optional_str(item.get("cwd")) or project
        command = normalize_optional_str(item.get("command"))
        start_script = resolve_project_relative(project, life["start_script"], "start_script")
        start_log = resolve_project_relative(project, life["start_log"], "start_log")
        if command:
            # Prefer explicit command when set (edit-mode field)
            start_log.parent.mkdir(parents=True, exist_ok=True)
            with start_log.open("a", encoding="utf-8") as log:
                try:
                    process = subprocess.Popen(
                        shlex.split(command),
                        cwd=cwd_override,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    fail("start_command_failed", str(exc))
        else:
            validate_start_script(start_script)
            start_log.parent.mkdir(parents=True, exist_ok=True)
            with start_log.open("a", encoding="utf-8") as log:
                try:
                    process = subprocess.Popen(
                        [str(start_script)],
                        cwd=cwd_override,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    fail("start_script_failed", str(exc))
        life["pid"] = process.pid
        try:
            life["pgid"] = os.getpgid(process.pid)
        except ProcessLookupError:
            life["pgid"] = process.pid
        life["started_at"] = utc_now()
        life["stopped_at"] = None
        activate_item(item, project, args.range_id, args.tailnet)
        maybe_push_history(registry, "start", hist_key)
        return {"status": "ok", "range": item}, [project]

    emit(locked_registry(mutate))


def cmd_stop(args):
    if bool(getattr(args, "all", False)):
        emit(run_stop_bulk(mode="all"))
        return
    if bool(getattr(args, "non_default", False)):
        emit(run_stop_bulk(mode="non-default"))
        return
    if not getattr(args, "range_id", None):
        fail("invalid_args", "stop requires --range-id, --all, or --non-default")
    project = project_path(args.project)
    require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        item = find_project_range(registry, project, args.range_id)
        if item is None:
            fail("not_found", f"range id not found for project: {args.range_id}")
        if item.get("state") == "released":
            return {"status": "ok", "range": item}, []
        life = lifecycle(item)
        has_live_pid = pid_alive(life.get("pid"))
        live_group_pids = process_group_pids(life.get("pgid")) if life.get("pgid") else []
        has_live_group = any(pid_alive(pid) for pid in live_group_pids)
        if has_live_pid or has_live_group:
            run_stop_script_if_ready(item, project)
            terminate_lifecycle_processes(item)
        else:
            life["pid"] = None
            life["pgid"] = None
            life["stopped_at"] = utc_now()
        release_item(item)
        maybe_push_history(registry, "stop", hist_key)
        return {"status": "ok", "range": item}, [project]

    emit(locked_registry(mutate))


def find_range_anywhere(registry, range_id, project=None):
    if project is not None:
        item = find_project_range(registry, project, range_id)
        if item is None:
            return None, None
        return project, item
    for project_path_key, project_entry in registry["projects"].items():
        for item in project_entry.get("ranges", []):
            if item.get("id") == range_id:
                return project_path_key, item
    return None, None


def find_matching_unreleased(registry, project, note):
    project_entry = registry["projects"].get(project) or {}
    for item in project_entry.get("ranges", []):
        if item.get("state") == "released":
            continue
        if (item.get("note") or None) == (note or None):
            return item
    return None


def iter_project_ranges(registry, project_filter=None):
    """Yield (project, item) for non-released ranges, optionally filtered by project path(s)."""
    filters = None
    if project_filter:
        filters = {project_path(p) for p in project_filter}
    for project, entry in registry["projects"].items():
        if filters is not None and project not in filters:
            continue
        for item in entry.get("ranges", []):
            if item.get("state") == "released":
                continue
            yield project, normalize_range_record(item)


def build_environment_payload(registry, name, description="", project_filter=None):
    services = []
    for project, item in iter_project_ranges(registry, project_filter):
        tailnet_mode = (item.get("tailnet") or {}).get("mode") or "none"
        count = int(item["end"]) - int(item["start"]) + 1
        services.append({
            "project": project,
            "note": item.get("note"),
            "count": count,
            "tailnet": tailnet_mode if tailnet_mode in ("serve", "funnel", "none") else "none",
            "default_state": normalize_default_state(item.get("default_state")),
            "range_id": item.get("id"),
        })
    return {
        "version": 1,
        "kind": "port-registry-environment",
        "name": name,
        "description": description or "",
        "exported_at": utc_now(),
        "services": services,
    }


def environment_payload_from_preset(registry, preset_name, export_name=None, description=None):
    presets = registry.get("presets") or {}
    if preset_name not in presets:
        fail("preset_not_found", f"preset not found: {preset_name}")
    preset = normalize_preset_entry(preset_name, presets[preset_name])
    name = export_name if export_name else preset["name"]
    desc = description if description is not None else preset.get("description") or ""
    return {
        "version": 1,
        "kind": "port-registry-environment",
        "name": name,
        "description": desc,
        "exported_at": utc_now(),
        "services": list(preset.get("services") or []),
        "from_preset": preset_name,
    }


def cmd_environment_export(args):
    project_filter = list(args.project) if args.project else None
    from_preset = getattr(args, "from_preset", None)

    def read(registry):
        if from_preset:
            payload = environment_payload_from_preset(
                registry,
                from_preset,
                export_name=args.name,
                description=getattr(args, "description", None),
            )
        else:
            payload = build_environment_payload(
                registry,
                name=args.name,
                description=getattr(args, "description", "") or "",
                project_filter=project_filter,
            )
        return payload, []

    payload = locked_registry(read)

    def compat_read(registry):
        return run_compat_check(registry, services=payload.get("services") or []), False, []

    try:
        compat = mutate_registry_meta(compat_read)
    except Exception:  # noqa: BLE001
        compat = {"compatible": True, "conflicts": []}
    if not compat.get("compatible"):
        settings_snap = mutate_registry_meta(
            lambda r: (normalize_settings(r.get("settings")), False, [])
        )
        if settings_snap.get("require_compat"):
            fail(
                "compat_conflict",
                f"Environment {payload.get('name')!r} has internal port conflicts; not exported",
                conflicts=compat.get("conflicts"),
                conflict_count=compat.get("conflict_count"),
            )
        payload["_compat_warning"] = {
            "reason": "compat_conflict",
            "conflicts": compat.get("conflicts"),
        }

    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        out_path = pathlib.Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(raw, encoding="utf-8")
        out_payload = {
            "status": "ok",
            "path": str(out_path.resolve()),
            "name": payload["name"],
            "service_count": len(payload["services"]),
            "compatible": bool(compat.get("compatible")),
        }
        if not compat.get("compatible"):
            out_payload["warnings"] = [{
                "reason": "compat_conflict",
                "message": "Services inside this environment conflict on ports",
                "conflicts": compat.get("conflicts"),
            }]
        emit(out_payload)
    else:
        # Portable JSON on stdout (not the usual one-line emit) when --out omitted
        print(raw, end="")


def load_environment_file(path):
    file_path = pathlib.Path(path).expanduser()
    if not file_path.exists():
        fail("environment_not_found", f"environment file not found: {file_path}")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("invalid_environment", f"environment JSON is corrupt: {exc.msg}")
    if not isinstance(data, dict):
        fail("invalid_environment", "environment root must be an object")
    if data.get("kind") not in (None, "port-registry-environment"):
        fail("invalid_environment", f"unexpected kind: {data.get('kind')}")
    services = data.get("services")
    if not isinstance(services, list):
        fail("invalid_environment", "services must be a list")
    return data, services


def import_services_into_registry(registry, services):
    """Create/update ranges for environment services. Returns (results, changed_projects)."""
    results = []
    changed = set()
    for index, service in enumerate(services):
        if not isinstance(service, dict):
            results.append({"index": index, "status": "error", "reason": "invalid_service"})
            continue
        project_raw = service.get("project")
        if not project_raw:
            results.append({"index": index, "status": "error", "reason": "missing_project"})
            continue
        try:
            project = project_path(project_raw)
        except (OSError, RuntimeError) as exc:
            results.append({"index": index, "status": "error", "reason": "invalid_project", "message": str(exc)})
            continue
        if not pathlib.Path(project).is_dir():
            results.append({
                "index": index,
                "status": "error",
                "reason": "project_not_found",
                "project": project,
            })
            continue
        note = service.get("note")
        default_state = normalize_default_state(service.get("default_state"))
        tailnet_mode = service.get("tailnet") or "none"
        if tailnet_mode not in ("serve", "funnel", "none"):
            tailnet_mode = "none"
        try:
            count = int(service.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        if count <= 0:
            count = 1
        range_id = service.get("range_id")
        item = None
        action = None
        if range_id:
            item = find_project_range(registry, project, range_id)
            if item is not None and item.get("state") == "released":
                item = None
        if item is None:
            item = find_matching_unreleased(registry, project, note)
            if item is not None:
                action = "reused"
        if item is None:
            scaffold_lifecycle_scripts(project)
            available = find_free_range(registry, count)
            if available is None:
                results.append({
                    "index": index,
                    "status": "error",
                    "reason": "pool_exhausted",
                    "project": project,
                    "note": note,
                })
                continue
            start, end = available
            project_entry = ensure_project(registry, project)
            item = range_record(
                next_range_id(project_entry),
                start,
                end,
                note,
                tailnet_mode,
                default_state=default_state,
            )
            project_entry["ranges"].append(item)
            action = "allocated"
            changed.add(project)
        else:
            item["default_state"] = default_state
            # Keep recorded tailnet if present; otherwise set from import
            item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
            if item["tailnet"].get("mode") is None:
                item["tailnet"]["mode"] = tailnet_mode
            normalize_range_record(item)
            action = action or "updated"
            changed.add(project)
        results.append({
            "index": index,
            "status": "ok",
            "action": action,
            "project": project,
            "note": note,
            "default_state": item["default_state"],
            "range": item,
        })
    return results, sorted(changed)


def cmd_environment_import(args):
    _data, services = load_environment_file(args.file)

    def mutate(registry):
        # Safety: auto-export current workspace before applying import
        try:
            backup_path = export_workspace_backup(registry, label="workspace")
        except OSError as exc:
            fail(
                "backup_failed",
                f"Import aborted: could not write workspace backup: {exc}",
            )
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        results, changed = import_services_into_registry(registry, services)
        maybe_push_history(registry, "environment import", hist_key)
        return {
            "status": "ok",
            "imported": len([r for r in results if r.get("status") == "ok"]),
            "failed": len([r for r in results if r.get("status") != "ok"]),
            "results": results,
            "backup_path": backup_path,
            "message": f"Import ok; previous workspace backed up to {backup_path}",
        }, changed

    payload = locked_registry(mutate)
    if args.apply_defaults:
        apply_payload = run_apply_defaults(
            project_filter=None,
            also_stop_off=False,
        )
        payload["apply_defaults"] = apply_payload
    emit(payload)


def cmd_set_default(args):
    state = normalize_default_state(args.state)
    project = project_path(args.project) if args.project else None
    if project is not None:
        require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        found_project, item = find_range_anywhere(registry, args.range_id, project)
        if item is None:
            fail("not_found", f"range id not found: {args.range_id}")
        item["default_state"] = state
        normalize_range_record(item)
        maybe_push_history(registry, "set-default", hist_key)
        return {"status": "ok", "range": item, "project": found_project}, [found_project]

    emit(locked_registry(mutate))


def cmd_set_defaults_from_current(args):
    """For every non-released local range: active -> default_state=on, else off."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        updated = []
        changed = set()
        for project, item in iter_project_ranges(registry, None):
            if is_remote_range(registry, item):
                continue
            state = item.get("state") or "reserved"
            desired = "on" if state == "active" else "off"
            prev = normalize_default_state(item.get("default_state"))
            if prev == desired:
                continue
            item["default_state"] = desired
            normalize_range_record(item)
            updated.append({
                "project": project,
                "range_id": item.get("id"),
                "state": state,
                "default_state": desired,
                "previous_default_state": prev,
            })
            changed.add(project)
        if updated:
            maybe_push_history(registry, "set-defaults-from-current", hist_key)
        return {
            "status": "ok",
            "updated": updated,
            "updated_count": len(updated),
            "message": f"Set default_state from current for {len(updated)} range(s)",
        }, sorted(changed)

    emit(locked_registry(mutate))


def start_item_for_apply(item, project, registry=None):
    """Start a range for apply-defaults. Returns (ok: bool, detail: dict). Does not emit/exit."""
    if item.get("state") == "released":
        return False, {"reason": "range_released", "range_id": item.get("id")}
    if registry is not None and is_remote_range(registry, item):
        return False, {
            "reason": "remote_machine",
            "range_id": item.get("id"),
            "machine_id": item.get("machine_id") or item.get("machine") or item.get("host_id"),
            "skipped": True,
            "message": "Remote ranges are links only; not started from this Mac",
        }
    life = lifecycle(item)
    if pid_alive(life.get("pid")) or range_has_live_process(item):
        # Already live — ensure active with recorded/none tailnet; no new process
        try:
            mode = (item.get("tailnet") or {}).get("mode") or "none"
            activate_item(item, project, item.get("id"), mode)
        except SystemExit:
            return False, {"reason": "activate_failed", "range_id": item.get("id")}
        return True, {"action": "already_live", "range": item}
    if life.get("pid") is not None:
        life["pid"] = None
        life["pgid"] = None
    mode = (item.get("tailnet") or {}).get("mode") or "none"
    try:
        start_script = resolve_project_relative(project, life["start_script"], "start_script")
        start_log = resolve_project_relative(project, life["start_log"], "start_log")
    except SystemExit:
        return False, {"reason": "invalid_lifecycle_path", "range_id": item.get("id")}
    if not start_script.exists():
        return False, {"reason": "start_script_missing", "range_id": item.get("id"), "path": str(start_script)}
    if script_is_placeholder(start_script):
        return False, {
            "reason": "start_script_placeholder",
            "range_id": item.get("id"),
            "path": str(start_script),
            "skipped": True,
        }
    if not start_script.is_file() or not os.access(start_script, os.X_OK):
        return False, {"reason": "start_script_invalid", "range_id": item.get("id"), "path": str(start_script)}
    start_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with start_log.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(start_script)],
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        return False, {"reason": "start_script_failed", "message": str(exc), "range_id": item.get("id")}
    life["pid"] = process.pid
    try:
        life["pgid"] = os.getpgid(process.pid)
    except ProcessLookupError:
        life["pgid"] = process.pid
    life["started_at"] = utc_now()
    life["stopped_at"] = None
    try:
        activate_item(item, project, item.get("id"), mode)
    except SystemExit:
        return False, {"reason": "activate_failed", "range_id": item.get("id"), "range": item}
    return True, {"action": "started", "range": item}


def stop_item_for_apply(item, project):
    """Stop a live Off range during apply-defaults --also-stop-off."""
    if item.get("state") == "released":
        return True, {"action": "already_released", "range": item}
    life = lifecycle(item)
    has_live_pid = pid_alive(life.get("pid"))
    live_group_pids = process_group_pids(life.get("pgid")) if life.get("pgid") else []
    has_live_group = any(pid_alive(pid) for pid in live_group_pids)
    if has_live_pid or has_live_group:
        run_stop_script_if_ready(item, project)
        terminate_lifecycle_processes(item)
    else:
        life["pid"] = None
        life["pgid"] = None
        life["stopped_at"] = utc_now()
    try:
        release_item(item)
    except SystemExit:
        return False, {"reason": "release_failed", "range_id": item.get("id")}
    return True, {"action": "stopped", "range": item}


def run_apply_defaults(
    project_filter=None,
    also_stop_off=False,
    *,
    session_preset=None,
    session_source="apply_defaults",
    record_session=True,
    enforce_overlap=True,
):
    filters = [project_path(p) for p in project_filter] if project_filter else None

    def mutate(registry):
        if enforce_overlap:
            # Port compatibility (not single-session).
            enforce_compat_or_fail(
                registry,
                include_default_on=True,
                project_filter=project_filter,
            )
        started = []
        skipped = []
        stopped = []
        errors = []
        changed = set()
        for project, item in list(iter_project_ranges(registry, filters)):
            default_state = normalize_default_state(item.get("default_state"))
            live = range_has_live_process(item)
            if default_state == "on":
                if live and item.get("state") == "active":
                    skipped.append({
                        "project": project,
                        "range_id": item.get("id"),
                        "reason": "already_active",
                    })
                    continue
                ok, detail = start_item_for_apply(item, project, registry=registry)
                detail = dict(detail)
                detail["project"] = project
                if ok:
                    started.append(detail)
                    changed.add(project)
                else:
                    if detail.get("skipped"):
                        skipped.append(detail)
                    else:
                        errors.append(detail)
                    # placeholder skip does not mutate; other partial may have
                    if "range" in detail:
                        changed.add(project)
            elif also_stop_off and live:
                ok, detail = stop_item_for_apply(item, project)
                detail = dict(detail)
                detail["project"] = project
                if ok:
                    stopped.append(detail)
                    changed.add(project)
                else:
                    errors.append(detail)
        payload = {
            "status": "ok",
            "started": started,
            "stopped": stopped,
            "skipped": skipped,
            "errors": errors,
            "started_count": len(started),
            "stopped_count": len(stopped),
            "skipped_count": len(skipped),
            "error_count": len(errors),
        }
        if record_session:
            active = begin_active_session(registry, session_preset, session_source)
            payload["session"] = active
            changed.add("__sessions__")
        return payload, sorted(changed)

    return locked_registry(mutate)


def cmd_apply_defaults(args):
    project_filter = list(args.project) if args.project else None
    also_stop_off = bool(getattr(args, "also_stop_off", False))
    payload = run_apply_defaults(
        project_filter=project_filter,
        also_stop_off=also_stop_off,
        session_preset=None,
        session_source="apply_defaults",
        record_session=True,
        enforce_overlap=True,
    )
    emit(payload)


def item_matches_preset_service(project, item, service):
    """Match a live range to a preset service by range_id or project+note."""
    svc_raw = service.get("project")
    if not isinstance(svc_raw, str) or not svc_raw.strip():
        return False
    try:
        svc_project = project_path(svc_raw)
    except SystemExit:
        svc_project = svc_raw
    proj = project_path(project) if not str(project).startswith("/") else str(project)
    # project keys in registry are already normalized absolute paths
    if str(project) != str(svc_project) and proj != str(svc_project) and str(project) != svc_raw:
        return False
    rid = service.get("range_id")
    if isinstance(rid, str) and rid.strip():
        return item.get("id") == rid.strip()
    return (item.get("note") or None) == (service.get("note") or None)


def park_item_for_exit(item, project, also_release=False):
    """Safe stop for deactivate: stop.sh + tracked pgid terminate + tailnet off.

    Default keeps the range reserved (allocation retained). With also_release,
    fully release via release_item (same as stop).
    """
    if item.get("state") == "released":
        return True, {"action": "already_released", "range": item}
    life = lifecycle(item)
    has_live = range_has_live_process(item)
    if has_live:
        run_stop_script_if_ready(item, project)
        terminate_lifecycle_processes(item)
    else:
        life["pid"] = None
        life["pgid"] = None
        if not life.get("stopped_at"):
            life["stopped_at"] = utc_now()
    if also_release:
        try:
            release_item(item)
        except SystemExit:
            return False, {"reason": "release_failed", "range_id": item.get("id")}
        return True, {"action": "stopped_released", "range": item}
    # Tear down tailnet but keep allocation reserved
    tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    mode = tailnet.get("mode")
    if item.get("state") == "active" and mode in ("serve", "funnel"):
        port = tailnet.get("port") or item["start"]
        try:
            run_tailnet(mode, port, off=True)
        except SystemExit:
            return False, {"reason": "tailnet_teardown_failed", "range_id": item.get("id")}
        tailnet["configured_at"] = None
    item["state"] = "reserved"
    return True, {"action": "stopped_reserved", "range": item}


def run_exit_house(project_filter=None, preset_name=None, also_release=False, clear_session=True):
    """Stop Default On cohort (or preset-matched running services).

    Safe path only: project stop.sh if ready + terminate tracked pgids + tailnet off.
    Default keeps ranges reserved unless also_release.
    """
    filters = [project_path(p) for p in project_filter] if project_filter else None
    preset_name = (preset_name or "").strip() or None

    def mutate(registry):
        stopped = []
        skipped = []
        errors = []
        changed = set()
        preset_services = None
        if preset_name:
            presets = registry.get("presets") or {}
            if preset_name not in presets:
                fail("preset_not_found", f"preset not found: {preset_name}")
            preset_services = list(
                normalize_preset_entry(preset_name, presets[preset_name]).get("services") or []
            )

        for project, item in list(iter_project_ranges(registry, filters)):
            if preset_services is not None:
                matched = any(
                    item_matches_preset_service(project, item, svc) for svc in preset_services
                )
                if not matched:
                    continue
            else:
                if normalize_default_state(item.get("default_state")) != "on":
                    continue
            live = range_has_live_process(item)
            is_active = item.get("state") == "active"
            if not live and not is_active:
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "not_running",
                })
                continue
            ok, detail = park_item_for_exit(item, project, also_release=also_release)
            detail = dict(detail)
            detail["project"] = project
            if ok:
                stopped.append(detail)
                changed.add(project)
            else:
                errors.append(detail)
                if "range" in detail:
                    changed.add(project)
        payload = {
            "status": "ok",
            "stopped": stopped,
            "skipped": skipped,
            "errors": errors,
            "stopped_count": len(stopped),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "also_release": bool(also_release),
            "preset": preset_name,
        }
        # Clear active session on full deactivate (no project/preset filter)
        if clear_session and not filters and not preset_name:
            closed = end_active_session(registry)
            payload["session_closed"] = closed
            changed.add("__sessions__")
        elif clear_session and preset_name:
            # Preset-scoped deactivate closes active only when it matches
            sessions = normalize_sessions(registry.get("sessions"))
            active = sessions.get("active")
            if active and active.get("preset") == preset_name:
                closed = end_active_session(registry)
                payload["session_closed"] = closed
                changed.add("__sessions__")
        return payload, sorted(changed)

    return locked_registry(mutate)


def cmd_deactivate(args):
    project_filter = list(args.project) if getattr(args, "project", None) else None
    preset_name = getattr(args, "preset", None)
    also_release = bool(getattr(args, "also_release", False))
    payload = run_exit_house(
        project_filter=project_filter,
        preset_name=preset_name,
        also_release=also_release,
        clear_session=True,
    )
    emit(payload)


# Compat alias
cmd_exit_house = cmd_deactivate


def services_from_environment_file(path):
    data, services = load_environment_file(path)
    return data, services


def mutate_registry_meta(mutator):
    """Like locked_registry but can write meta-only (presets/settings) without local mirrors."""
    path = registry_path()
    start, end = pool_bounds()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        raw_obj = None
        if raw.strip():
            try:
                registry = json.loads(raw)
                raw_obj = registry if isinstance(registry, dict) else None
            except json.JSONDecodeError as exc:
                fail(
                    "corrupt_registry",
                    f"registry JSON is corrupt at {path}: {exc.msg} (line {exc.lineno} col {exc.colno}); "
                    "fix or restore the file — refusing to wipe",
                )
        else:
            registry = initial_registry(start, end)
        needs_schema_persist = raw_obj is None or not isinstance(raw_obj.get("presets"), dict) or not isinstance(raw_obj.get("settings"), dict) or not isinstance(raw_obj.get("sessions"), dict)
        registry = normalize_registry(registry, start, end)
        result, should_write, changed_projects = mutator(registry)
        if should_write or needs_schema_persist:
            handle.seek(0)
            handle.truncate()
            json.dump(registry, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            for project in changed_projects or []:
                if project and not str(project).startswith("__"):
                    write_local_project_file(project, registry)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return result


def cmd_preset_save(args):
    name = (args.name or "").strip()
    if not name:
        fail("invalid_args", "--name is required")
    description = getattr(args, "description", "") or ""
    project_filter = list(args.project) if args.project else None
    from_file = getattr(args, "from_file", None)
    empty = bool(getattr(args, "empty", False))

    def mutate(registry):
        if empty and from_file:
            fail("invalid_args", "use either --empty or --from-file, not both")
        if empty:
            services = []
            use_description = description or "New environment"
            entry = {
                "name": name,
                "description": use_description,
                "updated_at": utc_now(),
                "services": services,
            }
            registry.setdefault("presets", {})
            registry["presets"][name] = normalize_preset_entry(name, entry)
            settings = normalize_settings(registry.get("settings"))
            tabs = list(settings.get("open_environment_tabs") or [])
            if name not in tabs:
                tabs.append(name)
            settings["open_environment_tabs"] = tabs
            settings["focused_environment"] = name
            registry["settings"] = normalize_settings(settings)
            # New empty env: baseline now (Original); no step yet
            ensure_baseline(registry, name)
            return {
                "status": "ok",
                "preset": registry["presets"][name],
                "service_count": 0,
                "compatible": True,
                "empty": True,
                "settings": registry["settings"],
            }, True, []
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None) or name)
        ensure_baseline(registry, hist_key)
        if from_file:
            data, services_raw = services_from_environment_file(from_file)
            services = []
            for item in services_raw:
                normalized = normalize_preset_service(item)
                if normalized is not None:
                    services.append(normalized)
            if not description and isinstance(data.get("description"), str):
                use_description = data.get("description") or ""
            else:
                use_description = description
        else:
            payload = build_environment_payload(
                registry,
                name=name,
                description=description,
                project_filter=project_filter,
            )
            services = []
            for item in payload["services"]:
                normalized = normalize_preset_service(item)
                if normalized is not None:
                    services.append(normalized)
            use_description = description
        entry = {
            "name": name,
            "description": use_description,
            "updated_at": utc_now(),
            "services": services,
        }
        registry.setdefault("presets", {})
        registry["presets"][name] = normalize_preset_entry(name, entry)
        registry["settings"] = normalize_settings(registry.get("settings"))
        report = run_compat_check(registry, services=services)
        if not report.get("compatible") and registry["settings"].get("require_compat"):
            fail(
                "compat_conflict",
                f"Preset {name!r} has internal port conflicts; not saved",
                conflicts=report.get("conflicts"),
                conflict_count=report.get("conflict_count"),
                hint="Fix overlapping services, or: settings set --require-compat off",
            )
        result = {
            "status": "ok",
            "preset": registry["presets"][name],
            "service_count": len(services),
            "compatible": bool(report.get("compatible")),
        }
        if not report.get("compatible"):
            result["warnings"] = [{
                "reason": "compat_conflict",
                "message": "Services inside this preset conflict on ports",
                "conflicts": report.get("conflicts"),
            }]
        # History for this env (or focused) when services rewritten
        maybe_push_history(registry, "preset save", hist_key)
        return result, True, []

    emit(mutate_registry_meta(mutate))


def cmd_preset_list(args):
    def read(registry):
        presets = registry.get("presets") or {}
        items = []
        for name in sorted(presets.keys()):
            entry = normalize_preset_entry(name, presets[name])
            items.append({
                "name": entry["name"],
                "description": entry.get("description") or "",
                "updated_at": entry.get("updated_at"),
                "service_count": len(entry.get("services") or []),
            })
        return {"status": "ok", "presets": items, "count": len(items)}, False, []

    emit(mutate_registry_meta(read))


def cmd_preset_show(args):
    name = (args.name or "").strip()
    if not name:
        fail("invalid_args", "--name is required")

    def read(registry):
        presets = registry.get("presets") or {}
        if name not in presets:
            fail("preset_not_found", f"preset not found: {name}")
        entry = normalize_preset_entry(name, presets[name])
        return {"status": "ok", "preset": entry}, False, []

    emit(mutate_registry_meta(read))


def cmd_preset_delete(args):
    name = (args.name or "").strip()
    if not name:
        fail("invalid_args", "--name is required")

    def mutate(registry):
        presets = registry.setdefault("presets", {})
        if name not in presets:
            fail("preset_not_found", f"preset not found: {name}")
        del presets[name]
        settings = registry.setdefault("settings", default_settings())
        if settings.get("auto_apply_preset") == name:
            settings["auto_apply_preset"] = None
            registry["settings"] = normalize_settings(settings)
        return {"status": "ok", "deleted": name}, True, []

    emit(mutate_registry_meta(mutate))


def run_preset_apply(name, also_stop_off=False):
    name = (name or "").strip()
    if not name:
        fail("invalid_args", "preset name is required")

    def mutate(registry):
        presets = registry.get("presets") or {}
        if name not in presets:
            fail("preset_not_found", f"preset not found: {name}")
        entry = normalize_preset_entry(name, presets[name])
        services = entry.get("services") or []
        enforce_compat_or_fail(
            registry,
            preset_names=[name],
            include_default_on=True,
        )
        results, changed = import_services_into_registry(registry, services)
        return {
            "status": "ok",
            "preset": name,
            "imported": len([r for r in results if r.get("status") == "ok"]),
            "failed": len([r for r in results if r.get("status") != "ok"]),
            "results": results,
        }, True, changed

    payload = mutate_registry_meta(mutate)
    apply_payload = run_apply_defaults(
        project_filter=None,
        also_stop_off=also_stop_off,
        session_preset=name,
        session_source="preset_apply",
        record_session=True,
        enforce_overlap=False,  # already checked above
    )
    payload["apply_defaults"] = apply_payload
    if apply_payload.get("session"):
        payload["session"] = apply_payload["session"]
    return payload


def cmd_preset_apply(args):
    also_stop_off = bool(getattr(args, "also_stop_off", False))
    emit(run_preset_apply(args.name, also_stop_off=also_stop_off))


def cmd_preset_check(args):
    names = list(args.name) if getattr(args, "name", None) else []
    if not names:
        fail("invalid_args", "preset check requires at least one --name")

    def read(registry):
        return run_compat_check(registry, preset_names=names), False, []

    report = mutate_registry_meta(read)
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


def cmd_preset(args):
    cmd = args.preset_command
    if cmd == "save":
        return cmd_preset_save(args)
    if cmd == "list":
        return cmd_preset_list(args)
    if cmd == "show":
        return cmd_preset_show(args)
    if cmd == "delete":
        return cmd_preset_delete(args)
    if cmd == "apply":
        return cmd_preset_apply(args)
    if cmd == "check":
        return cmd_preset_check(args)
    fail("invalid_args", "preset requires save|list|show|delete|apply|check")


def cmd_settings_get(args):
    def read(registry):
        settings = normalize_settings(registry.get("settings"))
        return {"status": "ok", "settings": settings}, False, []

    emit(mutate_registry_meta(read))


def cmd_settings_set(args):
    def mutate(registry):
        settings = normalize_settings(registry.get("settings"))
        changed = False
        # Combined shortcut: --auto-apply NAME|off
        auto_apply = getattr(args, "auto_apply", None)
        if auto_apply is not None:
            token = str(auto_apply).strip()
            if token.lower() in ("off", "none", "null", ""):
                settings["auto_apply_preset"] = None
                settings["auto_apply_on_launch"] = False
            else:
                settings["auto_apply_preset"] = token
                settings["auto_apply_on_launch"] = True
            changed = True
        if getattr(args, "auto_apply_preset", None) is not None:
            token = str(args.auto_apply_preset).strip()
            if token.lower() in ("none", "off", "null", ""):
                settings["auto_apply_preset"] = None
            else:
                settings["auto_apply_preset"] = token
            changed = True
        if getattr(args, "auto_apply_on_launch", None) is not None:
            token = str(args.auto_apply_on_launch).strip().lower()
            if token not in ("on", "off"):
                fail("invalid_args", "--auto-apply-on-launch must be on|off")
            settings["auto_apply_on_launch"] = token == "on"
            changed = True
        if getattr(args, "auto_exit_on_shutdown", None) is not None:
            token = str(args.auto_exit_on_shutdown).strip().lower()
            if token not in ("on", "off"):
                fail("invalid_args", "--auto-exit-on-shutdown must be on|off")
            settings["auto_exit_on_shutdown"] = token == "on"
            changed = True
        if getattr(args, "overlap_policy", None) is not None:
            token = str(args.overlap_policy).strip().lower()
            if token not in ("allow", "deny"):
                fail("invalid_args", "--overlap-policy must be allow|deny")
            settings["overlap_policy"] = token
            changed = True
        if getattr(args, "require_compat", None) is not None:
            token = str(args.require_compat).strip().lower()
            if token not in ("on", "off"):
                fail("invalid_args", "--require-compat must be on|off")
            # Always on — ignore off attempts; keep flag for CLI compat
            settings["require_compat"] = True
            changed = True
        if getattr(args, "open_environment_tabs", None) is not None:
            raw = args.open_environment_tabs
            if isinstance(raw, str):
                if raw.strip().lower() in ("", "none", "off", "[]"):
                    tabs = []
                else:
                    tabs = [p.strip() for p in raw.split(",") if p.strip()]
            elif isinstance(raw, list):
                tabs = [str(p).strip() for p in raw if str(p).strip()]
            else:
                fail("invalid_args", "--open-environment-tabs must be comma-separated names or none")
            settings["open_environment_tabs"] = tabs
            changed = True
        if getattr(args, "focused_environment", None) is not None:
            token = str(args.focused_environment).strip()
            if token.lower() in ("none", "off", "null", ""):
                settings["focused_environment"] = None
            else:
                settings["focused_environment"] = token
            changed = True
        if getattr(args, "open_tab", None) is not None:
            name = str(args.open_tab).strip()
            if not name:
                fail("invalid_args", "--open-tab requires a name")
            tabs = list(settings.get("open_environment_tabs") or [])
            if name not in tabs:
                tabs.append(name)
            settings["open_environment_tabs"] = tabs
            settings["focused_environment"] = name
            changed = True
        if getattr(args, "close_tab", None) is not None:
            name = str(args.close_tab).strip()
            tabs = [t for t in (settings.get("open_environment_tabs") or []) if t != name]
            settings["open_environment_tabs"] = tabs
            if settings.get("focused_environment") == name:
                settings["focused_environment"] = tabs[-1] if tabs else None
            changed = True
        # Per-tool MCP prefs: --mcp-tool NAME=on|off (repeatable)
        mcp_tool_args = getattr(args, "mcp_tool", None) or []
        if mcp_tool_args:
            tools = dict(settings.get("mcp_tools") or {})
            for item in mcp_tool_args:
                token = str(item).strip()
                if "=" not in token:
                    fail("invalid_args", "--mcp-tool requires NAME=on|off")
                name, _, state = token.partition("=")
                name = name.strip()
                state = state.strip().lower()
                if not name:
                    fail("invalid_args", "--mcp-tool requires a tool name")
                if state not in ("on", "off"):
                    fail("invalid_args", "--mcp-tool state must be on|off")
                tools[name] = state == "on"
            settings["mcp_tools"] = tools
            changed = True
        if getattr(args, "serve_portskill_on_tailscale", None) is not None:
            token = str(args.serve_portskill_on_tailscale).strip().lower()
            if token not in ("on", "off"):
                fail("invalid_args", "--serve-portskill-on-tailscale must be on|off")
            settings["serve_portskill_on_tailscale"] = token == "on"
            changed = True
        mcp_user_commands_json = getattr(args, "mcp_user_commands_json", None)
        if mcp_user_commands_json is not None:
            raw = str(mcp_user_commands_json).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                fail("invalid_args", f"--mcp-user-commands-json must be valid JSON: {exc}")
            if not isinstance(parsed, dict):
                fail("invalid_args", "--mcp-user-commands-json must be a JSON object")
            settings["mcp_user_commands"] = normalize_mcp_user_commands(parsed)
            changed = True
        mcp_user_command_upsert = getattr(args, "mcp_user_command_upsert", None)
        if mcp_user_command_upsert is not None:
            raw = str(mcp_user_command_upsert).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                fail("invalid_args", f"--mcp-user-command-upsert must be valid JSON: {exc}")
            if not isinstance(parsed, dict):
                fail("invalid_args", "--mcp-user-command-upsert must be a JSON object")
            cmds = dict(settings.get("mcp_user_commands") or {})
            one = normalize_mcp_user_commands({parsed.get("name") or "cmd": parsed})
            if not one:
                fail("invalid_args", "user command needs name + at least one valid step")
            cmds.update(one)
            settings["mcp_user_commands"] = normalize_mcp_user_commands(cmds)
            changed = True
        mcp_user_command_delete = getattr(args, "mcp_user_command_delete", None)
        if mcp_user_command_delete is not None:
            name = str(mcp_user_command_delete).strip()
            if not name:
                fail("invalid_args", "--mcp-user-command-delete requires a name")
            cmds = dict(settings.get("mcp_user_commands") or {})
            cmds.pop(name, None)
            settings["mcp_user_commands"] = normalize_mcp_user_commands(cmds)
            # Drop orphan enable flag for this user command
            tools = dict(settings.get("mcp_tools") or {})
            if name in tools:
                tools.pop(name, None)
                settings["mcp_tools"] = tools
            changed = True
        if getattr(args, "handoff_enabled", None) is not None:
            token = str(args.handoff_enabled).strip().lower()
            if token not in ("on", "off"):
                fail("invalid_args", "--handoff-enabled must be on|off")
            settings["handoff_enabled"] = token == "on"
            changed = True
        if getattr(args, "handoff_kit", None) is not None:
            token = str(args.handoff_kit).strip()
            if token.lower() in ("none", "off", "null", ""):
                settings["handoff_kit"] = None
            else:
                settings["handoff_kit"] = token
            changed = True
        mcp_tools_json = getattr(args, "mcp_tools_json", None)
        if mcp_tools_json is not None:
            raw = str(mcp_tools_json).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                fail("invalid_args", f"--mcp-tools-json must be valid JSON: {exc}")
            if not isinstance(parsed, dict):
                fail("invalid_args", "--mcp-tools-json must be a JSON object")
            tools = dict(settings.get("mcp_tools") or {})
            for key, val in parsed.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                name = key.strip()
                if isinstance(val, bool):
                    tools[name] = val
                elif isinstance(val, str):
                    token = val.strip().lower()
                    if token in ("on", "off"):
                        tools[name] = token == "on"
                    else:
                        tools[name] = token in ("1", "true", "yes")
                else:
                    tools[name] = bool(val)
            settings["mcp_tools"] = tools
            changed = True
        if not changed:
            fail(
                "invalid_args",
                "settings set requires a recognized flag (--auto-apply-preset, tabs, focus, --mcp-tool, …)",
            )
        # Validate preset exists when enabling launch apply with a name
        preset_name = settings.get("auto_apply_preset")
        if settings.get("auto_apply_on_launch") and preset_name:
            presets = registry.get("presets") or {}
            if preset_name not in presets:
                fail("preset_not_found", f"preset not found: {preset_name}")
        settings["require_compat"] = True
        registry["settings"] = normalize_settings(settings)
        return {"status": "ok", "settings": registry["settings"]}, True, []

    emit(mutate_registry_meta(mutate))


def cmd_settings(args):
    if args.settings_command == "get":
        return cmd_settings_get(args)
    if args.settings_command == "set":
        return cmd_settings_set(args)
    fail("invalid_args", "settings requires get or set")


def soft_preset_apply(name, also_stop_off=False):
    """Apply preset without SystemExit on missing preset / import issues. Returns payload dict."""
    name = (name or "").strip()
    if not name:
        return {"status": "error", "reason": "invalid_args", "message": "preset name is required"}

    def mutate(registry):
        settings = normalize_settings(registry.get("settings"))
        if settings.get("require_compat"):
            report = run_compat_check(
                registry, preset_names=[name], include_default_on=True
            )
            if not report.get("compatible"):
                return {
                    "status": "error",
                    "reason": "compat_conflict",
                    "message": "Selected environments are not compatible (port conflict)",
                    "conflicts": report.get("conflicts"),
                    "hint": "Run: compat check --preset NAME … or settings set --require-compat off",
                    "preset": name,
                }, False, []
        presets = registry.get("presets") or {}
        if name not in presets:
            return {
                "status": "error",
                "reason": "preset_not_found",
                "message": f"preset not found: {name}",
            }, False, []
        entry = normalize_preset_entry(name, presets[name])
        services = entry.get("services") or []
        results, changed = import_services_into_registry(registry, services)
        return {
            "status": "ok",
            "preset": name,
            "imported": len([r for r in results if r.get("status") == "ok"]),
            "failed": len([r for r in results if r.get("status") != "ok"]),
            "results": results,
        }, True, changed

    try:
        payload = mutate_registry_meta(mutate)
    except SystemExit as exc:
        return {
            "status": "error",
            "reason": "auto_apply_failed",
            "preset": name,
            "exit_code": getattr(exc, "code", 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "reason": "auto_apply_failed",
            "preset": name,
            "message": str(exc),
        }
    if payload.get("status") != "ok":
        return payload
    try:
        apply_payload = run_apply_defaults(
            project_filter=None,
            also_stop_off=also_stop_off,
            session_preset=name,
            session_source="preset_apply",
            record_session=True,
            enforce_overlap=False,
        )
        payload["apply_defaults"] = apply_payload
        if apply_payload.get("session"):
            payload["session"] = apply_payload["session"]
    except SystemExit as exc:
        payload["apply_defaults"] = {
            "status": "error",
            "reason": "apply_defaults_failed",
            "exit_code": getattr(exc, "code", 2),
        }
        payload["warnings"] = payload.get("warnings") or []
        payload["warnings"].append("apply_defaults raised SystemExit")
    except Exception as exc:  # noqa: BLE001
        payload["apply_defaults"] = {
            "status": "error",
            "reason": "apply_defaults_failed",
            "message": str(exc),
        }
        payload["warnings"] = payload.get("warnings") or []
        payload["warnings"].append(str(exc))
    # Surface placeholder/start issues as warnings without failing the whole launch
    apply = payload.get("apply_defaults") or {}
    warnings = list(payload.get("warnings") or [])
    for err in apply.get("errors") or []:
        warnings.append(err)
    for skip in apply.get("skipped") or []:
        if skip.get("reason") == "start_script_placeholder" or skip.get("skipped"):
            warnings.append(skip)
    if warnings:
        payload["warnings"] = warnings
    payload["auto_apply"] = True
    return payload


def maybe_auto_apply_on_launch():
    """Apply configured preset once at app launch. Returns payload or None if disabled.
    Never crashes the server on placeholder start failures — collect warnings.
    """
    def read(registry):
        settings = normalize_settings(registry.get("settings"))
        return settings, False, []

    try:
        settings = mutate_registry_meta(read)
    except SystemExit:
        # Corrupt registry — re-raise so launch can decide; usually fail() already emitted
        raise
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": "auto_apply_read_failed", "message": str(exc)}
    if not settings.get("auto_apply_on_launch"):
        return None
    name = settings.get("auto_apply_preset")
    if not name:
        return {
            "status": "skipped",
            "reason": "no_auto_apply_preset",
            "settings": settings,
        }
    return soft_preset_apply(name, also_stop_off=False)


def cmd_schedule(args):
    focus = getattr(args, "focus", None)

    def read(registry):
        return build_schedule_windows(registry, focus=focus), False, []

    emit(mutate_registry_meta(read))


def cmd_environment_check(args):
    names = list(getattr(args, "name", None) or [])
    file_path = getattr(args, "file", None)
    include_default_on = bool(getattr(args, "include_default_on", False))

    def read(registry):
        services = None
        if file_path:
            _data, services = load_environment_file(file_path)
        report = run_compat_check(
            registry,
            preset_names=names or None,
            services=services,
            include_default_on=include_default_on or (not names and not file_path),
        )
        return report, False, []

    report = mutate_registry_meta(read)
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


def cmd_environment(args):
    if args.env_command == "export":
        return cmd_environment_export(args)
    if args.env_command == "import":
        return cmd_environment_import(args)
    if args.env_command == "check":
        return cmd_environment_check(args)
    fail("invalid_args", "environment requires export|import|check")


def cmd_compat(args):
    """compat check — port-conflict report for selected presets/environments."""
    names = []
    if getattr(args, "preset", None):
        names.extend(list(args.preset))
    if getattr(args, "name", None):
        names.extend(list(args.name))
    presets_csv = getattr(args, "presets", None)
    if isinstance(presets_csv, str) and presets_csv.strip():
        names.extend([p.strip() for p in presets_csv.split(",") if p.strip()])
    file_path = getattr(args, "file", None)
    include_default_on = bool(getattr(args, "include_default_on", False))

    def read(registry):
        services = None
        if file_path:
            _data, services = load_environment_file(file_path)
        include = include_default_on or (not names and services is None)
        report = run_compat_check(
            registry,
            preset_names=names or None,
            services=services,
            include_default_on=include,
        )
        return report, False, []

    report = mutate_registry_meta(read)
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


def package_root():
    """Distribution root containing port_registry_app/, ui/, skill/, etc."""
    return pathlib.Path(__file__).resolve().parent.parent


def skill_dir():
    """Prefer package root (app layout); fall back to module parent for thin wrappers."""
    root = package_root()
    if (root / "port_registry_app").is_dir() or (root / "pyproject.toml").exists():
        return root
    return pathlib.Path(__file__).resolve().parent



def cmd_set_tailnet(args):
    """Update recorded tailnet mode; apply/tear down Serve when range is active.

    UI toggles Serve on/off (serve|none). Funnel remains available via --mode funnel.
    """
    mode = args.mode
    if mode not in ("serve", "funnel", "none"):
        fail("invalid_args", f"mode must be serve|funnel|none, got {mode!r}")
    project = project_path(args.project) if args.project else None
    if project is not None:
        require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, getattr(args, "environment", None))
        ensure_baseline(registry, hist_key)
        found_project, item = find_range_anywhere(registry, args.range_id, project)
        if item is None:
            fail("not_found", f"range id not found: {args.range_id}")
        normalize_range_record(item)
        tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
        old_mode = tailnet.get("mode") or "none"
        if old_mode not in ("serve", "funnel", "none"):
            old_mode = "none"

        if mode in ("serve", "funnel"):
            ensure_tailscale_authenticated(mode)

        tailnet["mode"] = mode
        now = utc_now()
        if item.get("state") == "active":
            if mode in ("serve", "funnel"):
                port = item["start"]
                # If switching modes while live, turn off previous first
                if old_mode in ("serve", "funnel") and old_mode != mode:
                    prev_port = tailnet.get("port") or item["start"]
                    try:
                        run_tailnet(old_mode, prev_port, off=True)
                    except SystemExit:
                        pass
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
        else:
            if mode == "none":
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

    emit(locked_registry(mutate))



def read_listen_port_from_disk():
    """Return Portskill listen port from sticky listen.json, or None."""
    path = listen_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None


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
    pref = False
    try:
        # Best-effort preference read without locking
        path = pathlib.Path(os.environ.get("PORT_REGISTRY_PATH", "~/.config/port-registry/registry.json")).expanduser()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            settings = data.get("settings") if isinstance(data, dict) else {}
            if isinstance(settings, dict):
                pref = bool(normalize_settings(settings).get("serve_portskill_on_tailscale"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
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
        fail("no_listen_port", "Cannot Serve Portskill: listen.json port missing")
    available, ts_bin = tailscale_bin_available()
    if not available:
        fail(
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
        except SystemExit:
            # Teardown best-effort if mapping already gone
            pass
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


def cmd_tailscale_serve_portskill(args):
    """Serve (or stop serving) the Portskill listen port via Tailscale Serve — never Funnel."""
    state = (getattr(args, "state", None) or "status").strip().lower()
    persist = not bool(getattr(args, "no_persist", False))
    if state == "status":
        emit({"status": "ok", **probe_portskill_serve_status()})
        return
    if state not in ("on", "off"):
        fail("invalid_args", "--state must be on|off|status")
    payload = apply_portskill_tailscale_serve(state == "on", persist=persist)
    emit(payload)


def cmd_tailscale_status(args):
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
    emit(payload)
    if status.get("state") == "binary_missing":
        raise SystemExit(2)
    # NeedsLogin is a valid status shape — exit 0 so UI/MCP can read JSON cleanly
    return


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


def cmd_tailscale_login(args):
    """Browser Login flow: extract auth URL, open system browser, poll until Connected.

    Does not hang on interactive password/sudo prompts. Serve never needs sudo;
    Funnel may require elevation separately (CLI --mode funnel only).
    """
    open_browser = not bool(getattr(args, "no_open", False))
    wait = getattr(args, "wait", None)
    if wait is None:
        wait = 90
    try:
        wait = int(wait)
    except (TypeError, ValueError):
        wait = 90

    status = probe_tailscale_status()
    if status.get("state") == "binary_missing":
        fail(
            "tailscale_bin_missing",
            status.get("message") or "Tailscale binary not found",
            tailscale=status,
        )
    if status.get("logged_in"):
        emit({
            "status": "ok",
            "already_logged_in": True,
            "logged_in": True,
            "chip": status.get("chip"),
            "BackendState": status.get("BackendState"),
            "Self": status.get("Self"),
            "browser_opened": False,
            "tailscale": status,
        })
        return

    auth_url = status.get("AuthURL")
    auth_url, outputs = obtain_tailscale_auth_url(auth_url)
    after_attempt = probe_tailscale_status()
    if after_attempt.get("logged_in"):
        emit({
            "status": "ok",
            "logged_in": True,
            "chip": after_attempt.get("chip"),
            "BackendState": after_attempt.get("BackendState"),
            "Self": after_attempt.get("Self"),
            "attempts": outputs,
            "browser_opened": False,
            "tailscale": after_attempt,
        })
        return
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
        emit({
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
        raise SystemExit(3)

    if wait <= 0:
        emit({
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
        raise SystemExit(3)

    ok_poll, final = poll_tailscale_running(timeout_sec=wait, interval_sec=2.0)
    if ok_poll:
        emit({
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
        })
        return

    emit({
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
    raise SystemExit(3)


def cmd_tailscale_logout(args):
    """Log out of Tailscale (CLI wrapper around `tailscale logout`)."""
    if not tailscale_bin_available():
        fail("tailscale_missing", "Tailscale binary not found; install Tailscale first")
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
    except subprocess.TimeoutExpired:
        fail("tailscale_logout_timeout", "tailscale logout timed out")
    except OSError as exc:
        fail("tailscale_logout_failed", str(exc))
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
    emit(payload)
    if result.returncode != 0:
        raise SystemExit(2)


def _live_range_lifecycle_fields(item):
    """Current registry values for draft comparison (normalized optional strings)."""
    life = lifecycle(item) if item else {}
    return {
        "start_script": normalize_optional_str(life.get("start_script")),
        "stop_script": normalize_optional_str(life.get("stop_script")),
        "command": normalize_optional_str((item or {}).get("command")),
        "cwd": normalize_optional_str((item or {}).get("cwd")),
    }


def cmd_workspace_draft_set(args):
    range_id = (getattr(args, "range_id", None) or "").strip()
    if not range_id:
        fail("invalid_args", "--range-id required")

    def mutate(registry):
        draft = normalize_workspace_draft(registry.get("workspace_draft"))
        entry = dict(draft["ranges"].get(range_id) or {})
        for key in ("start_script", "stop_script", "command", "cwd"):
            val = getattr(args, key, None)
            if val is not None:
                entry[key] = normalize_optional_str(val)
        _proj, item = find_range_anywhere(registry, range_id, None)
        live = _live_range_lifecycle_fields(item) if item is not None else {}
        staged = {
            "start_script": entry.get("start_script", live.get("start_script")),
            "stop_script": entry.get("stop_script", live.get("stop_script")),
            "command": entry.get("command", live.get("command")),
            "cwd": entry.get("cwd", live.get("cwd")),
        }
        noop = item is not None and all(
            staged.get(k) == live.get(k) for k in ("start_script", "stop_script", "command", "cwd")
        )
        if noop:
            draft["ranges"].pop(range_id, None)
            if not draft["ranges"]:
                draft["edit_mode"] = False
            registry["workspace_draft"] = normalize_workspace_draft(draft)
            return {
                "status": "ok",
                "range_id": range_id,
                "draft": None,
                "noop": True,
                "modified": workspace_draft_is_modified(registry["workspace_draft"]),
            }, ["__workspace_draft__"]
        draft["ranges"][range_id] = entry
        draft["edit_mode"] = True
        registry["workspace_draft"] = normalize_workspace_draft(draft)
        return {
            "status": "ok",
            "range_id": range_id,
            "draft": registry["workspace_draft"]["ranges"].get(range_id),
            "modified": workspace_draft_is_modified(registry["workspace_draft"]),
        }, ["__workspace_draft__"]

    emit(locked_registry(mutate))


def cmd_workspace_draft_save(args):
    only_range = (getattr(args, "range_id", None) or "").strip() or None

    def mutate(registry):
        draft = normalize_workspace_draft(registry.get("workspace_draft"))
        applied = []
        changed = set()
        remaining = dict(draft["ranges"])
        for project, item in list(iter_project_ranges(registry, None)):
            rid = item.get("id")
            if rid not in draft["ranges"]:
                continue
            if only_range is not None and rid != only_range:
                continue
            fields = draft["ranges"][rid]
            live = _live_range_lifecycle_fields(item)
            staged = {
                "start_script": fields["start_script"] if "start_script" in fields else live.get("start_script"),
                "stop_script": fields["stop_script"] if "stop_script" in fields else live.get("stop_script"),
                "command": fields["command"] if "command" in fields else live.get("command"),
                "cwd": fields["cwd"] if "cwd" in fields else live.get("cwd"),
            }
            unchanged = all(
                staged.get(k) == live.get(k) for k in ("start_script", "stop_script", "command", "cwd")
            ) and "extra" not in fields
            remaining.pop(rid, None)
            if unchanged:
                continue
            life = lifecycle(item)
            if "start_script" in fields and fields["start_script"] is not None:
                life["start_script"] = fields["start_script"]
            if "stop_script" in fields and fields["stop_script"] is not None:
                life["stop_script"] = fields["stop_script"]
            if "command" in fields:
                item["command"] = fields["command"]
            if "cwd" in fields:
                item["cwd"] = fields["cwd"]
            if "extra" in fields:
                item["extra"] = normalize_range_extra(fields.get("extra"))
            normalize_range_record(item)
            applied.append({"project": project, "range_id": rid})
            changed.add(project)
        keep_edit = bool(getattr(args, "keep_edit_mode", False))
        if only_range is None:
            registry["workspace_draft"] = {"ranges": {}, "edit_mode": keep_edit}
        else:
            registry["workspace_draft"] = normalize_workspace_draft(
                {"ranges": remaining, "edit_mode": keep_edit or bool(remaining)}
            )
        return {
            "status": "ok",
            "applied": applied,
            "applied_count": len(applied),
            "range_id": only_range,
            "modified": workspace_draft_is_modified(registry["workspace_draft"]),
        }, sorted(changed) + ["__workspace_draft__"]

    emit(locked_registry(mutate))


def cmd_workspace_draft_revert(args):
    only_range = (getattr(args, "range_id", None) or "").strip() or None

    def mutate(registry):
        keep = bool(getattr(args, "keep_edit_mode", False))
        if only_range is None:
            registry["workspace_draft"] = {"ranges": {}, "edit_mode": keep}
            return {
                "status": "ok",
                "modified": False,
                "edit_mode": keep,
                "range_id": None,
            }, ["__workspace_draft__"]
        draft = normalize_workspace_draft(registry.get("workspace_draft"))
        draft["ranges"].pop(only_range, None)
        if not draft["ranges"]:
            draft["edit_mode"] = keep
        registry["workspace_draft"] = normalize_workspace_draft(draft)
        return {
            "status": "ok",
            "modified": workspace_draft_is_modified(registry["workspace_draft"]),
            "edit_mode": bool(registry["workspace_draft"].get("edit_mode")),
            "range_id": only_range,
        }, ["__workspace_draft__"]

    emit(locked_registry(mutate))


def cmd_workspace_edit_mode(args):
    token = str(getattr(args, "state", "on")).strip().lower()
    on = token in ("on", "1", "true", "yes")

    def mutate(registry):
        draft = normalize_workspace_draft(registry.get("workspace_draft"))
        draft["edit_mode"] = on
        registry["workspace_draft"] = draft
        return {
            "status": "ok",
            "edit_mode": on,
            "modified": workspace_draft_is_modified(draft),
        }, ["__workspace_draft__"]

    emit(locked_registry(mutate))


def cmd_workspace(args):
    cmd = getattr(args, "workspace_command", None)
    if cmd == "draft-set":
        return cmd_workspace_draft_set(args)
    if cmd == "draft-save":
        return cmd_workspace_draft_save(args)
    if cmd == "draft-revert":
        return cmd_workspace_draft_revert(args)
    if cmd == "edit-mode":
        return cmd_workspace_edit_mode(args)
    if cmd == "capture-defaults":
        return cmd_set_defaults_from_current(args)
    fail("invalid_args", f"unknown workspace command: {cmd}")


def cmd_tailscale(args):
    cmd = getattr(args, "tailscale_command", None)
    if cmd == "status":
        return cmd_tailscale_status(args)
    if cmd == "login":
        return cmd_tailscale_login(args)
    if cmd == "logout":
        return cmd_tailscale_logout(args)
    if args.tailscale_command == "serve-portskill":
        return cmd_tailscale_serve_portskill(args)
    fail("invalid_args", f"unknown tailscale command: {cmd}")


def cmd_doctor(args):
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

    def _probe(name: str, url: object) -> None:
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
            with urllib.request.urlopen(url, timeout=3) as resp:
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
    _probe("mcp_reachability", mcp_url)

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
            "UI toggle is Serve on/off; Funnel remains available via CLI `--mode funnel`."
            + ("" if ts_status.get("logged_in") else " Log in before enabling Serve.")
        ),
    })

    skill = skill_dir()
    app_pkg = skill / "port_registry_app"
    required_rel = [
        "port_registry_app/__init__.py",
        "port_registry_app/cli.py",
        "port_registry_app/server.py",
        "port_registry_app/mcp.py",
    ]
    missing = [name for name in required_rel if not (skill / name).exists()]
    skill_md_ok = (skill / "skill" / "SKILL.md").exists() or (skill / "SKILL.md").exists()
    ui_ok = (skill / "ui").is_dir() or (app_pkg / "static").is_dir()
    skill_ok = not missing and ui_ok and skill_md_ok
    detail = str(skill)
    if missing:
        detail += f"; missing {', '.join(missing)}"
    if not skill_md_ok:
        detail += "; missing skill/SKILL.md (or SKILL.md)"
    if not ui_ok:
        detail += "; missing ui/ (or port_registry_app/static/)"
    checks.append({"name": "skill_files", "ok": skill_ok, "detail": detail})

    project = project_path(args.project)
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
        except (OSError, json.JSONDecodeError, SystemExit):
            focused_hist = None
    emit({
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
        "ui_url": ui_url,
        "mcp_url": mcp_url,
        "skill_dir": str(skill),
        "default_state_counts": {"on": default_on, "off": default_off},
        "tailscale": ts_status,
        "environment_history": focused_hist,
        "handoff_kit": handoff_info,
    })
    if not ok:
        raise SystemExit(DOCTOR_FAIL_CLOSED_EXIT)


def cmd_history_list(args):
    env = getattr(args, "environment", None)

    def read(registry):
        return history_list_payload(registry, env), False, []

    emit(mutate_registry_meta(read))


def cmd_history_restore(args):
    env = getattr(args, "environment", None)
    index = args.index

    def mutate(registry):
        payload, changed = restore_history(registry, env, index)
        return payload, True, changed

    emit(mutate_registry_meta(mutate))


def cmd_history_reset(args):
    env = getattr(args, "environment", None)

    def mutate(registry):
        payload, changed = reset_history(registry, env)
        return payload, True, changed

    emit(mutate_registry_meta(mutate))


def cmd_history_clear(args):
    env = getattr(args, "environment", None)

    def mutate(registry):
        return clear_history_stack(registry, env), True, []

    emit(mutate_registry_meta(mutate))


def cmd_history(args):
    cmd = args.history_command
    if cmd == "list":
        return cmd_history_list(args)
    if cmd == "restore":
        return cmd_history_restore(args)
    if cmd == "reset":
        return cmd_history_reset(args)
    if cmd == "clear":
        return cmd_history_clear(args)
    fail("invalid_args", "history requires list|restore|reset|clear")


def cmd_machine(args):
    """Public machine CLI held — Coming soon (no mutate / no writes)."""
    print("Remote machines — Coming soon", file=__import__("sys").stderr)
    raise SystemExit(2)


def status_project_from_context(args):
    if args.project is not None:
        return project_path(args.project)
    local_path = pathlib.Path.cwd() / ".port-registry.json"
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            local_project = data.get("project_path")
            if local_project:
                return project_path(local_project)
        except (OSError, json.JSONDecodeError):
            return project_path(".")
    return None


def enrich_range_for_status(registry, item, tailscale_self=None):
    """Return a shallow copy of range with resolved machine/host/url fields."""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    normalize_range_machine_fields(out)
    mid = out.get("machine_id") or "local"
    machine = get_machine(registry, mid) or default_machines()["local"]
    out["machine"] = {
        "id": machine.get("id") or mid,
        "label": machine.get("label") or mid,
        "host": machine.get("host"),
        "kind": machine.get("kind") or "local",
    }
    host = resolve_range_host(registry, out, tailscale_self=tailscale_self)
    out["resolved_host"] = host
    url = resolve_range_url(registry, out, tailscale_self=tailscale_self)
    if url:
        out["url"] = url
    return out


def cmd_status(args):
    selected_project = status_project_from_context(args)

    def read(registry):
        try:
            ts_self = (probe_tailscale_status() or {}).get("Self")
        except Exception:
            ts_self = None
        if selected_project is None:
            ranges = []
            for project_entry in registry["projects"].values():
                ranges.extend(project_entry.get("ranges", []))
            projects = registry["projects"]
        else:
            ranges = registry["projects"].get(selected_project, {}).get("ranges", [])
            projects = (
                {selected_project: {"ranges": ranges}}
                if selected_project in registry["projects"]
                else {}
            )
        enriched = [enrich_range_for_status(registry, r, tailscale_self=ts_self) for r in ranges]
        # Also enrich nested project ranges for consumers that walk projects
        projects_out = {}
        for proj, entry in (projects or {}).items():
            if not isinstance(entry, dict):
                projects_out[proj] = entry
                continue
            pranges = [
                enrich_range_for_status(registry, r, tailscale_self=ts_self)
                for r in (entry.get("ranges") or [])
            ]
            projects_out[proj] = {**entry, "ranges": pranges}
        return {
            "status": "ok",
            "ranges": enriched,
            "pool": registry["pool"],
            "projects": projects_out,
            "machines": normalize_machines(registry.get("machines")),
        }, []

    emit(locked_registry(read))



def cmd_discover_ports(args):
    """Poll local listeners + Tailscale Serve; diff vs non-released registry ranges."""

    def read(registry):
        report = discover_ports_mod.build_ports_discovery(registry, tailscale_base_command)
        return report, []

    emit(locked_registry(read))


def cmd_discover_import(args):
    """Claim exact listening/Serve ports into the workspace via allocate --start N."""
    user_tailnet = getattr(args, "tailnet", None)
    if user_tailnet == "funnel":
        fail("invalid_args", "discover import never uses Funnel; use serve or none")
    note_override = getattr(args, "note", None)
    project_override = getattr(args, "project", None)
    all_missing = bool(getattr(args, "all_missing", False))
    port_arg = getattr(args, "port", None)

    if all_missing and port_arg is not None:
        fail("invalid_args", "use either --port N or --all-missing, not both")
    if not all_missing and port_arg is None:
        fail("invalid_args", "require --port N or --all-missing")

    # Snapshot discovery once (read lock)
    def read_disc(registry):
        return discover_ports_mod.build_ports_discovery(registry, tailscale_base_command), []

    discovery = locked_registry(read_disc)
    serve_ports = {}
    raw_serve = (discovery.get("serve") or {}).get("ports") or {}
    for k, v in raw_serve.items():
        try:
            serve_ports[int(k)] = v
        except (TypeError, ValueError):
            continue
    by_port = {int(d["port"]): d for d in discovery.get("discoveries") or [] if d.get("port") is not None}
    missing_ports = [int(d["port"]) for d in discovery.get("missing") or []]

    if all_missing:
        targets = list(missing_ports)
        if not targets:
            emit({"status": "ok", "imported": [], "skipped": [], "message": "no missing ports to import"})
            return
    else:
        targets = [int(port_arg)]

    imported = []
    skipped = []
    errors = []

    for port in targets:
        entry = by_port.get(port) or {"port": port, "note": None, "project_guess": None, "serve": port in serve_ports}
        if entry.get("claimed"):
            skipped.append({"port": port, "reason": "already_claimed", "claim": entry.get("claim")})
            continue
        # Only auto-batch missing; single --port may claim even if not currently listening
        if all_missing and not entry.get("missing"):
            skipped.append({"port": port, "reason": "not_missing"})
            continue

        note = note_override
        if not note:
            note = entry.get("note") or f"discovered :{port}"
        project = project_override or entry.get("project_guess") or "."
        try:
            project = project_path(project)
        except Exception:
            project = project_path(".")
        require_project_directory(project)

        mode = discover_ports_mod.default_tailnet_for_import(port, serve_ports, user_tailnet)
        # Explicit allocate with --start so we never silently take a 20xxx pool slot
        # Re-enter allocate logic via locked mutate (same as cmd_allocate with manual_start)
        try:
            def mutate(registry, _port=port, _note=note, _mode=mode, _project=project):
                hist_key = resolve_history_env_name(registry, None)
                ensure_baseline(registry, hist_key)
                start = int(_port)
                end = start
                pool = registry["pool"]
                if start < pool["start"] or end > pool["end"]:
                    pool["start"] = min(int(pool["start"]), start)
                    pool["end"] = max(int(pool["end"]), end)
                    registry["pool"] = pool
                for other in non_released_ranges(registry):
                    if overlaps(start, end, other["start"], other["end"]):
                        fail(
                            "port_in_use",
                            f"ports {start}-{end} overlap existing range {other.get('id')} "
                            f"({other['start']}-{other['end']})",
                        )
                project_entry = ensure_project(registry, _project)
                scaffold_lifecycle_scripts(_project)
                item = range_record(
                    next_range_id(project_entry),
                    start,
                    end,
                    _note,
                    _mode,
                    machine_id="local",
                    host=None,
                    scheme=None,
                )
                # If already Serving, record configured_at without re-running serve when mode=serve
                if _mode == "serve" and start in serve_ports:
                    item["tailnet"]["port"] = start
                    item["tailnet"]["configured_at"] = utc_now()
                elif _mode == "serve":
                    # User opted in — apply Serve (never Funnel)
                    ensure_tailscale_authenticated("serve")
                    run_tailnet("serve", start, off=False)
                    item["tailnet"]["port"] = start
                    item["tailnet"]["configured_at"] = utc_now()
                preset_warnings = warnings_for_port_range(
                    registry, start, end, exclude_range_id=item.get("id")
                )
                settings = normalize_settings(registry.get("settings"))
                if preset_warnings and settings.get("require_compat"):
                    fail(
                        "compat_conflict",
                        "Ports collide with a saved environment/preset",
                        conflicts=[
                            {
                                "reason": "preset_port_overlap",
                                "message": w["message"],
                                "preset": w.get("preset"),
                                "ports": w.get("ports"),
                            }
                            for w in preset_warnings
                        ],
                        conflict_count=len(preset_warnings),
                        hint="Pick free ports, or: settings set --require-compat off",
                    )
                project_entry["ranges"].append(item)
                maybe_push_history(registry, "discover-import", hist_key)
                payload = {
                    "status": "ok",
                    "range": item,
                    "port": start,
                    "tailnet": _mode,
                    "project": _project,
                    "note": _note,
                }
                if preset_warnings:
                    payload["warnings"] = preset_warnings
                return payload, [_project]

            result = locked_registry(mutate)
            imported.append(result)
        except SystemExit:
            # fail() already emitted; re-raise for single-port, collect for batch
            if not all_missing:
                raise
            errors.append({"port": port, "reason": "import_failed"})
        except Exception as exc:  # noqa: BLE001
            if not all_missing:
                fail("discover_import_failed", str(exc), port=port)
            errors.append({"port": port, "reason": "import_failed", "message": str(exc)})

    emit({
        "status": "ok" if not errors else "partial",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "count_imported": len(imported),
    })


def cmd_discover(args):
    sub = getattr(args, "discover_command", None)
    if sub == "ports":
        return cmd_discover_ports(args)
    if sub == "import":
        return cmd_discover_import(args)
    fail("invalid_args", "discover requires ports|import")


def parser():
    root = JsonArgumentParser(prog="portskill-cli")
    subparsers = root.add_subparsers(dest="command", required=True)

    allocate = subparsers.add_parser("allocate")
    allocate.add_argument("--count", type=int, required=True)
    allocate.add_argument("--note", default=None)
    allocate.add_argument("--project", default=".")
    allocate.add_argument("--tailnet", choices=["serve", "funnel", "none"], default=None)
    allocate.add_argument(
        "--start",
        type=int,
        default=None,
        help="Optional explicit start port (manual assign); warns if preset conflict",
    )
    allocate.add_argument(
        "--machine",
        default="local",
        help="Registry machine id that owns this range (default: local)",
    )
    allocate.add_argument(
        "--host",
        default=None,
        help="Optional advertise host override for links (e.g. MagicDNS name)",
    )
    allocate.add_argument(
        "--scheme",
        default="http",
        help="URL scheme for port hyperlinks (default http)",
    )
    allocate.add_argument("--environment", default=None, help="History env scope (default: focused or __all__)")
    allocate.set_defaults(func=cmd_allocate)

    activate = subparsers.add_parser("activate")
    activate.add_argument("--range-id", required=True)
    activate.add_argument("--project", default=".")
    activate.add_argument("--tailnet", choices=["serve", "funnel", "none"], default=None)
    activate.set_defaults(func=cmd_activate)

    release = subparsers.add_parser("release")
    release.add_argument("--range-id", required=True)
    release.add_argument("--project", default=".")
    release.add_argument("--environment", default=None, help="History env scope")
    release.set_defaults(func=cmd_release)

    start = subparsers.add_parser("start")
    start.add_argument("--range-id", default=None)
    start.add_argument("--all", action="store_true", help="Start all non-released services")
    start.add_argument("--project", default=".")
    start.add_argument("--tailnet", choices=["serve", "funnel", "none"], default=None)
    start.add_argument("--environment", default=None, help="History env scope")
    start.set_defaults(func=cmd_start)

    stop = subparsers.add_parser("stop")
    stop.add_argument("--range-id", default=None)
    stop.add_argument("--all", action="store_true", help="Stop all active/running services")
    stop.add_argument(
        "--non-default",
        action="store_true",
        help="Stop running services whose default_state is off",
    )
    stop.add_argument("--project", default=".")
    stop.add_argument("--environment", default=None, help="History env scope")
    stop.set_defaults(func=cmd_stop)

    status = subparsers.add_parser("status")
    status.add_argument("--project", default=None)
    status.set_defaults(func=cmd_status)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project", default=".")
    doctor.set_defaults(func=cmd_doctor)

    set_default = subparsers.add_parser("set-default")
    set_default.add_argument("--range-id", required=True)
    set_default.add_argument("--state", required=True, choices=["on", "off"])
    set_default.add_argument("--project", default=None)
    set_default.add_argument("--environment", default=None, help="History env scope")
    set_default.set_defaults(func=cmd_set_default)

    set_defaults_cur = subparsers.add_parser(
        "set-defaults-from-current",
        help="Set default_state on/off from each local range's current active/inactive state",
    )
    set_defaults_cur.add_argument("--environment", default=None, help="History env scope")
    set_defaults_cur.set_defaults(func=cmd_set_defaults_from_current)

    apply_defaults = subparsers.add_parser("apply-defaults")
    apply_defaults.add_argument("--project", action="append", default=None)
    apply_defaults.add_argument(
        "--also-stop-off",
        action="store_true",
        help="Also stop running services whose default_state is off",
    )
    apply_defaults.set_defaults(func=cmd_apply_defaults)

    deactivate = subparsers.add_parser(
        "deactivate",
        aliases=["exit-house", "leave"],
        help="Deactivate environment: stop Default On services (keep reserved unless --also-release)",
    )
    deactivate.add_argument("--project", action="append", default=None)
    deactivate.add_argument(
        "--preset",
        default=None,
        help="Limit teardown to services in this named preset (instead of default_state=on)",
    )
    deactivate.add_argument(
        "--also-release",
        action="store_true",
        help="Also release ranges after stop (default keeps them reserved)",
    )
    deactivate.set_defaults(func=cmd_deactivate)

    schedule = subparsers.add_parser(
        "schedule",
        aliases=["busy"],
        help="(Inert) session history windows — prefer compat check for port conflicts",
    )
    schedule.add_argument(
        "--focus",
        default=None,
        help="Optional preset name to filter windows",
    )
    schedule.set_defaults(func=cmd_schedule)

    compat = subparsers.add_parser(
        "compat",
        help="Check whether selected environments can share ports safely",
    )
    compat_sub = compat.add_subparsers(dest="compat_command")
    compat_check = compat_sub.add_parser("check", help="Report port conflicts (default)")
    for p in (compat, compat_check):
        p.add_argument("--preset", action="append", default=None, help="Preset name (repeatable)")
        p.add_argument("--name", action="append", default=None, help="Alias of --preset")
        p.add_argument("--presets", default=None, help="Comma-separated preset names")
        p.add_argument("--file", default=None, help="Environment JSON file")
        p.add_argument(
            "--include-default-on",
            action="store_true",
            help="Include live Default On ranges",
        )
        p.set_defaults(func=cmd_compat)

    environment = subparsers.add_parser("environment")
    env_sub = environment.add_subparsers(dest="env_command", required=True)

    env_export = env_sub.add_parser("export")
    env_export.add_argument("--name", required=True)
    env_export.add_argument("--out", default=None)
    env_export.add_argument("--description", default="")
    env_export.add_argument("--project", action="append", default=None)
    env_export.add_argument(
        "--from-preset",
        default=None,
        help="Export services from a named in-registry preset instead of live ranges",
    )
    env_export.set_defaults(func=cmd_environment)

    env_import = env_sub.add_parser("import")
    env_import.add_argument("--file", required=True)
    env_import.add_argument(
        "--apply-defaults",
        action="store_true",
        help="After import, start services marked default_state=on",
    )
    env_import.add_argument("--environment", default=None, help="History env scope")
    env_import.set_defaults(func=cmd_environment)

    env_check = env_sub.add_parser("check")
    env_check.add_argument("--name", action="append", default=None, help="Preset name(s) to check")
    env_check.add_argument("--file", default=None, help="Environment JSON file to check")
    env_check.add_argument(
        "--include-default-on",
        action="store_true",
        help="Include live Default On ranges in the check",
    )
    env_check.set_defaults(func=cmd_environment)

    preset = subparsers.add_parser("preset")
    preset_sub = preset.add_subparsers(dest="preset_command", required=True)

    preset_save = preset_sub.add_parser("save")
    preset_save.add_argument("--name", required=True)
    preset_save.add_argument("--description", default="")
    preset_save.add_argument("--project", action="append", default=None)
    preset_save.add_argument(
        "--from-file",
        default=None,
        help="Load services from an environment JSON file instead of live ranges",
    )
    preset_save.add_argument("--empty", action="store_true", help="Create an empty environment (no services)")
    preset_save.add_argument("--environment", default=None, help="History env scope")
    preset_save.set_defaults(func=cmd_preset)

    preset_list = preset_sub.add_parser("list")
    preset_list.set_defaults(func=cmd_preset)

    preset_show = preset_sub.add_parser("show")
    preset_show.add_argument("--name", required=True)
    preset_show.set_defaults(func=cmd_preset)

    preset_delete = preset_sub.add_parser("delete")
    preset_delete.add_argument("--name", required=True)
    preset_delete.set_defaults(func=cmd_preset)

    preset_apply = preset_sub.add_parser("apply")
    preset_apply.add_argument("--name", required=True)
    preset_apply.add_argument(
        "--also-stop-off",
        action="store_true",
        help="Also stop running services whose default_state is off",
    )
    preset_apply.set_defaults(func=cmd_preset)

    preset_check = preset_sub.add_parser("check")
    preset_check.add_argument(
        "--name",
        action="append",
        required=True,
        help="Preset name (repeatable) to check for port conflicts",
    )
    preset_check.set_defaults(func=cmd_preset)

    settings = subparsers.add_parser("settings")
    settings_sub = settings.add_subparsers(dest="settings_command", required=True)

    settings_get = settings_sub.add_parser("get")
    settings_get.set_defaults(func=cmd_settings)

    settings_set = settings_sub.add_parser("set")
    settings_set.add_argument(
        "--auto-apply-preset",
        default=None,
        help="Preset name to auto-apply, or --none / none to clear",
    )
    # Allow literal flag form: --auto-apply-preset NAME|--none via argparse nargs?
    # Spec: settings set --auto-apply-preset NAME|--none
    settings_set.add_argument(
        "--auto-apply-on-launch",
        choices=["on", "off"],
        default=None,
        help="Enable or disable auto-apply when the HTTP UI launches",
    )
    settings_set.add_argument(
        "--auto-apply",
        default=None,
        help="Shortcut: preset name enables launch apply; 'off' clears both",
    )
    settings_set.add_argument(
        "--auto-exit-on-shutdown",
        choices=["on", "off"],
        default=None,
        help="When on, Portskill UI attempts deactivate once on SIGINT/SIGTERM",
    )
    settings_set.add_argument(
        "--overlap-policy",
        choices=["allow", "deny"],
        default=None,
        help="Legacy; prefer --require-compat. deny maps to require_compat when unset",
    )
    settings_set.add_argument(
        "--require-compat",
        choices=["on", "off"],
        default=None,
        help="When on, refuse activate/apply/allocate that would collide on ports",
    )
    settings_set.add_argument(
        "--open-environment-tabs",
        default=None,
        help="Comma-separated open environment tab names, or none",
    )
    settings_set.add_argument(
        "--focused-environment",
        default=None,
        help="Focused environment tab name, or none",
    )
    settings_set.add_argument(
        "--open-tab",
        default=None,
        help="Open (and focus) an environment tab by preset name",
    )
    settings_set.add_argument(
        "--close-tab",
        default=None,
        help="Close an environment tab (does not delete the preset)",
    )
    settings_set.add_argument(
        "--mcp-tool",
        action="append",
        default=None,
        help="Enable/disable an MCP tool for tools/list+call: NAME=on|off (repeatable)",
    )
    settings_set.add_argument(
        "--mcp-tools-json",
        default=None,
        help="JSON object of MCP tool name -> bool (merged into settings.mcp_tools)",
    )
    settings_set.add_argument(
        "--serve-portskill-on-tailscale",
        choices=["on", "off"],
        default=None,
        help="Persist preference to Tailscale-Serve the Portskill listen port (never Funnel)",
    )
    settings_set.add_argument(
        "--mcp-user-commands-json",
        default=None,
        help="Replace settings.mcp_user_commands with this JSON object",
    )
    settings_set.add_argument(
        "--mcp-user-command-upsert",
        default=None,
        help="JSON object for one user command {name,description,steps} to upsert",
    )
    settings_set.add_argument(
        "--mcp-user-command-delete",
        default=None,
        help="Delete a user MCP command by name",
    )
    settings_set.add_argument(
        "--handoff-enabled",
        choices=["on", "off"],
        default=None,
        help="Enable or hide the Session Handoff product section",
    )
    settings_set.add_argument(
        "--handoff-kit",
        default=None,
        help="Optional Session Handoff kit path override (or none to use vendored kit)",
    )
    settings_set.set_defaults(func=cmd_settings)

    set_tailnet = subparsers.add_parser(
        "set-tailnet",
        help="Set recorded Tailscale mode (serve|none|funnel); apply when range is active",
    )
    set_tailnet.add_argument("--range-id", required=True)
    set_tailnet.add_argument("--mode", required=True, choices=["serve", "none", "funnel"])
    set_tailnet.add_argument("--project", default=None)
    set_tailnet.add_argument("--environment", default=None, help="History env scope")
    set_tailnet.set_defaults(func=cmd_set_tailnet)

    tailscale = subparsers.add_parser(
        "tailscale",
        help="Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
    )
    ts_sub = tailscale.add_subparsers(dest="tailscale_command", required=True)
    ts_status = ts_sub.add_parser("status", help="Wrap tailscale status --json")
    ts_status.set_defaults(func=cmd_tailscale)
    ts_login = ts_sub.add_parser(
        "login",
        help="Browser Login: open Tailscale auth URL and poll until Connected",
    )
    ts_login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the system browser (still returns auth_url)",
    )
    ts_login.add_argument(
        "--wait",
        type=int,
        default=90,
        help="Seconds to poll status --json for BackendState Running (default 90)",
    )
    ts_login.set_defaults(func=cmd_tailscale)
    ts_logout = ts_sub.add_parser(
        "logout",
        help="Log out of Tailscale (tailscale logout)",
    )
    ts_logout.set_defaults(func=cmd_tailscale)
    ts_serve_ps = ts_sub.add_parser(
        "serve-portskill",
        help="Tailscale Serve the Portskill listen port from listen.json (never Funnel)",
    )
    ts_serve_ps.add_argument(
        "--state",
        choices=["on", "off", "status"],
        default="status",
        help="on=configure Serve, off=teardown, status=report (default)",
    )
    ts_serve_ps.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write settings.serve_portskill_on_tailscale",
    )
    ts_serve_ps.set_defaults(func=cmd_tailscale)

    workspace = subparsers.add_parser(
        "workspace",
        help="Workspace draft edit-mode (Save/Revert metadata)",
    )
    ws_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    ws_set = ws_sub.add_parser("draft-set", help="Stage edit-mode fields for a range")
    ws_set.add_argument("--range-id", required=True)
    ws_set.add_argument("--start-script", default=None)
    ws_set.add_argument("--stop-script", default=None)
    ws_set.add_argument("--command", default=None)
    ws_set.add_argument("--cwd", default=None)
    ws_set.set_defaults(func=cmd_workspace)
    ws_save = ws_sub.add_parser("draft-save", help="Persist draft metadata into registry")
    ws_save.add_argument("--range-id", default=None, help="Apply only this range's draft (default: all)")
    ws_save.add_argument("--keep-edit-mode", action="store_true")
    ws_save.set_defaults(func=cmd_workspace)
    ws_revert = ws_sub.add_parser("draft-revert", help="Discard draft; restore last saved")
    ws_revert.add_argument("--range-id", default=None, help="Clear only this range's draft (default: all)")
    ws_revert.add_argument("--keep-edit-mode", action="store_true")
    ws_revert.set_defaults(func=cmd_workspace)
    ws_edit = ws_sub.add_parser("edit-mode", help="Enter/leave edit mode")
    ws_edit.add_argument("--state", choices=["on", "off"], default="on")
    ws_edit.set_defaults(func=cmd_workspace)
    ws_cap = ws_sub.add_parser(
        "capture-defaults",
        help="Alias: set default_state from current active/inactive states",
    )
    ws_cap.add_argument("--environment", default=None, help="History env scope")
    ws_cap.set_defaults(func=cmd_workspace)

    history = subparsers.add_parser(
        "history",
        help="Photoshop-style environment undo stack (per focused env)",
    )
    history_sub = history.add_subparsers(dest="history_command", required=True)

    history_list = history_sub.add_parser("list", help="List Original + steps for an environment")
    history_list.add_argument("--environment", default=None, help="Env name (default: focused or __all__)")
    history_list.set_defaults(func=cmd_history)

    history_restore = history_sub.add_parser("restore", help="Restore snapshot at index (0=Original)")
    history_restore.add_argument("--index", type=int, required=True)
    history_restore.add_argument("--environment", default=None)
    history_restore.set_defaults(func=cmd_history)

    history_reset = history_sub.add_parser("reset", help="Restore Original (index 0)")
    history_reset.add_argument("--environment", default=None)
    history_reset.set_defaults(func=cmd_history)

    history_clear = history_sub.add_parser("clear", help="Drop stack; keep live state; re-baseline")
    history_clear.add_argument("--environment", default=None)
    history_clear.set_defaults(func=cmd_history)


    discover = subparsers.add_parser(
        "discover",
        help="Poll occupied local ports + Tailscale Serve; import missing into workspace",
    )
    discover_sub = discover.add_subparsers(dest="discover_command", required=True)
    disc_ports = discover_sub.add_parser(
        "ports",
        help="JSON: listeners + serve mappings + missing vs registry",
    )
    disc_ports.set_defaults(func=cmd_discover)
    disc_import = discover_sub.add_parser(
        "import",
        help="Claim exact port(s) via allocate --start N (never silent 20xxx realloc)",
    )
    disc_import.add_argument("--port", type=int, default=None, help="Exact start port to claim")
    disc_import.add_argument(
        "--all-missing",
        action="store_true",
        help="Import every missing discovery (use with care)",
    )
    disc_import.add_argument("--note", default=None)
    disc_import.add_argument(
        "--tailnet",
        choices=["serve", "none"],
        default=None,
        help="serve|none (default: serve if already Serving, else none). Never Funnel.",
    )
    disc_import.add_argument("--project", default=None, help="Project path (default: guess or .)")
    disc_import.set_defaults(func=cmd_discover)

    machine = subparsers.add_parser(
        "machine",
        help="Remote machines — Coming soon",
    )
    machine.add_argument(
        "machine_args",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )
    machine.set_defaults(func=cmd_machine)

    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except argparse.ArgumentError as exc:
        fail("invalid_args", str(exc))
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
