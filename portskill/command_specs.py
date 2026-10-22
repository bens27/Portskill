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
    order: int | _Missing = MISSING


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
    order: int | _Missing = MISSING


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
    parent_destinations: tuple[str, ...] = ()
    parent_required: tuple[bool, ...] = ()


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
    action_binders: tuple[tuple[str, str], ...] = ()
    presenter: str | _Missing = MISSING


@dataclass(frozen=True, slots=True)
class CommandSpec:
    id: str
    handler: HandlerRef
    params: tuple[ParamSpec, ...] = ()
    cli: CLICommandBinding | None = None
    mcp: MCPCommandBinding | None = None
    ui: UICommandBinding | None = None


ALLOCATE_COMMAND = CommandSpec(
    id="allocate",
    handler=HandlerRef("portskill.registry_mutations", "allocate_result"),
    params=(
        ParamSpec(
            key="count",
            kind="integer",
            cli=CLIParamBinding(
                flags=("--count",), type_name="integer", required=True
            ),
            mcp=MCPParamBinding(
                required=True,
                description="Number of ports",
                minimum=1,
                order=0,
            ),
            ui=UIParamBinding(("count",), coercion="integer_or_one"),
        ),
        ParamSpec(
            key="note",
            cli=CLIParamBinding(flags=("--note",), default=None),
            mcp=MCPParamBinding(order=2),
            ui=UIParamBinding(("note",)),
        ),
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default="."),
            mcp=MCPParamBinding(
                description="Project directory (default .)", order=1
            ),
            ui=UIParamBinding(("project",), coercion="string_or_dot"),
        ),
        ParamSpec(
            key="tailnet",
            cli=CLIParamBinding(
                flags=("--tailnet",),
                choices=("serve", "funnel", "none"),
                default=None,
            ),
            mcp=MCPParamBinding(
                description="Required to avoid exit-3 needs_input; omit to be asked",
                enum=("serve", "funnel", "none"),
                order=3,
            ),
            ui=UIParamBinding(("tailnet",), coercion="tailnet_mode_or_none"),
        ),
        ParamSpec(
            key="start",
            kind="integer",
            cli=CLIParamBinding(
                flags=("--start",),
                type_name="integer",
                default=None,
                help=(
                    "Optional explicit start port (manual assign); warns if preset conflict"
                ),
            ),
        ),
        ParamSpec(
            key="machine",
            cli=CLIParamBinding(
                flags=("--machine",),
                default="local",
                help="Registry machine id that owns this range (default: local)",
            ),
            mcp=MCPParamBinding(
                description=(
                    "Registry machine id (default local). Remotes are registry+links only."
                ),
                order=4,
            ),
            ui=UIParamBinding(("machine", "machineId", "machine_id")),
        ),
        ParamSpec(
            key="host",
            cli=CLIParamBinding(
                flags=("--host",),
                default=None,
                help="Optional advertise host override for links (e.g. MagicDNS name)",
            ),
            mcp=MCPParamBinding(
                description="Optional advertise host override for port hyperlinks",
                order=5,
            ),
            ui=UIParamBinding(("host",)),
        ),
        ParamSpec(
            key="scheme",
            cli=CLIParamBinding(
                flags=("--scheme",),
                default="http",
                help="URL scheme for port hyperlinks (default http)",
            ),
            mcp=MCPParamBinding(
                description="URL scheme for port hyperlinks (default http)",
                enum=("http", "https"),
                order=6,
            ),
            ui=UIParamBinding(("scheme",)),
        ),
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(
                flags=("--environment",),
                default=None,
                help="History env scope (default: focused or __all__)",
            ),
        ),
    ),
    cli=CLICommandBinding(path=("allocate",)),
    mcp=MCPCommandBinding(
        name="allocate",
        description=(
            "Low-level: only reserve free ports without starting anything. "
            "To register and serve a project, use portskill mode=start instead."
        ),
        catalog_position=0,
        lean=True,
    ),
    ui=UICommandBinding(actions=("allocate",), binder="allocate_body"),
)


ACTIVATE_COMMAND = CommandSpec(
    id="activate",
    handler=HandlerRef("portskill.registry_mutations", "activate_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(flags=("--range-id",), required=True),
            mcp=MCPParamBinding(required=True, order=0),
        ),
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default="."),
            mcp=MCPParamBinding(order=1),
        ),
        ParamSpec(
            key="tailnet",
            cli=CLIParamBinding(
                flags=("--tailnet",),
                choices=("serve", "funnel", "none"),
                default=None,
            ),
            mcp=MCPParamBinding(
                enum=("serve", "funnel", "none"), order=2
            ),
        ),
    ),
    cli=CLICommandBinding(path=("activate",)),
    mcp=MCPCommandBinding(
        name="activate",
        description=(
            "Internal primitive: mark an allocated range active without running "
            "start.sh. Prefer start or the portskill orchestrator. Off in the lean "
            "profile; not a happy-path peer. Enable with settings set --mcp-tool "
            "activate=on."
        ),
        catalog_position=1,
    ),
)


RELEASE_COMMAND = CommandSpec(
    id="release",
    handler=HandlerRef("portskill.registry_mutations", "release_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(flags=("--range-id",), required=True),
            mcp=MCPParamBinding(required=True, order=0),
            ui=UIParamBinding(("rangeId",), required=True),
        ),
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default="."),
            mcp=MCPParamBinding(order=1),
            ui=UIParamBinding(("project",), required=True),
        ),
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(
                flags=("--environment",), default=None, help="History env scope"
            ),
        ),
    ),
    cli=CLICommandBinding(path=("release",)),
    mcp=MCPCommandBinding(
        name="release",
        description=(
            "Free a range that is not running (portskill release). Refuses with "
            "process_still_running if pid/pgid still live — use stop instead."
        ),
        catalog_position=4,
        lean=True,
    ),
    ui=UICommandBinding(actions=("release",), binder="range_action"),
)


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


SETTINGS_GET_COMMAND = CommandSpec(
    id="settings_get",
    handler=HandlerRef("portskill.settings", "settings_get", "none"),
    cli=CLICommandBinding(
        path=("settings", "get"),
        parent_destinations=("settings_command",),
        parent_required=(True,),
    ),
    mcp=MCPCommandBinding(
        name="settings_get",
        description=(
            "Read registry settings (auto_apply_preset, auto_apply_on_launch, "
            "auto_exit_on_shutdown, stop_also_release, default_tailnet, "
            "require_compat, mcp_tools, mcp_tools_profile)."
        ),
        catalog_position=19,
        lean=True,
    ),
)


SETTINGS_SET_COMMAND = CommandSpec(
    id="settings_set",
    handler=HandlerRef("portskill.settings", "settings_set"),
    params=(
        ParamSpec(
            key="auto_apply_preset",
            cli=CLIParamBinding(
                flags=("--auto-apply-preset",),
                default=None,
                help="Preset name to auto-apply, or --none / none to clear",
                order=0,
            ),
            mcp=MCPParamBinding(
                description="Preset name, or none/off to clear", order=3
            ),
        ),
        ParamSpec(
            key="auto_apply_on_launch",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--auto-apply-on-launch",),
                choices=("on", "off"),
                default=None,
                help="Enable or disable auto-apply when the HTTP UI launches",
                order=1,
            ),
            mcp=MCPParamBinding(order=4),
        ),
        ParamSpec(
            key="auto_apply",
            cli=CLIParamBinding(
                flags=("--auto-apply",),
                default=None,
                help="Shortcut: preset name enables launch apply; 'off' clears both",
                order=2,
            ),
            mcp=MCPParamBinding(
                description="Shortcut: preset name enables launch apply; off clears",
                order=5,
            ),
        ),
        ParamSpec(
            key="auto_exit_on_shutdown",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--auto-exit-on-shutdown",),
                choices=("on", "off"),
                default=None,
                help=(
                    "When on, Portskill UI attempts deactivate once on SIGINT/SIGTERM"
                ),
                order=3,
            ),
            mcp=MCPParamBinding(
                description=(
                    "If true, Portskill UI attempts deactivate on SIGINT/SIGTERM"
                ),
                order=6,
            ),
        ),
        ParamSpec(
            key="default_tailnet",
            cli=CLIParamBinding(
                flags=("--default-tailnet",),
                choices=("serve", "none"),
                default=None,
                help=(
                    "Tailnet used by path/portskill start|restart when --tailnet is "
                    "omitted (default none)."
                ),
                order=4,
            ),
            mcp=MCPParamBinding(
                enum=("serve", "none"),
                description=(
                    "Default chain: serve makes portskill start/restart register, "
                    "Tailscale Serve and run when tailnet is omitted. none (default) "
                    "skips Tailscale. Funnel is never a default."
                ),
                order=1,
            ),
        ),
        ParamSpec(
            key="stop_also_release",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--stop-also-release",),
                choices=("on", "off"),
                default=None,
                help=(
                    "When on (default), stop also releases the range. When off, stop "
                    "keeps the range reserved; use release to free it."
                ),
                order=5,
            ),
            mcp=MCPParamBinding(
                description=(
                    "When true (default), stop also releases the range. When false, "
                    "stop keeps the range reserved; use release to free it."
                ),
                order=2,
            ),
        ),
        ParamSpec(
            key="overlap_policy",
            cli=CLIParamBinding(
                flags=("--overlap-policy",),
                choices=("allow", "deny"),
                default=None,
                help=(
                    "Legacy; prefer --require-compat. deny maps to require_compat "
                    "when unset"
                ),
                order=6,
            ),
            mcp=MCPParamBinding(
                enum=("allow", "deny"),
                description="Legacy; prefer require_compat",
                order=8,
            ),
        ),
        ParamSpec(
            key="require_compat",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--require-compat",),
                choices=("on", "off"),
                default=None,
                help=(
                    "When on, refuse activate/apply/allocate that would collide on "
                    "ports"
                ),
                order=7,
            ),
            mcp=MCPParamBinding(
                description=(
                    "If true, refuse activate/apply/allocate that would collide on "
                    "ports"
                ),
                order=7,
            ),
        ),
        ParamSpec(
            key="open_environment_tabs",
            cli=CLIParamBinding(
                flags=("--open-environment-tabs",),
                default=None,
                help="Comma-separated open environment tab names, or none",
                order=8,
            ),
            mcp=MCPParamBinding(
                description="Comma-separated open environment tab names, or none",
                order=9,
            ),
        ),
        ParamSpec(
            key="focused_environment",
            cli=CLIParamBinding(
                flags=("--focused-environment",),
                default=None,
                help="Focused environment tab name, or none",
                order=9,
            ),
            mcp=MCPParamBinding(
                description="Focused environment name, or none", order=10
            ),
        ),
        ParamSpec(
            key="open_tab",
            cli=CLIParamBinding(
                flags=("--open-tab",),
                default=None,
                help="Open (and focus) an environment tab by preset name",
                order=10,
            ),
            mcp=MCPParamBinding(
                description="Open and focus an environment tab", order=11
            ),
        ),
        ParamSpec(
            key="close_tab",
            cli=CLIParamBinding(
                flags=("--close-tab",),
                default=None,
                help="Close an environment tab (does not delete the preset)",
                order=11,
            ),
            mcp=MCPParamBinding(
                description="Close an environment tab (does not delete)", order=12
            ),
        ),
        ParamSpec(
            key="mcp_tool",
            cli=CLIParamBinding(
                flags=("--mcp-tool",),
                action="append",
                default=None,
                help=(
                    "Enable/disable an MCP tool for tools/list+call: NAME=on|off "
                    "(repeatable)"
                ),
                order=12,
            ),
        ),
        ParamSpec(
            key="mcp_tools_json",
            cli=CLIParamBinding(
                flags=("--mcp-tools-json",),
                default=None,
                help=(
                    "JSON object of MCP tool name -> bool (merged into "
                    "settings.mcp_tools)"
                ),
                order=13,
            ),
        ),
        ParamSpec(
            key="mcp_tools_profile",
            cli=CLIParamBinding(
                flags=("--mcp-tools-profile",),
                choices=("full", "lean"),
                default=None,
                help=(
                    "Apply named MCP tools profile into settings.mcp_tools. full = "
                    "all core tools enabled (default); Handoff requires separate "
                    "opt-in. lean = portskill, status, settings_get, "
                    "allocate/stop/release; activate and other CRUD and handoff_* "
                    "stay off until toggled. Opt-in."
                ),
                order=14,
            ),
            mcp=MCPParamBinding(
                enum=("full", "lean"),
                description=(
                    "Apply named MCP tools profile into settings.mcp_tools. full = "
                    "all core tools enabled (empty map); Session Handoff requires "
                    "separate opt-in. lean = portskill, status, settings_get, plus "
                    "escape hatches allocate/stop/release; activate and other CRUD "
                    "and handoff_* stay off until toggled. Opt-in: existing installs "
                    "stay full until this is applied."
                ),
                order=0,
            ),
        ),
        ParamSpec(
            key="serve_portskill_on_tailscale",
            cli=CLIParamBinding(
                flags=("--serve-portskill-on-tailscale",),
                choices=("on", "off"),
                default=None,
                help=(
                    "Persist preference to Tailscale-Serve the Portskill listen "
                    "port (never Funnel). New installs default off."
                ),
                order=15,
            ),
        ),
        ParamSpec(
            key="mcp_user_commands_json",
            cli=CLIParamBinding(
                flags=("--mcp-user-commands-json",),
                default=None,
                help="Replace settings.mcp_user_commands with this JSON object",
                order=16,
            ),
        ),
        ParamSpec(
            key="mcp_user_command_upsert",
            cli=CLIParamBinding(
                flags=("--mcp-user-command-upsert",),
                default=None,
                help=(
                    "JSON object for one user command {name,description,steps} to "
                    "upsert"
                ),
                order=17,
            ),
        ),
        ParamSpec(
            key="mcp_user_command_delete",
            cli=CLIParamBinding(
                flags=("--mcp-user-command-delete",),
                default=None,
                help="Delete a user MCP command by name",
                order=18,
            ),
        ),
        ParamSpec(
            key="handoff_enabled",
            cli=CLIParamBinding(
                flags=("--handoff-enabled",),
                choices=("on", "off"),
                default=None,
                help=(
                    "Enable experimental Session Handoff MCP tools and dashboard "
                    "ledger access (default off)"
                ),
                order=19,
            ),
        ),
        ParamSpec(
            key="handoff_kit",
            cli=CLIParamBinding(
                flags=("--handoff-kit",),
                default=None,
                help=(
                    "Optional Session Handoff kit path override (or none to use "
                    "the fetched kit)"
                ),
                order=20,
            ),
        ),
        ParamSpec(
            key="handoff_skill",
            cli=CLIParamBinding(
                flags=("--handoff-skill",),
                default=None,
                help=(
                    "Optional custom Write-a-Handoff SKILL.md path (or none to use "
                    "bundled skill)"
                ),
                order=21,
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("settings", "set"),
        parent_destinations=("settings_command",),
        parent_required=(True,),
    ),
    mcp=MCPCommandBinding(
        name="settings_set",
        description=(
            "Update auto-apply settings or apply mcp_tools_profile lean|full. Use "
            "auto_apply as shortcut (name enables launch apply; off clears)."
        ),
        catalog_position=20,
    ),
    ui=UICommandBinding(
        actions=(
            "settings-save",
            "mcp-user-command-save",
            "mcp-user-command-delete",
            "mcp-tool-set",
            "handoff-set-enabled",
            "handoff-set-kit",
            "handoff-skill-upload",
            "handoff-skill-clear",
            "focus-environment",
            "open-environment-tab",
            "close-environment-tab",
        ),
        action_binders=(
            ("settings-save", "settings_save"),
            ("mcp-user-command-save", "mcp_user_command_save"),
            ("mcp-user-command-delete", "mcp_user_command_delete"),
            ("mcp-tool-set", "mcp_tool_set"),
            ("handoff-set-enabled", "handoff_set_enabled"),
            ("handoff-set-kit", "handoff_set_kit"),
            ("handoff-skill-upload", "handoff_skill_upload"),
            ("handoff-skill-clear", "handoff_skill_clear"),
            ("focus-environment", "open_environment_tab"),
            ("open-environment-tab", "open_environment_tab"),
            ("close-environment-tab", "close_environment_tab"),
        ),
    ),
)


COMMAND_SPECS = (
    ALLOCATE_COMMAND,
    ACTIVATE_COMMAND,
    RELEASE_COMMAND,
    STATUS_COMMAND,
    DOCTOR_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
)


def command_spec(spec_id: str) -> CommandSpec:
    for spec in COMMAND_SPECS:
        if spec.id == spec_id:
            return spec
    raise KeyError(spec_id)
