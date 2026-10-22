"""Callable port discovery and discover-import domain.

``discover_ports_result`` and ``discover_import_result`` return the payload the
CLI used to print and raise :class:`DomainError` where the CLI used to exit
non-zero, so CLI, MCP and HTTP adapters share one in-process implementation.
"""
from __future__ import annotations

from typing import Any

from . import discover_ports as discover_ports_mod
from .domain import DomainError, utc_now
from .lifecycle import ensure_tailscale_authenticated, run_tailnet, scaffold_lifecycle_scripts
from .registry import normalize_settings
from .registry_domain import project_path
from .registry_operations import (
    ensure_baseline,
    ensure_project,
    locked_registry,
    maybe_push_history,
    next_range_id,
    non_released_ranges,
    overlaps,
    range_record,
    require_project_directory,
    resolve_history_env_name,
    warnings_for_port_range,
)
from .tailscale import tailscale_base_command


class _LocalImportFailure(DomainError):
    """A failure the legacy CLI raised via fail() inside the import mutation.

    Single-port imports surface it like any DomainError; --all-missing batches
    record the legacy ``{port, reason: "import_failed"}`` entry for it.
    """


def _build_discovery(registry):
    return discover_ports_mod.build_ports_discovery(registry, tailscale_base_command), []


def discover_ports_result() -> dict[str, Any]:
    """Poll local listeners + Tailscale Serve; diff vs non-released registry ranges."""
    return locked_registry(_build_discovery)


def _import_one(port, note, mode, project, serve_ports):
    """Claim one exact port under the registry lock; returns the import payload."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, None)
        ensure_baseline(registry, hist_key)
        start = int(port)
        end = start
        pool = registry["pool"]
        if start < pool["start"] or end > pool["end"]:
            pool["start"] = min(int(pool["start"]), start)
            pool["end"] = max(int(pool["end"]), end)
            registry["pool"] = pool
        for other in non_released_ranges(registry):
            if overlaps(start, end, other["start"], other["end"]):
                raise _LocalImportFailure(
                    "port_in_use",
                    f"ports {start}-{end} overlap existing range {other.get('id')} "
                    f"({other['start']}-{other['end']})",
                )
        project_entry = ensure_project(registry, project)
        scaffold_lifecycle_scripts(project)
        item = range_record(
            next_range_id(project_entry), start, end, note, mode,
            machine_id="local", host=None, scheme=None,
        )
        if mode == "serve" and start in serve_ports:
            # Already Serving: record it without re-running serve.
            item["tailnet"]["port"] = start
            item["tailnet"]["configured_at"] = utc_now()
        elif mode == "serve":
            # User opted in: apply Serve (never Funnel).
            ensure_tailscale_authenticated("serve")
            run_tailnet("serve", start, off=False)
            item["tailnet"]["port"] = start
            item["tailnet"]["configured_at"] = utc_now()
        preset_warnings = warnings_for_port_range(registry, start, end, exclude_range_id=item.get("id"))
        settings = normalize_settings(registry.get("settings"))
        if preset_warnings and settings.get("require_compat"):
            raise _LocalImportFailure(
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
            "tailnet": mode,
            "project": project,
            "note": note,
        }
        if preset_warnings:
            payload["warnings"] = preset_warnings
        return payload, [project]

    return locked_registry(mutate)


def discover_import_result(options: dict[str, Any]) -> dict[str, Any]:
    """Claim exact listening/Serve ports; options: port, all_missing, tailnet, note, project."""
    user_tailnet = options.get("tailnet")
    if user_tailnet == "funnel":
        raise DomainError("invalid_args", "discover import never uses Funnel; use serve or none")
    note_override = options.get("note")
    project_override = options.get("project")
    all_missing = bool(options.get("all_missing"))
    port_arg = options.get("port")

    if all_missing and port_arg is not None:
        raise DomainError("invalid_args", "use either --port N or --all-missing, not both")
    if not all_missing and port_arg is None:
        raise DomainError("invalid_args", "require --port N or --all-missing")

    discovery = locked_registry(_build_discovery)
    serve_ports = {}
    for key, value in ((discovery.get("serve") or {}).get("ports") or {}).items():
        try:
            serve_ports[int(key)] = value
        except (TypeError, ValueError):
            continue
    by_port = {int(d["port"]): d for d in discovery.get("discoveries") or [] if d.get("port") is not None}
    missing_ports = [int(d["port"]) for d in discovery.get("missing") or []]

    if all_missing:
        targets = list(missing_ports)
        if not targets:
            return {"status": "ok", "imported": [], "skipped": [], "message": "no missing ports to import"}
    else:
        targets = [int(port_arg)]

    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for port in targets:
        entry = by_port.get(port) or {"port": port, "note": None, "project_guess": None, "serve": port in serve_ports}
        if entry.get("claimed"):
            skipped.append({"port": port, "reason": "already_claimed", "claim": entry.get("claim")})
            continue
        # Only auto-batch missing; single --port may claim even if not currently listening.
        if all_missing and not entry.get("missing"):
            skipped.append({"port": port, "reason": "not_missing"})
            continue

        note = note_override or entry.get("note") or f"discovered :{port}"
        project = project_override or entry.get("project_guess") or "."
        try:
            project = project_path(project)
        except Exception:
            project = project_path(".")
        require_project_directory(project)

        mode = discover_ports_mod.default_tailnet_for_import(port, serve_ports, user_tailnet)
        try:
            imported.append(_import_one(port, note, mode, project, serve_ports))
        except _LocalImportFailure:
            if not all_missing:
                raise
            errors.append({"port": port, "reason": "import_failed"})
        except DomainError as exc:
            if not all_missing:
                raise
            errors.append({"port": port, "reason": exc.reason, "message": exc.message or str(exc)})
        except Exception as exc:  # noqa: BLE001 - batch continues past unexpected failures
            if not all_missing:
                raise DomainError("discover_import_failed", str(exc), port=port) from exc
            errors.append({"port": port, "reason": "import_failed", "message": str(exc)})

    return {
        "status": "ok" if not errors else "partial",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "count_imported": len(imported),
    }
