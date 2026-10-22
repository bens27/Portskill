"""Port-compatibility policy shared by CLI, MCP, HTTP, presets, and environments."""
from __future__ import annotations

from .domain import DomainError
from .environment_files import load_environment_file
from .process_commands import iter_project_ranges
from .registry import normalize_settings
from .registry_domain import normalize_default_state, normalize_preset_entry, normalize_preset_service, project_path
from .registry_operations import mutate_registry_meta, overlaps, resolve_service_claim


def _claim_summary(claim):
    return {
        "source": claim.get("source"),
        "project": claim.get("project"),
        "note": claim.get("note"),
        "range_id": claim.get("range_id"),
        "count": claim.get("count"),
        "start": claim.get("start"),
        "end": claim.get("end"),
        "resolved": bool(claim.get("resolved")),
    }


def collect_claims_from_services(registry, services, source):
    claims = []
    for index, service in enumerate(services or []):
        normalized = normalize_preset_service(service)
        if normalized is None:
            continue
        claim = resolve_service_claim(registry, normalized, source=source)
        if claim is not None:
            claim["index"] = index
            claims.append(claim)
    return claims


def collect_claims_from_presets(registry, preset_names):
    presets = registry.get("presets") or {}
    claims = []
    missing = []
    for name in preset_names or []:
        name = (name or "").strip()
        if not name:
            continue
        if name not in presets:
            missing.append(name)
            continue
        entry = normalize_preset_entry(name, presets[name])
        claims.extend(collect_claims_from_services(
            registry, entry.get("services") or [], source=f"preset:{name}"
        ))
    return claims, missing


def collect_claims_default_on(registry, project_filter=None):
    filters = [project_path(p) for p in project_filter] if project_filter else None
    claims = []
    for project, item in iter_project_ranges(registry, filters):
        if normalize_default_state(item.get("default_state")) != "on":
            continue
        if item.get("state") == "released":
            continue
        claims.append({
            "source": "default_on",
            "project": project,
            "note": item.get("note"),
            "count": int(item["end"]) - int(item["start"]) + 1,
            "range_id": item.get("id"),
            "start": int(item["start"]),
            "end": int(item["end"]),
            "resolved": True,
            "index": len(claims),
        })
    return claims


def find_port_conflicts(claims):
    conflicts = []
    for index, first in enumerate(claims):
        for second in claims[index + 1:]:
            if (
                first.get("resolved") and second.get("resolved")
                and first.get("range_id")
                and first.get("range_id") == second.get("range_id")
            ):
                continue
            if first.get("resolved") and second.get("resolved"):
                if overlaps(first["start"], first["end"], second["start"], second["end"]):
                    conflicts.append({
                        "reason": "port_overlap",
                        "message": (
                            f"Port ranges overlap: {first.get('start')}-{first.get('end')} "
                            f"({first.get('source')}) vs {second.get('start')}-{second.get('end')} "
                            f"({second.get('source')})"
                        ),
                        "a": _claim_summary(first),
                        "b": _claim_summary(second),
                    })
                    continue
            same_identity = (
                first.get("project") == second.get("project")
                and (first.get("note") or None) == (second.get("note") or None)
            )
            if (
                same_identity and first.get("range_id") and second.get("range_id")
                and first["range_id"] != second["range_id"]
            ):
                conflicts.append({
                    "reason": "duplicate_identity_different_ranges",
                    "message": (
                        "Same project/note maps to different ranges: "
                        f"{first['range_id']} vs {second['range_id']}"
                    ),
                    "a": _claim_summary(first),
                    "b": _claim_summary(second),
                })
            elif (
                same_identity and first.get("count") != second.get("count")
                and not (first.get("range_id") and first.get("range_id") == second.get("range_id"))
            ):
                conflicts.append({
                    "reason": "count_mismatch",
                    "message": (
                        "Same project/note with different port counts: "
                        f"{first.get('count')} vs {second.get('count')}"
                    ),
                    "a": _claim_summary(first),
                    "b": _claim_summary(second),
                })
    return conflicts


def compat_report(
    registry, *, preset_names=None, services=None,
    include_default_on=False, project_filter=None,
):
    claims = []
    missing = []
    sources = []
    if preset_names:
        preset_claims, missing = collect_claims_from_presets(registry, preset_names)
        claims.extend(preset_claims)
        sources.extend([f"preset:{name}" for name in preset_names if (name or "").strip()])
    if services is not None:
        claims.extend(collect_claims_from_services(registry, services, source="services"))
        sources.append("services")
    if include_default_on:
        claims.extend(collect_claims_default_on(registry, project_filter=project_filter))
        sources.append("default_on")
    conflicts = find_port_conflicts(claims)
    compatible = not conflicts and not missing
    return {
        "status": "ok" if compatible else "conflict",
        "compatible": compatible,
        "sources": sources,
        "missing_presets": missing,
        "claims": [_claim_summary(claim) for claim in claims],
        "claim_count": len(claims),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


def compatibility_result(options: dict | None = None) -> dict:
    options = options or {}
    preset_names = options.get("presets") or options.get("preset") or options.get("names") or []
    if isinstance(preset_names, str):
        preset_names = [preset_names]
    services = options.get("services")
    include_default_on = bool(options.get("include_default_on"))
    if not preset_names and services is None and not options.get("file"):
        include_default_on = True
    if options.get("file"):
        _data, services = load_environment_file(options["file"])

    def read(registry):
        return compat_report(
            registry,
            preset_names=preset_names or None,
            services=services,
            include_default_on=include_default_on,
            project_filter=options.get("project"),
        ), False, []

    return mutate_registry_meta(read)


def enforce_compatibility(
    registry, *, preset_names=None, services=None,
    include_default_on=False, project_filter=None,
):
    if not normalize_settings(registry.get("settings")).get("require_compat"):
        return None
    report = compat_report(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )
    if report.get("compatible"):
        return report
    raise DomainError(
        "compat_conflict",
        "Selected environments are not compatible (port conflict). "
        "Fix overlapping ranges, or: settings set --require-compat off",
        conflicts=report.get("conflicts"),
        conflict_count=report.get("conflict_count"),
        missing_presets=report.get("missing_presets") or None,
        hint="Run: compat check --preset NAME …  or settings set --require-compat off",
        report={
            key: report[key]
            for key in (
                "compatible", "sources", "claim_count", "conflict_count",
                "conflicts", "missing_presets",
            )
        },
    )
