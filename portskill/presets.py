"""Callable preset operations and launch-time preset application."""
from __future__ import annotations

from .apply_defaults import apply_defaults_result
from .compatibility import compat_report, enforce_compatibility
from .domain import DomainError, utc_now
from .environments import build_environment_payload, import_services_into_registry, load_environment_file
from .registry import default_settings, normalize_settings
from .registry_domain import normalize_preset_entry, normalize_preset_service
from .registry_operations import (
    ensure_baseline,
    maybe_push_history,
    mutate_registry_meta,
    resolve_history_env_name,
)


def preset_save_result(options: dict) -> dict:
    name = str(options.get("name") or "").strip()
    if not name:
        raise DomainError("invalid_args", "--name is required")
    description = options.get("description") or ""
    project_filter = options.get("project")
    if isinstance(project_filter, str):
        project_filter = [project_filter]
    from_file = options.get("from_file")
    empty = bool(options.get("empty"))

    def mutate(registry):
        if empty and from_file:
            raise DomainError("invalid_args", "use either --empty or --from-file, not both")
        if empty:
            entry = {
                "name": name,
                "description": description or "New environment",
                "updated_at": utc_now(),
                "services": [],
            }
            registry.setdefault("presets", {})
            registry["presets"][name] = normalize_preset_entry(name, entry)
            settings = normalize_settings(registry.get("settings"))
            tabs = list(settings.get("open_environment_tabs") or [])
            if name not in tabs:
                tabs.append(name)
            settings["open_environment_tabs"] = tabs
            settings["focused_environment"] = name
            registry["settings"] = normalize_settings(settings)
            ensure_baseline(registry, name)
            return {
                "status": "ok",
                "preset": registry["presets"][name],
                "service_count": 0,
                "compatible": True,
                "empty": True,
                "settings": registry["settings"],
            }, True, []

        history = resolve_history_env_name(registry, options.get("environment") or name)
        ensure_baseline(registry, history)
        if from_file:
            data, raw_services = load_environment_file(from_file)
            services = [
                normalized
                for item in raw_services
                if (normalized := normalize_preset_service(item)) is not None
            ]
            use_description = (
                data.get("description") or ""
                if not description and isinstance(data.get("description"), str)
                else description
            )
        else:
            payload = build_environment_payload(
                registry,
                name=name,
                description=description,
                project_filter=project_filter,
            )
            services = [
                normalized
                for item in payload["services"]
                if (normalized := normalize_preset_service(item)) is not None
            ]
            use_description = description
        entry = {
            "name": name,
            "description": use_description,
            "updated_at": utc_now(),
            "services": services,
        }
        registry.setdefault("presets", {})
        registry["presets"][name] = normalize_preset_entry(name, entry)
        registry["settings"] = normalize_settings(registry.get("settings"))
        report = compat_report(registry, services=services)
        if not report.get("compatible") and registry["settings"].get("require_compat"):
            raise DomainError(
                "compat_conflict",
                f"Preset {name!r} has internal port conflicts; not saved",
                conflicts=report.get("conflicts"),
                conflict_count=report.get("conflict_count"),
                hint="Fix overlapping services, or: settings set --require-compat off",
            )
        result = {
            "status": "ok",
            "preset": registry["presets"][name],
            "service_count": len(services),
            "compatible": bool(report.get("compatible")),
        }
        if not report.get("compatible"):
            result["warnings"] = [{
                "reason": "compat_conflict",
                "message": "Services inside this preset conflict on ports",
                "conflicts": report.get("conflicts"),
            }]
        maybe_push_history(registry, "preset save", history)
        return result, True, []

    return mutate_registry_meta(mutate)


def preset_list_result(_options: dict | None = None) -> dict:
    def read(registry):
        presets = registry.get("presets") or {}
        items = []
        for name in sorted(presets):
            entry = normalize_preset_entry(name, presets[name])
            items.append({
                "name": entry["name"],
                "description": entry.get("description") or "",
                "updated_at": entry.get("updated_at"),
                "service_count": len(entry.get("services") or []),
            })
        return {"status": "ok", "presets": items, "count": len(items)}, False, []

    return mutate_registry_meta(read)


def preset_show_result(options: dict) -> dict:
    name = str(options.get("name") or "").strip()
    if not name:
        raise DomainError("invalid_args", "--name is required")

    def read(registry):
        presets = registry.get("presets") or {}
        if name not in presets:
            raise DomainError("preset_not_found", f"preset not found: {name}")
        return {"status": "ok", "preset": normalize_preset_entry(name, presets[name])}, False, []

    return mutate_registry_meta(read)


def preset_delete_result(options: dict) -> dict:
    name = str(options.get("name") or "").strip()
    if not name:
        raise DomainError("invalid_args", "--name is required")

    def mutate(registry):
        presets = registry.setdefault("presets", {})
        if name not in presets:
            raise DomainError("preset_not_found", f"preset not found: {name}")
        del presets[name]
        settings = registry.setdefault("settings", default_settings())
        if settings.get("auto_apply_preset") == name:
            settings["auto_apply_preset"] = None
            registry["settings"] = normalize_settings(settings)
        return {"status": "ok", "deleted": name}, True, []

    return mutate_registry_meta(mutate)


def preset_apply_result(options: dict) -> dict:
    name = str(options.get("name") or "").strip()
    if not name:
        raise DomainError("invalid_args", "preset name is required")

    def mutate(registry):
        presets = registry.get("presets") or {}
        if name not in presets:
            raise DomainError("preset_not_found", f"preset not found: {name}")
        entry = normalize_preset_entry(name, presets[name])
        services = entry.get("services") or []
        enforce_compatibility(registry, preset_names=[name], include_default_on=True)
        results, changed = import_services_into_registry(registry, services)
        return {
            "status": "ok",
            "preset": name,
            "imported": len([result for result in results if result.get("status") == "ok"]),
            "failed": len([result for result in results if result.get("status") != "ok"]),
            "results": results,
        }, True, changed

    payload = mutate_registry_meta(mutate)
    applied = apply_defaults_result(
        also_stop_off=bool(options.get("also_stop_off")),
        session_preset=name,
        session_source="preset_apply",
        record_session=True,
        enforce_overlap=False,
    )
    payload["apply_defaults"] = applied
    if applied.get("session"):
        payload["session"] = applied["session"]
    return payload


def preset_check_result(options: dict) -> dict:
    names = options.get("name") or options.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not names:
        raise DomainError("invalid_args", "preset check requires at least one --name")

    def read(registry):
        return compat_report(registry, preset_names=list(names)), False, []

    return mutate_registry_meta(read)


def preset_apply_for_launch(name, also_stop_off=False):
    name = str(name or "").strip()
    if not name:
        return {"status": "error", "reason": "invalid_args", "message": "preset name is required"}

    def mutate(registry):
        if normalize_settings(registry.get("settings")).get("require_compat"):
            report = compat_report(registry, preset_names=[name], include_default_on=True)
            if not report.get("compatible"):
                return {
                    "status": "error",
                    "reason": "compat_conflict",
                    "message": "Selected environments are not compatible (port conflict)",
                    "conflicts": report.get("conflicts"),
                    "hint": (
                        "Run: compat check --preset NAME … or "
                        "settings set --require-compat off"
                    ),
                    "preset": name,
                }, False, []
        presets = registry.get("presets") or {}
        if name not in presets:
            return {
                "status": "error",
                "reason": "preset_not_found",
                "message": f"preset not found: {name}",
            }, False, []
        entry = normalize_preset_entry(name, presets[name])
        results, changed = import_services_into_registry(
            registry, entry.get("services") or []
        )
        return {
            "status": "ok",
            "preset": name,
            "imported": len([result for result in results if result.get("status") == "ok"]),
            "failed": len([result for result in results if result.get("status") != "ok"]),
            "results": results,
        }, True, changed

    try:
        payload = mutate_registry_meta(mutate)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "reason": "auto_apply_failed",
            "preset": name,
            "message": str(exc),
        }
    if payload.get("status") != "ok":
        return payload

    try:
        applied = apply_defaults_result(
            also_stop_off=also_stop_off,
            session_preset=name,
            session_source="preset_apply",
            record_session=True,
            enforce_overlap=False,
        )
        payload["apply_defaults"] = applied
        if applied.get("session"):
            payload["session"] = applied["session"]
    except Exception as exc:  # noqa: BLE001
        payload["apply_defaults"] = {
            "status": "error",
            "reason": "apply_defaults_failed",
            "message": str(exc),
        }
        payload.setdefault("warnings", []).append(str(exc))

    warnings = list(payload.get("warnings") or [])
    applied = payload.get("apply_defaults") or {}
    warnings.extend(applied.get("errors") or [])
    for skipped in applied.get("skipped") or []:
        if skipped.get("reason") == "start_script_placeholder" or skipped.get("skipped"):
            warnings.append(skipped)
    if warnings:
        payload["warnings"] = warnings
    payload["auto_apply"] = True
    return payload


def maybe_auto_apply_on_launch():
    def read(registry):
        return normalize_settings(registry.get("settings")), False, []

    try:
        settings = mutate_registry_meta(read)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": "auto_apply_read_failed", "message": str(exc)}
    if not settings.get("auto_apply_on_launch"):
        return None
    name = settings.get("auto_apply_preset")
    if not name:
        return {"status": "skipped", "reason": "no_auto_apply_preset", "settings": settings}
    return preset_apply_for_launch(name)
