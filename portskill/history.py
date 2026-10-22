"""Environment history domain operations shared by all adapters."""
from __future__ import annotations

import copy

from .domain import DomainError, utc_now
from .lifecycle import (
    range_has_live_process,
    release_item,
    run_stop_script_if_ready,
    terminate_lifecycle_processes,
)
from .registry_domain import (
    HISTORY_ALL_KEY,
    default_lifecycle,
    history_is_modified,
    normalize_environment_history,
    normalize_history_entry,
    normalize_lifecycle,
    normalize_preset_entry,
    normalize_preset_service,
    normalize_range_record,
)
from .registry_operations import (
    _cohort_range_refs,
    ensure_project,
    find_range_anywhere,
    mutate_registry_meta,
    non_released_ranges,
    overlaps,
    resolve_history_env_name,
    snapshot_environment,
)


def _snapshot_at_index(entry, index):
    entry = normalize_history_entry(entry)
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise DomainError("invalid_args", "history index must be an integer")
    if index < 0:
        raise DomainError("invalid_args", "history index must be >= 0")
    if index == 0:
        snap = entry.get("baseline")
        if not isinstance(snap, dict):
            raise DomainError("history_corrupt", "baseline snapshot missing")
        return snap
    steps = entry.get("steps") or []
    if index > len(steps):
        raise DomainError("invalid_args", f"history index {index} out of range (0..{len(steps)})")
    step = steps[index - 1]
    snap = step.get("snapshot") if isinstance(step, dict) else None
    if not isinstance(snap, dict):
        raise DomainError("history_corrupt", f"snapshot missing at index {index}")
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
        raise DomainError("history_empty", f"no history for environment {key}")
    snap = _snapshot_at_index(entry, index)
    snap_ranges = snap.get("ranges")
    if not isinstance(snap_ranges, list):
        raise DomainError("history_corrupt", "snapshot.ranges missing or not a list")

    desired = {}  # range_id -> (project, range_dict)
    for row in snap_ranges:
        if not isinstance(row, dict):
            raise DomainError("history_corrupt", "snapshot range row corrupt")
        project = row.get("project")
        rng = row.get("range")
        if not isinstance(project, str) or not project.strip():
            raise DomainError("history_corrupt", "snapshot range missing project")
        if not isinstance(rng, dict) or not isinstance(rng.get("id"), str):
            raise DomainError("history_corrupt", "snapshot range missing id")
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
                raise DomainError(
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
                raise DomainError(
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
                    raise DomainError("port_in_use", f"cannot restore range {rid}: ports busy")
                _apply_range_fields_from_snapshot(existing, snap_range)
                changed_projects.add(project)
        else:
            if not _ports_free_for_restore(registry, start, end, exclude_range_id=None):
                raise DomainError(
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


def history_list_result(options: dict | None = None) -> dict:
    options = options or {}

    def read(registry):
        return history_list_payload(registry, options.get("environment")), False, []

    return mutate_registry_meta(read)


def history_restore_result(options: dict | None = None) -> dict:
    options = options or {}
    if "index" not in options:
        raise DomainError("invalid_args", "history index is required")

    def mutate(registry):
        payload, changed = restore_history(
            registry, options.get("environment"), options.get("index")
        )
        return payload, True, changed

    return mutate_registry_meta(mutate)


def history_reset_result(options: dict | None = None) -> dict:
    options = options or {}

    def mutate(registry):
        payload, changed = reset_history(registry, options.get("environment"))
        return payload, True, changed

    return mutate_registry_meta(mutate)


def history_clear_result(options: dict | None = None) -> dict:
    options = options or {}

    def mutate(registry):
        return clear_history_stack(registry, options.get("environment")), True, []

    return mutate_registry_meta(mutate)
