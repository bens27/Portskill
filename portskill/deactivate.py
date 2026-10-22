"""Environment deactivation domain operation shared by all adapters."""
from __future__ import annotations

from .lifecycle import park_item_for_exit, range_has_live_process
from .process_commands import iter_project_ranges
from .registry_domain import (
    normalize_default_state,
    normalize_preset_entry,
    normalize_sessions,
    project_path,
)
from .registry_operations import locked_registry
from .sessions import end_active_session
from .domain import DomainError


def item_matches_preset_service(project, item, service):
    """Match a live range to a preset service by range_id or project+note."""
    svc_raw = service.get("project")
    if not isinstance(svc_raw, str) or not svc_raw.strip():
        return False
    try:
        svc_project = project_path(svc_raw)
    except DomainError:
        svc_project = svc_raw
    proj = project_path(project) if not str(project).startswith("/") else str(project)
    # project keys in registry are already normalized absolute paths
    if str(project) != str(svc_project) and proj != str(svc_project) and str(project) != svc_raw:
        return False
    rid = service.get("range_id")
    if isinstance(rid, str) and rid.strip():
        return item.get("id") == rid.strip()
    return (item.get("note") or None) == (service.get("note") or None)


def deactivate_result(options: dict | None = None) -> dict:
    """Stop Default On cohort (or preset-matched running services).

    Safe path only: project stop.sh if ready + terminate tracked pgids + tailnet off.
    Default keeps ranges reserved unless also_release.
    """
    options = options or {}
    project_filter = options.get("project")
    if isinstance(project_filter, str):
        project_filter = [project_filter]
    filters = [project_path(p) for p in project_filter] if project_filter else None
    preset_name = (options.get("preset") or "").strip() or None
    also_release = bool(options.get("also_release"))
    clear_session = bool(options.get("clear_session", True))

    def mutate(registry):
        stopped = []
        skipped = []
        errors = []
        changed = set()
        preset_services = None
        if preset_name:
            presets = registry.get("presets") or {}
            if preset_name not in presets:
                raise DomainError("preset_not_found", f"preset not found: {preset_name}")
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
