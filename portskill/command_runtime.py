"""Late-bound execution for trusted command specifications."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

from .command_specs import COMMAND_SPECS, MISSING, CommandSpec, HandlerRef
from .domain import DomainError


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


def _bind_status(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    # Plain status keeps the historical one-argument call.
    extra: dict[str, Any] = {}
    if options.get("all"):
        extra["all_projects"] = True
    if options.get("find") is not None:
        extra["find"] = options["find"]
    return handler(options.get("project"), **extra)


def _bind_registry_settings(
    handler: Callable[..., dict], options: dict[str, Any]
) -> dict:
    del options
    from .registry import read_registry

    return handler(read_registry()["settings"])


def _bind_http_auth(handler: Callable[..., dict], options: dict[str, Any]) -> dict:
    auth_options = dict(options)
    if auth_options.get("http_auth_command"):
        auth_options["command"] = auth_options["http_auth_command"]
    return handler(auth_options)


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
    if payload.get("status") == "error":
        from .domain import DomainError

        # Raised after the lock: the registry change is already committed.
        raise DomainError(
            payload.get("reason") or "path_failed", payload.get("message"), payload=payload
        )
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
    "status": _bind_status,
    "registry_settings": _bind_registry_settings,
    "http_auth": _bind_http_auth,
    "path": _bind_path,
    "apply_defaults": _bind_apply_defaults,
    "compatibility": _bind_compatibility,
}


def _boolean_error(name: str) -> DomainError:
    return DomainError("invalid_args", f"{name} must be a JSON boolean (true or false)")


def require_boolean_values(values: Any, names: Any) -> None:
    """Reject any present key in ``names`` whose value is not a real bool.

    ``bool`` is an int subclass, so only ``type(value) is bool`` passes: the
    strings "false"/"true", 0/1 and null are refused instead of being truthy.
    An absent key is left to the command's default.
    """
    if not isinstance(values, dict):
        return
    for name in names:
        if name in values and type(values[name]) is not bool:
            raise _boolean_error(name)


def _mcp_boolean_names(spec: CommandSpec) -> tuple[str, ...]:
    return tuple(
        param.key if param.mcp.name is MISSING else param.mcp.name
        for param in spec.params
        if param.kind == "boolean" and param.mcp is not None
    )


def _mcp_boolean_params() -> dict[str, tuple[str, ...]]:
    table: dict[str, tuple[str, ...]] = {}
    for spec in COMMAND_SPECS:
        if spec.mcp is None:
            continue
        names = _mcp_boolean_names(spec)
        if not names:
            continue
        for tool_name in (spec.mcp.name, *spec.mcp.aliases):
            table[tool_name] = names
    return table


MCP_BOOLEAN_PARAMS = _mcp_boolean_params()


def validate_mcp_boolean_args(tool_name: str, arguments: Any) -> None:
    """Type-check every declared boolean argument of one MCP tool call."""
    names = MCP_BOOLEAN_PARAMS.get(tool_name)
    if names:
        require_boolean_values(arguments, names)


# A UI field coerced from a wire boolean into a non-boolean domain value
# (tailscale serve-portskill: enabled true/false -> state "on"/"off").
_UI_BOOLEAN_WIRE_COERCIONS = frozenset({"parsed_boolean"})


def ui_boolean_fields(spec: CommandSpec) -> tuple[str, ...]:
    """Payload field names of ``spec`` that must arrive as JSON booleans:
    every kind=boolean UI param, plus any field read as a wire boolean."""
    return tuple(
        field
        for param in spec.params
        if param.ui is not None
        and (param.kind == "boolean" or param.ui.coercion in _UI_BOOLEAN_WIRE_COERCIONS)
        for field in param.ui.fields
    )


def execute_command(
    spec: CommandSpec, options: dict[str, Any] | None = None
) -> dict:
    """Execute one catalog command without applying transport serialization."""
    if not any(spec is trusted for trusted in COMMAND_SPECS):
        raise ValueError("command spec is not declared in COMMAND_SPECS")
    if spec.handler.binder.startswith("handoff_mcp:"):
        from .registry import read_settings

        tool_name = spec.handler.binder.split(":", 1)[1]
        return resolve_handler(spec.handler)(
            tool_name, dict(options or {}), read_settings()
        )
    try:
        binder = _BINDERS[spec.handler.binder]
    except KeyError as exc:
        raise ValueError(f"unknown command binder: {spec.handler.binder}") from exc
    return binder(resolve_handler(spec.handler), dict(options or {}))
