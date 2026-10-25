"""Shared apply-defaults orchestration used by presets and later adapters."""
from __future__ import annotations

from .compatibility import enforce_compatibility
from .lifecycle import park_item_for_exit, range_has_live_process
from .process_commands import iter_project_ranges, start_item_for_apply
from .registry_domain import normalize_default_state, project_path
from .registry_operations import locked_registry
from .sessions import begin_active_session


def stop_item_for_apply(item, project):
    """Stop and release, with stop's teardown contract: on a Tailscale teardown
    failure the process is gone, so the range is parked reserved with the
    mapping recorded and the detail carries it so the caller commits."""
    ok, detail = park_item_for_exit(item, project, also_release=True)
    if ok and detail.get("action") == "stopped_released":
        detail = {**detail, "action": "stopped"}
    return ok, detail


def apply_defaults_result(
    project_filter=None,
    also_stop_off=False,
    *,
    session_preset=None,
    session_source="apply_defaults",
    record_session=True,
    enforce_overlap=True,
):
    if isinstance(project_filter, str):
        project_filter = [project_filter]
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
                    if "range" in detail:
                        changed.add(project)
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
