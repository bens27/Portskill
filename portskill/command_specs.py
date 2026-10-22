"""Pure command declarations shared by adapter surface generators.

This module deliberately imports no Portskill runtime modules. Handler targets
are trusted string constants and are resolved only by execution code in later
migration slices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


@dataclass(frozen=True, slots=True)
class HandlerRef:
    module: str
    symbol: str
    binder: str = "options"


@dataclass(frozen=True, slots=True)
class CLIParamBinding:
    flags: tuple[str, ...] = ()
    positional: bool = False
    dest: str | _Missing = MISSING
    action: str | _Missing = MISSING
    type_name: str | _Missing = MISSING
    required: bool | _Missing = MISSING
    default: Any = MISSING
    nargs: str | int | _Missing = MISSING
    choices: tuple[Any, ...] | _Missing = MISSING
    help: str | _Missing = MISSING
    metavar: str | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class MCPParamBinding:
    name: str | _Missing = MISSING
    json_type: str | _Missing = MISSING
    required: bool = False
    description: str | _Missing = MISSING
    enum: tuple[Any, ...] | _Missing = MISSING
    minimum: int | float | _Missing = MISSING
    items_type: str | _Missing = MISSING
    additional_properties_type: str | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class UIParamBinding:
    fields: tuple[str, ...]
    required: bool = False
    coercion: str = "identity"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    key: str
    kind: str = "string"
    cli: CLIParamBinding | None = None
    mcp: MCPParamBinding | None = None
    ui: UIParamBinding | None = None


@dataclass(frozen=True, slots=True)
class CLICommandBinding:
    path: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    help: str | _Missing = MISSING
    description: str | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class MCPCommandBinding:
    name: str
    description: str
    catalog_position: int
    aliases: tuple[str, ...] = ()
    lean: bool = False
    annotations: tuple[tuple[str, Any], ...] | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class UICommandBinding:
    actions: tuple[str, ...]
    binder: str = "options"
    presenter: str | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class CommandSpec:
    id: str
    handler: HandlerRef
    params: tuple[ParamSpec, ...] = ()
    cli: CLICommandBinding | None = None
    mcp: MCPCommandBinding | None = None
    ui: UICommandBinding | None = None


STATUS_COMMAND = CommandSpec(
    id="status",
    handler=HandlerRef("portskill.status", "status_result", "project"),
    params=(
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default=None),
            mcp=MCPParamBinding(description="Optional project filter"),
        ),
    ),
    cli=CLICommandBinding(path=("status",)),
    mcp=MCPCommandBinding(
        name="status",
        description="Inspect registry state (portskill status).",
        catalog_position=5,
        lean=True,
    ),
)


DOCTOR_COMMAND = CommandSpec(
    id="doctor",
    handler=HandlerRef("portskill.doctor", "doctor_result", "project"),
    params=(
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default="."),
            mcp=MCPParamBinding(),
        ),
    ),
    cli=CLICommandBinding(path=("doctor",)),
    mcp=MCPCommandBinding(
        name="doctor",
        description=(
            "Environment checks: registry, Tailscale bin, app files, "
            "placeholder start.sh, default_state counts."
        ),
        catalog_position=7,
    ),
)


COMMAND_SPECS = (STATUS_COMMAND, DOCTOR_COMMAND)


def command_spec(spec_id: str) -> CommandSpec:
    for spec in COMMAND_SPECS:
        if spec.id == spec_id:
            return spec
    raise KeyError(spec_id)
