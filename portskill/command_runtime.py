"""Late-bound execution for trusted command specifications."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

from .command_specs import COMMAND_SPECS, CommandSpec, HandlerRef


_TRUSTED_HANDLER_REFS = tuple(spec.handler for spec in COMMAND_SPECS)


def resolve_handler(ref: HandlerRef) -> Callable[..., dict]:
    """Resolve a catalog-owned handler reference only when execution begins."""
    if not any(ref is trusted for trusted in _TRUSTED_HANDLER_REFS):
        raise ValueError("handler reference is not declared in COMMAND_SPECS")
    handler = getattr(import_module(ref.module), ref.symbol)
    if not callable(handler):
        raise TypeError(f"command handler is not callable: {ref.module}.{ref.symbol}")
    return handler


def _bind_options(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    return handler(options)


def _bind_none(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    del options
    return handler()


def _bind_project(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    return handler(options.get("project"))


def _bind_path(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    payload = handler(
        mode=options.get("mode"),
        project=options.get("project") or ".",
        count=int(options.get("count") or 1),
        range_id=options.get("range_id"),
        note=options.get("note"),
        tailnet=options.get("tailnet"),
        command=options.get("command"),
        cwd=options.get("cwd"),
        default_state=options.get("default_state"),
        start=options.get("start"),
        environment=options.get("environment"),
    )
    if payload.get("needs_input") or payload.get("status") == "needs_input":
        from .domain import DomainError

        raise DomainError("needs_input", exit_code=3, payload=payload)
    return payload


def _bind_apply_defaults(
    handler: Callable[..., dict], options: dict[str, Any]
) -> dict:
    project_filter = options.get("project")
    if project_filter and not isinstance(project_filter, list):
        project_filter = [project_filter]
    return handler(
        project_filter=project_filter,
        also_stop_off=bool(options.get("also_stop_off")),
        session_preset=None,
        session_source="apply_defaults",
        record_session=True,
        enforce_overlap=True,
    )


def _bind_compatibility(
    handler: Callable[..., dict], options: dict[str, Any]
) -> dict:
    names = []
    for key in ("preset", "name"):
        value = options.get(key)
        if value:
            names.extend(value if isinstance(value, list) else [value])
    presets = options.get("presets")
    if isinstance(presets, str) and presets.strip():
        names.extend(part.strip() for part in presets.split(",") if part.strip())
    elif isinstance(presets, list):
        names.extend(presets)
    return handler({
        "presets": names,
        "file": options.get("file"),
        "include_default_on": bool(options.get("include_default_on")),
    })


_BINDERS = {
    "options": _bind_options,
    "none": _bind_none,
    "project": _bind_project,
    "path": _bind_path,
    "apply_defaults": _bind_apply_defaults,
    "compatibility": _bind_compatibility,
}


def execute_command(
    spec: CommandSpec, options: dict[str, Any] | None = None
) -> dict:
    """Execute one catalog command without applying transport serialization."""
    if not any(spec is trusted for trusted in COMMAND_SPECS):
        raise ValueError("command spec is not declared in COMMAND_SPECS")
    try:
        binder = _BINDERS[spec.handler.binder]
    except KeyError as exc:
        raise ValueError(f"unknown command binder: {spec.handler.binder}") from exc
    return binder(resolve_handler(spec.handler), dict(options or {}))
