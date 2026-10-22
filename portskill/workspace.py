"""Workspace draft operations shared by CLI and HTTP adapters."""
from __future__ import annotations

from .domain import DomainError
from .lifecycle import lifecycle
from .process_commands import iter_project_ranges
from .registry_domain import (
    normalize_optional_str,
    normalize_range_extra,
    normalize_range_record,
    normalize_workspace_draft,
    workspace_draft_is_modified,
)
from .registry_operations import find_range_anywhere, locked_registry


def _live_range_lifecycle_fields(item):
    """Current registry values for draft comparison (normalized optional strings)."""
    life = lifecycle(item) if item else {}
    return {
        "start_script": normalize_optional_str(life.get("start_script")),
        "stop_script": normalize_optional_str(life.get("stop_script")),
        "command": normalize_optional_str((item or {}).get("command")),
        "cwd": normalize_optional_str((item or {}).get("cwd")),
    }


def workspace_draft_set_result(options: dict | None = None) -> dict:
    options = options or {}
    range_id = (options.get("range_id") or "").strip()
    if not range_id:
        raise DomainError("invalid_args", "--range-id required")

    def mutate(registry):
        draft = normalize_workspace_draft(registry.get("workspace_draft"))
        entry = dict(draft["ranges"].get(range_id) or {})
        for key in ("start_script", "stop_script", "command", "cwd"):
            val = options.get(key)
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

    return locked_registry(mutate)


def workspace_draft_save_result(options: dict | None = None) -> dict:
    options = options or {}
    only_range = (options.get("range_id") or "").strip() or None

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
        keep_edit = bool(options.get("keep_edit_mode", False))
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

    return locked_registry(mutate)


def workspace_draft_revert_result(options: dict | None = None) -> dict:
    options = options or {}
    only_range = (options.get("range_id") or "").strip() or None

    def mutate(registry):
        keep = bool(options.get("keep_edit_mode", False))
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

    return locked_registry(mutate)


def workspace_edit_mode_result(options: dict | None = None) -> dict:
    options = options or {}
    token = str(options.get("state", "on")).strip().lower()
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

    return locked_registry(mutate)
