"""Registry-domain normalization and range presentation helpers.

This module owns the richer registry schema layered over registry.py persistence.
It remains independent of CLI, MCP, and server adapters.
"""
from __future__ import annotations

import pathlib
import uuid

from .domain import utc_now
from .registry import normalize_registry_schema, normalize_settings, parse_bool
from .tailscale import probe_tailscale_status

SESSION_HISTORY_CAP = 50
SESSION_SOURCES = ("preset_apply", "apply_defaults", "enter_house")
HISTORY_STEPS_CAP = 40
HISTORY_ALL_KEY = "__all__"
DEFAULT_LIFECYCLE_DIR = ".portskill"
DEFAULT_START_SCRIPT = ".portskill/start.sh"
DEFAULT_STOP_SCRIPT = ".portskill/stop.sh"
DEFAULT_START_LOG = ".portskill/start.log"
DEFAULT_STOP_LOG = ".portskill/stop.log"
LEGACY_LIFECYCLE_DIR = ".port-registry"

def project_path(value):
    return str(pathlib.Path(value).expanduser().resolve())


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
    # Serve/Funnel terminate TLS at the tailnet endpoint and forward HTTP to
    # the local service. A loopback fallback must not inherit that HTTPS scheme.
    if (host in ("127.0.0.1", "localhost", "::1")
            and (item.get("tailnet") or {}).get("mode") in ("serve", "funnel")
            and not is_remote_range(registry, item)):
        scheme = "http"
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
    data = normalize_registry_schema(data, start, end)
    projects = data["projects"]
    for project_key, project_entry in projects.items():
        if isinstance(project_key, str) and project_key.startswith("/"):
            migrate_legacy_project_files(project_key)
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
    for key in ("start_script", "stop_script", "start_log", "stop_log"):
        value = normalized.get(key)
        if isinstance(value, str) and value.startswith(LEGACY_LIFECYCLE_DIR + "/"):
            normalized[key] = DEFAULT_LIFECYCLE_DIR + value[len(LEGACY_LIFECYCLE_DIR):]
    return normalized


def migrate_legacy_project_files(project):
    """Move <project>/.port-registry(.json) to the Portskill names, leaving a symlink
    at the old name so scripts that still reference it keep working."""
    root = pathlib.Path(project)
    for old_name, new_name in (
        (LEGACY_LIFECYCLE_DIR, DEFAULT_LIFECYCLE_DIR),
        (LEGACY_LIFECYCLE_DIR + ".json", DEFAULT_LIFECYCLE_DIR + ".json"),
    ):
        old, new = root / old_name, root / new_name
        try:
            if old.exists() and not old.is_symlink() and not new.exists():
                old.rename(new)
                old.symlink_to(new_name)
        except OSError:
            pass  # read-only FS: leave it; start reports the missing script


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
    edit_mode = parse_bool(draft.get("edit_mode"), False)
    return {"ranges": clean, "edit_mode": edit_mode}


def workspace_draft_is_modified(draft):
    draft = normalize_workspace_draft(draft)
    return bool(draft.get("ranges"))
