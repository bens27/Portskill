"""Callable environment export/import operations."""
from __future__ import annotations

import datetime
import json
import pathlib

from .compatibility import compat_report
from .domain import DomainError, utc_now
from .environment_files import load_environment_file
from .lifecycle import scaffold_lifecycle_scripts
from .process_commands import iter_project_ranges
from .registry import backups_dir, normalize_settings
from .registry_domain import (
    normalize_default_state,
    normalize_preset_entry,
    normalize_range_record,
    project_path,
)
from .registry_operations import (
    ensure_baseline,
    ensure_project,
    find_free_range,
    find_matching_unreleased,
    find_project_range,
    locked_registry,
    maybe_push_history,
    mutate_registry_meta,
    next_range_id,
    range_record,
    resolve_history_env_name,
)


def build_environment_payload(registry, name, description="", project_filter=None):
    services = []
    for project, item in iter_project_ranges(registry, project_filter):
        tailnet_mode = (item.get("tailnet") or {}).get("mode") or "none"
        services.append({
            "project": project,
            "note": item.get("note"),
            "count": int(item["end"]) - int(item["start"]) + 1,
            "tailnet": tailnet_mode if tailnet_mode in ("serve", "funnel", "none") else "none",
            "default_state": normalize_default_state(item.get("default_state")),
            "range_id": item.get("id"),
        })
    return {
        "version": 1,
        "kind": "portskill-environment",
        "name": name,
        "description": description or "",
        "exported_at": utc_now(),
        "services": services,
    }


def environment_payload_from_preset(registry, preset_name, export_name=None, description=None):
    presets = registry.get("presets") or {}
    if preset_name not in presets:
        raise DomainError("preset_not_found", f"preset not found: {preset_name}")
    preset = normalize_preset_entry(preset_name, presets[preset_name])
    return {
        "version": 1,
        "kind": "portskill-environment",
        "name": export_name if export_name else preset["name"],
        "description": description if description is not None else preset.get("description") or "",
        "exported_at": utc_now(),
        "services": list(preset.get("services") or []),
        "from_preset": preset_name,
    }


def environment_export_result(options: dict) -> dict:
    project_filter = options.get("project")
    if isinstance(project_filter, str):
        project_filter = [project_filter]
    from_preset = options.get("from_preset")

    def read(registry):
        if from_preset:
            payload = environment_payload_from_preset(
                registry,
                from_preset,
                export_name=options.get("name"),
                description=options.get("description"),
            )
        else:
            payload = build_environment_payload(
                registry,
                name=options.get("name"),
                description=options.get("description") or "",
                project_filter=project_filter,
            )
        return payload, []

    payload = locked_registry(read)

    def compat_read(registry):
        return compat_report(registry, services=payload.get("services") or []), False, []

    try:
        compatibility = mutate_registry_meta(compat_read)
    except Exception:  # Preserve export's best-effort compatibility annotation.
        compatibility = {"compatible": True, "conflicts": []}
    if not compatibility.get("compatible"):
        settings = mutate_registry_meta(
            lambda registry: (normalize_settings(registry.get("settings")), False, [])
        )
        if settings.get("require_compat"):
            raise DomainError(
                "compat_conflict",
                f"Environment {payload.get('name')!r} has internal port conflicts; not exported",
                conflicts=compatibility.get("conflicts"),
                conflict_count=compatibility.get("conflict_count"),
            )
        payload["_compat_warning"] = {
            "reason": "compat_conflict",
            "conflicts": compatibility.get("conflicts"),
        }

    out = options.get("out")
    if not out:
        return payload
    out_path = pathlib.Path(out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "status": "ok",
        "path": str(out_path.resolve()),
        "name": payload["name"],
        "service_count": len(payload["services"]),
        "compatible": bool(compatibility.get("compatible")),
    }
    if not compatibility.get("compatible"):
        result["warnings"] = [{
            "reason": "compat_conflict",
            "message": "Services inside this environment conflict on ports",
            "conflicts": compatibility.get("conflicts"),
        }]
    return result


def import_services_into_registry(registry, services):
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
            results.append({
                "index": index, "status": "error", "reason": "invalid_project", "message": str(exc)
            })
            continue
        if not pathlib.Path(project).is_dir():
            results.append({
                "index": index, "status": "error", "reason": "project_not_found", "project": project,
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
                    "index": index, "status": "error", "reason": "pool_exhausted",
                    "project": project, "note": note,
                })
                continue
            start, end = available
            project_entry = ensure_project(registry, project)
            item = range_record(
                next_range_id(project_entry), start, end, note, tailnet_mode,
                default_state=default_state,
            )
            project_entry["ranges"].append(item)
            action = "allocated"
        else:
            item["default_state"] = default_state
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


def export_workspace_backup(registry, label="workspace"):
    stamp = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    destination_dir = backups_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{label}-{stamp}.json"
    payload = build_environment_payload(
        registry,
        name=label,
        description=f"Auto-backup before import ({stamp})",
        project_filter=None,
    )
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(destination.resolve())


def environment_import_result(options: dict) -> dict:
    _data, services = load_environment_file(options.get("file"))

    def mutate(registry):
        try:
            backup_path = export_workspace_backup(registry, label="workspace")
        except OSError as exc:
            raise DomainError(
                "backup_failed", f"Import aborted: could not write workspace backup: {exc}"
            ) from exc
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        results, changed = import_services_into_registry(registry, services)
        maybe_push_history(registry, "environment import", history)
        return {
            "status": "ok",
            "imported": len([result for result in results if result.get("status") == "ok"]),
            "failed": len([result for result in results if result.get("status") != "ok"]),
            "results": results,
            "backup_path": backup_path,
            "message": f"Import ok; previous workspace backed up to {backup_path}",
        }, changed

    payload = locked_registry(mutate)
    if options.get("apply_defaults"):
        from .apply_defaults import apply_defaults_result
        payload["apply_defaults"] = apply_defaults_result()
    return payload
