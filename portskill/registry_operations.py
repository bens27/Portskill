"""Neutral registry mutation and history primitives."""
from __future__ import annotations

import copy
import datetime
import os
import pathlib
import uuid

from .domain import DomainError, pool_bounds_from_environment, utc_now
from .lifecycle import scaffold_lifecycle_scripts
from .registry import CorruptRegistryError, atomic_write_json, mutate_registry, normalize_settings
from .registry_domain import (
    HISTORY_ALL_KEY,
    HISTORY_STEPS_CAP,
    default_lifecycle,
    normalize_default_state,
    normalize_history_entry,
    normalize_machines,
    normalize_preset_entry,
    normalize_range_record,
    normalize_registry,
    project_path,
)

VERSION = 1

def pool_bounds():
    return pool_bounds_from_environment()


def mutate_registry_meta(mutator):
    """Mutate registry metadata without requiring project-local mirror writes."""
    start, end = pool_bounds()
    try:
        return mutate_registry(
            mutator,
            start=start,
            end=end,
            normalizer=normalize_registry,
            after_write=write_local_project_files,
        )
    except CorruptRegistryError as exc:
        raise DomainError("corrupt_registry", str(exc)) from exc


def local_registry_path(project):
    return pathlib.Path(project) / ".portskill.json"


def require_project_directory(project):
    project_dir = pathlib.Path(project)
    if not project_dir.exists() or not project_dir.is_dir():
        raise DomainError("project_not_found", f"project path does not exist: {project}")


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


def locked_registry(mutator):
    start, end = pool_bounds()

    def adapt(registry):
        result, changed_projects = mutator(registry)
        return result, bool(changed_projects), changed_projects

    try:
        return mutate_registry(
            adapt,
            start=start,
            end=end,
            normalizer=normalize_registry,
            after_write=write_local_project_files,
        )
    except CorruptRegistryError as exc:
        raise DomainError("corrupt_registry", str(exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise DomainError("registry_unavailable", str(exc), exit_code=1) from exc


def write_local_project_files(registry, changed_projects):
    for project in changed_projects or []:
        if project and not str(project).startswith("__"):
            write_local_project_file(project, registry)


def write_local_project_file(project, registry):
    require_project_directory(project)
    data = {
        "version": VERSION,
        "project_path": project,
        "ranges": registry["projects"].get(project, {}).get("ranges", []),
    }
    path = local_registry_path(project)
    atomic_write_json(path, data, compact=True)


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


def find_free_range(registry, count):
    pool = registry["pool"]
    pool_start = pool["start"]
    pool_end = pool["end"]
    if count <= 0:
        raise DomainError("invalid_args", "--count must be a positive integer")
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


def _normalize_allocation_target(registry, machine_id="local", host=None, scheme=None):
    if not isinstance(machine_id, str) or not machine_id.strip():
        machine_id = "local"
    else:
        machine_id = machine_id.strip()

    if host is None or (isinstance(host, str) and not host.strip()):
        host = None
    elif not isinstance(host, str):
        raise DomainError("invalid_host", "host must be a hostname or IP address")
    else:
        host = host.strip()
        if any(char.isspace() for char in host) or any(char in host for char in "/?#@"):
            raise DomainError(
                "invalid_host",
                "host must be a hostname or IP address without scheme, path, query, or fragment",
            )

    if scheme is None or (isinstance(scheme, str) and not scheme.strip()):
        scheme = None
    elif not isinstance(scheme, str) or scheme.strip().lower() not in ("http", "https"):
        raise DomainError("invalid_scheme", "scheme must be http or https")
    else:
        scheme = scheme.strip().lower()

    machines = normalize_machines(registry.get("machines"))
    registry["machines"] = machines
    if machine_id not in machines:
        raise DomainError(
            "machine_not_found",
            f"unknown machine id: {machine_id}",
            hint="machine add --id ID --host HOST [--label LABEL]",
        )
    return machine_id, host, scheme


def allocate_range(
    registry,
    project,
    count,
    note,
    tailnet_mode,
    *,
    start_port=None,
    machine_id="local",
    host=None,
    scheme=None,
):
    """Reserve a range in an already-locked registry and return it with warnings."""
    scaffold_lifecycle_scripts(project)
    count = int(count)
    machine_id, host, scheme = _normalize_allocation_target(
        registry, machine_id, host, scheme
    )
    if start_port is not None:
        start = int(start_port)
        end = start + count - 1
        pool = registry["pool"]
        # Allow outside-pool claims so already-served ports can be registered.
        if start < pool["start"] or end > pool["end"]:
            pool["start"] = min(int(pool["start"]), start)
            pool["end"] = max(int(pool["end"]), end)
            registry["pool"] = pool
        for other in non_released_ranges(registry):
            if overlaps(start, end, other["start"], other["end"]):
                raise DomainError(
                    "port_in_use",
                    f"ports {start}-{end} overlap existing range {other.get('id')} "
                    f"({other['start']}-{other['end']})",
                )
    else:
        available = find_free_range(registry, count)
        if available is None:
            raise DomainError("pool_exhausted")
        start, end = available

    project_entry = ensure_project(registry, project)
    item = range_record(
        next_range_id(project_entry),
        start,
        end,
        note,
        tailnet_mode,
        machine_id=machine_id,
        host=host,
        scheme=scheme,
    )
    preset_warnings = warnings_for_port_range(
        registry, start, end, exclude_range_id=item.get("id")
    )
    settings = normalize_settings(registry.get("settings"))
    if preset_warnings and settings.get("require_compat"):
        raise DomainError(
            "compat_conflict",
            "Ports collide with a saved environment/preset",
            conflicts=[
                {
                    "reason": "preset_port_overlap",
                    "message": warning["message"],
                    "preset": warning.get("preset"),
                    "ports": warning.get("ports"),
                }
                for warning in preset_warnings
            ],
            conflict_count=len(preset_warnings),
            hint="Pick free ports, or: settings set --require-compat off",
        )
    project_entry["ranges"].append(item)
    return item, preset_warnings


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
