"""Callable orchestration for simple registry mutations.

Lifecycle primitives remain injected until the dedicated lifecycle slice; this
module owns transport-neutral command contracts and imports no adapter.
"""
from __future__ import annotations

from typing import Any

from .domain import DomainError
from .lifecycle import activate_item, range_has_live_process, refuse_remote_process, release_item
from .registry_domain import is_remote_range, project_path
from .registry_operations import (
    allocate_range,
    ensure_baseline,
    find_project_range,
    locked_registry,
    maybe_push_history,
    require_project_directory,
    resolve_history_env_name,
)


def _option(options: dict, name: str, default=None):
    value = options.get(name, default)
    return value


def allocate_result(options: dict) -> dict:
    project = project_path(_option(options, "project", "."))
    require_project_directory(project)
    count = _option(options, "count", 1)
    tailnet = _option(options, "tailnet")
    if tailnet is None:
        payload = {
            "status": "needs_input",
            "input": "tailnet",
            "prompt": f"Choose tailnet exposure for project {project} and {count} port"
            + ("s" if int(count) != 1 else "")
            + ": serve, funnel, or none.",
            "options": ["serve", "funnel", "none"],
            "resume_hint": "--tailnet",
        }
        raise DomainError("needs_input", exit_code=3, payload=payload)

    def mutate(registry):
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        item, warnings = allocate_range(
            registry,
            project,
            count,
            options.get("note"),
            tailnet,
            start_port=options.get("start"),
            machine_id=options.get("machine"),
            host=options.get("host"),
            scheme=options.get("scheme"),
        )
        maybe_push_history(registry, "allocate", history)
        payload = {"status": "ok", "range": item}
        if warnings:
            payload["warnings"] = warnings
        return payload, [project]

    return locked_registry(mutate)


def activate_result(options: dict) -> dict:
    project = project_path(_option(options, "project", "."))
    require_project_directory(project)
    range_id = options.get("range_id")

    def mutate(registry):
        item = find_project_range(registry, project, range_id)
        if item is None:
            raise DomainError("not_found", f"range id not found for project: {range_id}")
        if is_remote_range(registry, item):
            refuse_remote_process(registry, item, action="activate")
        activate_item(item, project, range_id, options.get("tailnet"))
        return {"status": "ok", "range": item}, [project]

    return locked_registry(mutate)


def release_result(options: dict) -> dict:
    project = project_path(_option(options, "project", "."))
    require_project_directory(project)
    range_id = options.get("range_id")

    def mutate(registry):
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        item = find_project_range(registry, project, range_id)
        if item is None:
            raise DomainError("not_found", f"range id not found for project: {range_id}")
        if range_has_live_process(item):
            raise DomainError(
                "process_still_running",
                "range still has a live pid/pgid; use stop (not release) to terminate then free the ports",
            )
        release_item(item)
        maybe_push_history(registry, "release", history)
        return {"status": "ok", "range": item}, [project]

    return locked_registry(mutate)
