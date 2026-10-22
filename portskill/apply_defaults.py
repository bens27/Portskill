"""Shared apply-defaults orchestration used by presets and later adapters."""
from __future__ import annotations

from .compatibility import enforce_compatibility
from .domain import utc_now
from .lifecycle import (
    lifecycle,
    pid_alive,
    process_group_pids,
    range_has_live_process,
    release_item,
    run_stop_script_if_ready,
    terminate_lifecycle_processes,
)
from .process_commands import iter_project_ranges, start_item_for_apply
from .registry_domain import normalize_default_state, project_path
from .registry_operations import locked_registry
from .sessions import begin_active_session
from .domain import DomainError


def stop_item_for_apply(item, project):
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
    except DomainError:
        return False, {"reason": "release_failed", "range_id": item.get("id")}
    return True, {"action": "stopped", "range": item}


def apply_defaults_result(
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
            enforce_compatibility(
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
