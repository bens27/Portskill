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


_BINDERS = {
    "options": _bind_options,
    "none": _bind_none,
    "project": _bind_project,
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
