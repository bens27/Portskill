#!/usr/bin/env python3
from __future__ import annotations
import argparse
from . import __version__
from . import discover_ports as discover_ports_mod
from .auth import (
    ensure_http_auth_token,
    http_auth_public_status,
    http_auth_token,
    http_bearer_matches,
    regenerate_http_auth_token,
)
from .apply_defaults import apply_defaults_result as domain_apply_defaults_result
from .compatibility import (
    compat_report as domain_compat_report,
    compatibility_result as domain_compatibility_result,
    enforce_compatibility as domain_enforce_compatibility,
)
from .http_cookies import HTTP_AUTH_COOKIE_NAME, HTTP_SESSION_COOKIE_NAME
from .doctor import DOCTOR_FAIL_CLOSED_EXIT, DOCTOR_HARD_CHECKS
from .domain import (
    DomainError,
    pid_alive as domain_pid_alive,
    pool_bounds_from_environment,
    utc_now,
)
from .network import (
    ALLOW_NON_LOOPBACK_FLAG,
    bind_host_warning,
    is_loopback_host,
    listen_allows_non_loopback,
    non_loopback_refuse_message,
)
from .environments import (
    build_environment_payload as domain_build_environment_payload,
    environment_export_result,
    environment_import_result,
    environment_payload_from_preset as domain_environment_payload_from_preset,
    import_services_into_registry as domain_import_services_into_registry,
    load_environment_file as domain_load_environment_file,
)
from .lifecycle import (
    activate_item,
    ensure_tailscale_authenticated,
    funnel_of_listen_port,
    lifecycle,
    mark_item_active,
    needs_tailnet,
    pid_alive,
    placeholder_content,
    park_item_for_exit,
    process_group_pids,
    range_has_live_process,
    read_listen_port_from_disk,
    refuse_funnel_listen_message,
    refuse_remote_process,
    resolve_stop_also_release,
    release_item,
    resolve_project_relative,
    run_stop_script_if_ready,
    run_tailnet,
    scaffold_lifecycle_scripts,
    should_use_sudo,
    tailscale_command,
    terminate_lifecycle_processes,
    validate_start_script,
    wait_for_processes_to_exit,
)
from .registry import (
    CorruptRegistryError,
    MCP_USER_COMMAND_MAX_STEPS,
    atomic_write_json,
    default_registry as initial_registry,
    default_settings,
    http_auth_path,
    listen_path,
    mutate_registry,
    normalize_mcp_user_command_step,
    normalize_mcp_user_commands,
    normalize_overlap_policy,
    normalize_registry_schema,
    normalize_settings,
    parse_bool,
    read_listen,
    read_settings,
    registry_path,
)
from .registry_domain import (
    DEFAULT_LIFECYCLE_DIR,
    DEFAULT_START_LOG,
    DEFAULT_START_SCRIPT,
    DEFAULT_STOP_LOG,
    DEFAULT_STOP_SCRIPT,
    HISTORY_ALL_KEY,
    HISTORY_STEPS_CAP,
    SESSION_HISTORY_CAP,
    default_sessions,
    default_lifecycle,
    default_machines,
    extract_tailscale_advertise_host,
    get_machine,
    history_is_modified,
    is_remote_range,
    migrate_legacy_project_files,
    normalize_default_state,
    normalize_environment_history,
    normalize_history_entry,
    normalize_lifecycle,
    normalize_machines,
    normalize_optional_str,
    normalize_preset_entry,
    normalize_preset_service,
    normalize_presets,
    normalize_range_extra,
    normalize_range_machine_fields,
    normalize_range_record,
    normalize_registry,
    normalize_session_preset,
    normalize_session_source,
    normalize_sessions,
    normalize_workspace_draft,
    project_path,
    resolve_range_host,
    resolve_range_scheme,
    resolve_range_url,
    workspace_draft_is_modified,
)
from .registry_mutations import (
    activate_result,
    allocate_result,
    release_result,
)
from .registry_operations import (
    _cohort_range_refs,
    all_ranges,
    allocate_range,
    ensure_baseline,
    ensure_project,
    find_free_range,
    find_matching_unreleased,
    find_project_range,
    find_range_anywhere,
    locked_registry,
    maybe_push_history,
    mutate_registry_meta as domain_mutate_registry_meta,
    next_range_id,
    non_released_ranges,
    overlaps,
    pool_bounds,
    presets_claiming_ports,
    push_history_step,
    range_record,
    require_project_directory,
    resolve_history_env_name,
    snapshot_environment,
    warnings_for_port_range,
    write_local_project_file,
    write_local_project_files,
)
from .presets import (
    maybe_auto_apply_on_launch as domain_maybe_auto_apply_on_launch,
    preset_apply_for_launch,
    preset_apply_result,
    preset_check_result,
    preset_delete_result,
    preset_list_result,
    preset_save_result,
    preset_show_result,
)
from .process_commands import (
    iter_project_ranges,
    reclaim_range,
    run_start_all,
    run_stop_bulk,
    start_item_for_apply,
    start_result,
    stop_result,
)
from .service import launch_service, script_is_placeholder, service_launch
from .sessions import (
    begin_active_session as domain_begin_active_session,
    end_active_session as domain_end_active_session,
)
from .status import (
    enrich_range as enrich_range_for_status,
    observed_range_status,
    project_from_context,
    status_result,
)
import copy
import json
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import time
import uuid
import webbrowser
from .tailscale import (
    extract_tailscale_auth_url,
    probe_tailscale_status,
    resolve_tailscale_bin,
    tailscale_base_command,
    tailscale_bin_available,
)


VERSION = 1
DEFAULT_POOL_START = 20000
DEFAULT_POOL_END = 29999


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DomainError("invalid_args", message)


def emit(payload):
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))








def fail(reason, message=None, exit_code=2, **extra):
    payload = {"status": "error", "reason": reason}
    if message is not None:
        payload["message"] = message
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    emit(payload)
    raise SystemExit(exit_code)








MCP_USER_COMMAND_TOOL_NAMES = None  # filled lazily from mcp.TOOL_DEFS when validating


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
    return domain_begin_active_session(registry, preset, source)


def end_active_session(registry):
    return domain_end_active_session(registry)


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






























def run_compat_check(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    return domain_compat_report(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )






def enforce_compat_or_fail(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    return domain_enforce_compatibility(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )




















































def cmd_allocate(args):
    return allocate_result(vars(args))


def cmd_activate(args):
    return activate_result(vars(args))




def cmd_release(args):
    return release_result(vars(args))


def cmd_start(args):
    return start_result(vars(args))


def cmd_stop(args):
    return stop_result(vars(args))


















def build_environment_payload(registry, name, description="", project_filter=None):
    return domain_build_environment_payload(registry, name, description, project_filter)


def environment_payload_from_preset(registry, preset_name, export_name=None, description=None):
    return domain_environment_payload_from_preset(
        registry, preset_name, export_name=export_name, description=description
    )


def cmd_environment_export(args):
    options = {
        "name": args.name,
        "description": getattr(args, "description", None),
        "from_preset": getattr(args, "from_preset", None),
        "project": list(args.project) if args.project else None,
        "out": args.out,
    }
    payload = environment_export_result(options)
    if args.out:
        emit(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
    return


def load_environment_file(path):
    return domain_load_environment_file(path)


def import_services_into_registry(registry, services):
    return domain_import_services_into_registry(registry, services)


def cmd_environment_import(args):
    emit(environment_import_result({
        "file": args.file,
        "environment": getattr(args, "environment", None),
        "apply_defaults": bool(args.apply_defaults),
    }))
    return


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




def stop_item_for_apply(item, project):
    from .apply_defaults import stop_item_for_apply as domain_stop_item_for_apply

    return domain_stop_item_for_apply(item, project)


def run_apply_defaults(
    project_filter=None,
    also_stop_off=False,
    *,
    session_preset=None,
    session_source="apply_defaults",
    record_session=True,
    enforce_overlap=True,
):
    return domain_apply_defaults_result(
        project_filter=project_filter,
        also_stop_off=also_stop_off,
        session_preset=session_preset,
        session_source=session_source,
        record_session=record_session,
        enforce_overlap=enforce_overlap,
    )


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




def mutate_registry_meta(mutator):
    return domain_mutate_registry_meta(mutator)


def cmd_preset_save(args):
    emit(preset_save_result({
        "name": args.name,
        "description": getattr(args, "description", "") or "",
        "project": list(args.project) if args.project else None,
        "from_file": getattr(args, "from_file", None),
        "empty": bool(getattr(args, "empty", False)),
        "environment": getattr(args, "environment", None),
    }))
    return


def cmd_preset_list(args):
    emit(preset_list_result())
    return


def cmd_preset_show(args):
    emit(preset_show_result({"name": args.name}))
    return


def cmd_preset_delete(args):
    emit(preset_delete_result({"name": args.name}))
    return


def run_preset_apply(name, also_stop_off=False):
    return preset_apply_result({"name": name, "also_stop_off": also_stop_off})


def cmd_preset_apply(args):
    also_stop_off = bool(getattr(args, "also_stop_off", False))
    emit(run_preset_apply(args.name, also_stop_off=also_stop_off))


def cmd_preset_check(args):
    report = preset_check_result({"name": list(args.name) if getattr(args, "name", None) else []})
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)
    return


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
    from .settings import settings_get

    return settings_get()


def cmd_settings_set(args):
    from .settings import settings_set

    return settings_set(vars(args))


def cmd_settings(args):
    if args.settings_command == "get":
        return cmd_settings_get(args)
    if args.settings_command == "set":
        return cmd_settings_set(args)
    fail("invalid_args", "settings requires get or set")


def soft_preset_apply(name, also_stop_off=False):
    return preset_apply_for_launch(name, also_stop_off=also_stop_off)


def maybe_auto_apply_on_launch():
    return domain_maybe_auto_apply_on_launch()


def cmd_schedule(args):
    focus = getattr(args, "focus", None)

    def read(registry):
        return build_schedule_windows(registry, focus=focus), False, []

    emit(mutate_registry_meta(read))


def cmd_environment_check(args):
    report = domain_compatibility_result({
        "presets": list(getattr(args, "name", None) or []),
        "file": getattr(args, "file", None),
        "include_default_on": bool(getattr(args, "include_default_on", False)),
    })
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)
    return


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

    report = domain_compatibility_result({
        "presets": names,
        "file": file_path,
        "include_default_on": include_default_on,
    })
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


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
                    except DomainError:
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
        except DomainError:
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


def cmd_http_auth(args):
    """Show/regenerate optional bearer, or toggle the opt-in passkey gate."""
    from .webauthn import (
        delete_passkey_credential,
        http_passkey_gate_enabled,
        list_passkey_credentials,
        passkey_path,
        passkey_public_status,
        set_http_passkey_gate,
    )

    sub = getattr(args, "http_auth_command", None)
    path = str(http_auth_path())
    if sub == "show":
        payload, minted = ensure_http_auth_token()
        emit({
            "status": "ok",
            "configured": True,
            "present": True,
            "path": path,
            "token": payload.get("token"),
            "minted": bool(minted),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "passkey_gate": http_passkey_gate_enabled(),
            "passkey_credentials": len(list_passkey_credentials()),
        })
        return
    if sub == "regenerate":
        payload = regenerate_http_auth_token()
        emit({
            "status": "ok",
            "configured": True,
            "present": True,
            "path": path,
            "token": payload.get("token"),
            "regenerated": True,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "passkey_gate": http_passkey_gate_enabled(),
            "message": "Previous HTTP bearer token is invalidated.",
        })
        return
    if sub == "gate":
        mode = getattr(args, "gate_mode", None)
        if mode in ("on", "off"):
            set_http_passkey_gate(mode == "on")
        pk = passkey_public_status()
        emit({
            "status": "ok",
            "passkey_gate": bool(pk.get("gate")),
            "passkey_credentials": int(pk.get("count") or 0),
            "path": str(passkey_path()),
            "default": "off",
            "message": (
                "Passkey HTTP gate is ON — UI/mutating APIs need a passkey session or bearer."
                if pk.get("gate")
                else "Passkey HTTP gate is OFF — personal listen UI/API stay open (post-#14)."
            ),
        })
        return
    if sub == "passkeys":
        action = getattr(args, "passkeys_action", None) or "list"
        if action == "delete":
            cred_id = getattr(args, "credential_id", None)
            if not isinstance(cred_id, str) or not cred_id.strip():
                fail("invalid_args", "http-auth passkeys delete requires --id")
            deleted = delete_passkey_credential(cred_id.strip())
            emit({
                "status": "ok",
                "deleted": bool(deleted),
                "id": cred_id.strip(),
                **passkey_public_status(),
            })
            return
        emit({"status": "ok", **passkey_public_status()})
        return
    fail("invalid_args", "http-auth requires show|regenerate|gate|passkeys")


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
    from .doctor import doctor_result

    return doctor_result(args.project)


def cmd_handoff_fetch(args):
    from portskill.handoff import fetch_kit

    result = fetch_kit(getattr(args, "ref", None))
    if not result.get("ok"):
        fail(result.get("error") or "fetch_failed", result.get("message"))
    emit(result)


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
    return project_from_context(args.project)


def cmd_path(args):
    """CLI mirror of MCP portskill. Implementation lives in .path."""
    from .path import cmd_path_from_args

    return cmd_path_from_args(args)


def cmd_status(args):
    return status_result(args.project)


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
        except DomainError as exc:
            if not all_missing:
                raise
            errors.append({
                "port": port,
                "reason": exc.reason,
                "message": exc.message or str(exc),
            })
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
    start.add_argument("--reclaim", action="store_true", help="Re-reserve a released service's ports if free, then start it")
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
    stop.add_argument(
        "--also-release",
        choices=["on", "off"],
        default=None,
        help=(
            "Override settings.stop_also_release for this call. "
            "on releases the range; off keeps it reserved."
        ),
    )
    stop.set_defaults(func=cmd_stop)

    status = subparsers.add_parser("status")
    status.add_argument("--project", default=None)
    status.set_defaults(func=cmd_status)

    path_cmd = subparsers.add_parser(
        "path",
        aliases=["portskill-path", "portskill_path"],
        help=(
            "One MCP tool for your agent to handle all port management functions "
            "(MCP portskill). Modes start|stop|release|restart|status with inspectable skips."
        ),
    )
    path_cmd.add_argument(
        "--mode",
        required=True,
        choices=["start", "stop", "release", "restart", "status"],
    )
    path_cmd.add_argument("--project", default=".")
    path_cmd.add_argument("--count", type=int, default=1, help="Ports to allocate when needed (default 1)")
    path_cmd.add_argument("--range-id", default=None)
    path_cmd.add_argument("--note", default=None)
    path_cmd.add_argument("--tailnet", choices=["serve", "funnel", "none"], default=None)
    path_cmd.add_argument("--command", default=None, help="Optional start command to wire onto the range")
    path_cmd.add_argument("--cwd", default=None)
    path_cmd.add_argument("--default-state", choices=["on", "off"], default=None)
    path_cmd.add_argument(
        "--start",
        type=int,
        default=None,
        help="Optional explicit start port when allocate runs",
    )
    path_cmd.add_argument("--environment", default=None, help="History env scope")
    path_cmd.set_defaults(func=cmd_path)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project", default=".")
    doctor.set_defaults(func=cmd_doctor)

    handoff = subparsers.add_parser("handoff", help="Experimental Session Handoff kit management")
    handoff_sub = handoff.add_subparsers(dest="handoff_command", required=True)
    handoff_fetch = handoff_sub.add_parser(
        "fetch", help="Download the Session Handoff kit release into the Portskill config dir")
    handoff_fetch.add_argument("--ref", default=None,
                               help="Kit git ref (tag/branch/sha); default is the pinned release")
    handoff_fetch.set_defaults(func=cmd_handoff_fetch)

    http_auth = subparsers.add_parser(
        "http-auth",
        help="Optional helper: bearer token + opt-in passkey gate (default off)",
    )
    http_auth_sub = http_auth.add_subparsers(dest="http_auth_command", required=True)
    http_auth_show = http_auth_sub.add_parser(
        "show",
        help="Print the local HTTP bearer token (mints one if missing)",
    )
    http_auth_show.set_defaults(func=cmd_http_auth)
    http_auth_regen = http_auth_sub.add_parser(
        "regenerate",
        help="Mint a new HTTP bearer token and invalidate the old one",
    )
    http_auth_regen.set_defaults(func=cmd_http_auth)
    http_auth_gate = http_auth_sub.add_parser(
        "gate",
        help="Show or set the opt-in passkey HTTP gate (default off)",
    )
    http_auth_gate.add_argument(
        "gate_mode",
        nargs="?",
        choices=["on", "off", "show"],
        default="show",
        help="on|off|show (default show). Off keeps post-#14 open listen.",
    )
    http_auth_gate.set_defaults(func=cmd_http_auth)
    http_auth_keys = http_auth_sub.add_parser(
        "passkeys",
        help="List or delete local operator passkeys (not stored in registry.json)",
    )
    http_auth_keys.add_argument(
        "passkeys_action",
        nargs="?",
        choices=["list", "delete"],
        default="list",
    )
    http_auth_keys.add_argument("--id", dest="credential_id", default=None)
    http_auth_keys.set_defaults(func=cmd_http_auth)

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
        "--default-tailnet",
        choices=["serve", "none"],
        default=None,
        help="Tailnet used by path/portskill start|restart when --tailnet is omitted (default none).",
    )
    settings_set.add_argument(
        "--stop-also-release",
        choices=["on", "off"],
        default=None,
        help=(
            "When on (default), stop also releases the range. "
            "When off, stop keeps the range reserved; use release to free it."
        ),
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
        "--mcp-tools-profile",
        choices=["full", "lean"],
        default=None,
        help=(
            "Apply named MCP tools profile into settings.mcp_tools. "
            "full = all core tools enabled (default); Handoff requires separate opt-in. "
            "lean = portskill, status, settings_get, allocate/stop/release; "
            "activate and other CRUD and handoff_* stay off until toggled. Opt-in."
        ),
    )
    settings_set.add_argument(
        "--serve-portskill-on-tailscale",
        choices=["on", "off"],
        default=None,
        help=(
            "Persist preference to Tailscale-Serve the Portskill listen port "
            "(never Funnel). New installs default off."
        ),
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
        help="Enable experimental Session Handoff MCP tools and dashboard ledger access (default off)",
    )
    settings_set.add_argument(
        "--handoff-kit",
        default=None,
        help="Optional Session Handoff kit path override (or none to use the fetched kit)",
    )
    settings_set.add_argument(
        "--handoff-skill",
        default=None,
        help="Optional custom Write-a-Handoff SKILL.md path (or none to use bundled skill)",
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
        help=(
            "Tailscale Serve the Portskill listen port from listen.json (never Funnel). "
            "New installs default off."
        ),
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
        result = args.func(args)
        if isinstance(result, dict):
            emit(result)
        return 0
    except argparse.ArgumentError as exc:
        error = DomainError("invalid_args", str(exc))
    except DomainError as exc:
        error = exc
    emit(error.as_dict())
    return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
