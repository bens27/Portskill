"""Default-state domain operations shared by CLI, MCP, and HTTP adapters."""
from __future__ import annotations

from .domain import DomainError
from .process_commands import iter_project_ranges
from .registry_domain import (
    is_remote_range,
    normalize_default_state,
    normalize_range_record,
    project_path,
)
from .registry_operations import (
    ensure_baseline,
    find_range_anywhere,
    locked_registry,
    maybe_push_history,
    require_project_directory,
    resolve_history_env_name,
)


def set_default_result(options: dict | None = None) -> dict:
    options = options or {}
    range_id = options.get("range_id")
    if not isinstance(range_id, str):
        raise DomainError("invalid_args", "range_id is required")
    raw_state = options.get("state")
    if raw_state not in ("on", "off"):
        raise DomainError("invalid_args", "state must be on or off")
    state = normalize_default_state(raw_state)
    project = project_path(options["project"]) if options.get("project") else None
    if project is not None:
        require_project_directory(project)

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, hist_key)
        found_project, item = find_range_anywhere(registry, range_id, project)
        if item is None:
            raise DomainError("not_found", f"range id not found: {range_id}")
        item["default_state"] = state
        normalize_range_record(item)
        maybe_push_history(registry, "set-default", hist_key)
        return {"status": "ok", "range": item, "project": found_project}, [found_project]

    return locked_registry(mutate)


def set_defaults_from_current_result(options: dict | None = None) -> dict:
    options = options or {}

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, options.get("environment"))
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

    return locked_registry(mutate)
