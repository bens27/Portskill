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
    parent_helps: tuple[str | _Missing, ...] = ()


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


START_COMMAND = CommandSpec(
    id="start",
    handler=HandlerRef("portskill.process_commands", "start_result"),
    params=(
        ParamSpec(key="range_id", cli=CLIParamBinding(flags=("--range-id",), default=None), mcp=MCPParamBinding(required=True, order=0), ui=UIParamBinding(("rangeId",), required=True)),
        ParamSpec(key="reclaim", kind="boolean", cli=CLIParamBinding(flags=("--reclaim",), action="store_true", help="Re-reserve a released service's ports if free, then start it")),
        ParamSpec(key="all", kind="boolean", cli=CLIParamBinding(flags=("--all",), action="store_true", help="Start all non-released services")),
        ParamSpec(key="project", cli=CLIParamBinding(flags=("--project",), default="."), mcp=MCPParamBinding(order=1), ui=UIParamBinding(("project",), required=True)),
        ParamSpec(key="tailnet", cli=CLIParamBinding(flags=("--tailnet",), choices=("serve", "funnel", "none"), default=None), mcp=MCPParamBinding(enum=("serve", "funnel", "none"), order=2)),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(path=("start",)),
    mcp=MCPCommandBinding(
        name="start",
        description="Low-level: run start.sh for an already-reserved range_id. To register and serve a new project, use portskill mode=start instead.",
        catalog_position=2,
    ),
    ui=UICommandBinding(
        actions=("start", "reclaim-start", "start-all"),
        binder="range_action",
        action_binders=(("reclaim-start", "reclaim_start"), ("start-all", "start_all")),
    ),
)


STOP_COMMAND = CommandSpec(
    id="stop",
    handler=HandlerRef("portskill.process_commands", "stop_result"),
    params=(
        ParamSpec(key="range_id", cli=CLIParamBinding(flags=("--range-id",), default=None), mcp=MCPParamBinding(required=True, order=0), ui=UIParamBinding(("rangeId",), required=True)),
        ParamSpec(key="all", kind="boolean", cli=CLIParamBinding(flags=("--all",), action="store_true", help="Stop all active/running services")),
        ParamSpec(key="non_default", kind="boolean", cli=CLIParamBinding(flags=("--non-default",), action="store_true", help="Stop running services whose default_state is off")),
        ParamSpec(key="project", cli=CLIParamBinding(flags=("--project",), default="."), mcp=MCPParamBinding(order=1), ui=UIParamBinding(("project",), required=True)),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
        ParamSpec(
            key="also_release",
            kind="boolean",
            cli=CLIParamBinding(flags=("--also-release",), choices=("on", "off"), default=None, help="Override settings.stop_also_release for this call. on releases the range; off keeps it reserved."),
            mcp=MCPParamBinding(description="Override settings.stop_also_release for this call", order=2),
        ),
    ),
    cli=CLICommandBinding(path=("stop",)),
    mcp=MCPCommandBinding(
        name="stop",
        description="Stop the tracked process group (stop.sh / pgid). Whether the range is released follows settings.stop_also_release (default true). When false, the range stays reserved and release is the explicit free. Optional also_release overrides the setting for this call.",
        catalog_position=3,
        lean=True,
    ),
    ui=UICommandBinding(
        actions=("stop", "stop-all", "stop-non-default"),
        binder="range_action",
        action_binders=(("stop-all", "stop_all"), ("stop-non-default", "stop_non_default")),
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


PORTSKILL_TOOL_PITCH = "One MCP tool for your agent to handle all port management functions."
PORTSKILL_TOOL_DESCRIPTION = PORTSKILL_TOOL_PITCH + (
    " Use it whenever the user says register a port, register, add, host, serve, run, launch, expose, "
    "put on or get onto Portskill (or the port registry), or needs a dev server, app "
    "or project to get a port, a URL, or a Tailscale/tailnet link. "
    "mode=start does the whole job in one call: reserves a free port, wires start.sh "
    "to command, starts the service and, with tailnet=serve, publishes it over Tailscale Serve. "
    "Already-done steps are skipped, so it is safe to repeat. "
    "mode=stop, release or restart to take a service down, free its port or bounce it; "
    "mode=status to see what is registered and running."
)


PATH_COMMAND = CommandSpec(
    id="path",
    handler=HandlerRef("portskill.path", "run_path", "path"),
    params=(
        ParamSpec(key="mode", cli=CLIParamBinding(flags=("--mode",), required=True, choices=("start", "stop", "release", "restart", "status")), mcp=MCPParamBinding(required=True, enum=("start", "stop", "release", "restart", "status"), description="start = register and serve (allocate, wire, activate, start, tailnet); stop, release, restart, or status", order=0)),
        ParamSpec(key="project", cli=CLIParamBinding(flags=("--project",), default="."), mcp=MCPParamBinding(description="Project directory (default .)", order=1)),
        ParamSpec(key="count", kind="integer", cli=CLIParamBinding(flags=("--count",), type_name="integer", default=1, help="Ports to allocate when needed (default 1)"), mcp=MCPParamBinding(minimum=1, description="Ports to allocate when start/restart needs a new range (default 1)", order=2)),
        ParamSpec(key="range_id", cli=CLIParamBinding(flags=("--range-id",), default=None), mcp=MCPParamBinding(order=3)),
        ParamSpec(key="note", cli=CLIParamBinding(flags=("--note",), default=None), mcp=MCPParamBinding(order=4)),
        ParamSpec(key="tailnet", cli=CLIParamBinding(flags=("--tailnet",), choices=("serve", "funnel", "none"), default=None), mcp=MCPParamBinding(enum=("serve", "funnel", "none"), description="Optional user-service Tailnet. Omitted follows settings.default_tailnet (none unless set to serve); none skips Serve. serve requires login (needs_input to resume). Never Funnels Portskill listen.", order=5)),
        ParamSpec(key="command", cli=CLIParamBinding(flags=("--command",), default=None, help="Optional start command to wire onto the range"), mcp=MCPParamBinding(description="Shell command that launches the service, e.g. npm run dev -- --port $PORT. Needed for start when start.sh is still a placeholder", order=6)),
        ParamSpec(key="cwd", cli=CLIParamBinding(flags=("--cwd",), default=None), mcp=MCPParamBinding(order=7)),
        ParamSpec(key="default_state", cli=CLIParamBinding(flags=("--default-state",), choices=("on", "off"), default=None), mcp=MCPParamBinding(enum=("on", "off"), order=8)),
        ParamSpec(key="start", kind="integer", cli=CLIParamBinding(flags=("--start",), type_name="integer", default=None, help="Optional explicit start port when allocate runs"), mcp=MCPParamBinding(description="Optional explicit start port when allocating", order=9)),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(
        path=("path",),
        aliases=("portskill-path", "portskill_path"),
        help="One MCP tool for your agent to handle all port management functions (MCP portskill). Modes start|stop|release|restart|status with inspectable skips.",
    ),
    mcp=MCPCommandBinding(name="portskill", description=PORTSKILL_TOOL_DESCRIPTION, catalog_position=6, aliases=("portskill_path",), lean=True),
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


ENVIRONMENT_EXPORT_COMMAND = CommandSpec(
    id="environment_export",
    handler=HandlerRef("portskill.environments", "environment_export_result"),
    params=(
        ParamSpec(key="name", cli=CLIParamBinding(flags=("--name",), required=True), mcp=MCPParamBinding(required=True, description="Environment name (e.g. ui-work)", order=0), ui=UIParamBinding(("name",), coercion="environment_name")),
        ParamSpec(key="out", cli=CLIParamBinding(flags=("--out",), default=None), mcp=MCPParamBinding(description="Optional output path; omit to return JSON in the tool result", order=1)),
        ParamSpec(key="description", cli=CLIParamBinding(flags=("--description",), default=""), mcp=MCPParamBinding(order=3)),
        ParamSpec(key="project", kind="array", cli=CLIParamBinding(flags=("--project",), action="append", default=None), mcp=MCPParamBinding(items_type="string", description="Optional project path filters", order=2)),
        ParamSpec(key="from_preset", cli=CLIParamBinding(flags=("--from-preset",), default=None, help="Export services from a named in-registry preset instead of live ranges")),
    ),
    cli=CLICommandBinding(path=("environment", "export"), parent_destinations=("env_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="environment_export", description="Export a portable environment JSON of registered services (portskill environment export).", catalog_position=8),
    ui=UICommandBinding(actions=("export-environment",), binder="declared_params"),
)


ENVIRONMENT_IMPORT_COMMAND = CommandSpec(
    id="environment_import",
    handler=HandlerRef("portskill.environments", "environment_import_result"),
    params=(
        ParamSpec(key="file", cli=CLIParamBinding(flags=("--file",), required=True), mcp=MCPParamBinding(required=True, description="Path to environment JSON", order=0), ui=UIParamBinding(("file",), required=True)),
        ParamSpec(key="apply_defaults", kind="boolean", cli=CLIParamBinding(flags=("--apply-defaults",), action="store_true", help="After import, start services marked default_state=on"), mcp=MCPParamBinding(description="If true, run apply-defaults after import", order=1)),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(path=("environment", "import"), parent_destinations=("env_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="environment_import", description="Import an environment JSON into the registry (portskill environment import). Does not auto-start unless apply_defaults is true.", catalog_position=9),
    ui=UICommandBinding(actions=("import-environment",), binder="declared_params"),
)


ENVIRONMENT_CHECK_COMMAND = CommandSpec(
    id="environment_check",
    handler=HandlerRef("portskill.compatibility", "compatibility_result", "compatibility"),
    params=(
        ParamSpec(key="name", kind="array", cli=CLIParamBinding(flags=("--name",), action="append", default=None, help="Preset name(s) to check")),
        ParamSpec(key="file", cli=CLIParamBinding(flags=("--file",), default=None, help="Environment JSON file to check")),
        ParamSpec(key="include_default_on", kind="boolean", cli=CLIParamBinding(flags=("--include-default-on",), action="store_true", help="Include live Default On ranges in the check")),
    ),
    cli=CLICommandBinding(path=("environment", "check"), parent_destinations=("env_command",), parent_required=(True,)),
)


COMPAT_COMMAND = CommandSpec(
    id="compat_check",
    handler=HandlerRef("portskill.compatibility", "compatibility_result"),
    params=(
        ParamSpec(key="preset", kind="array", cli=CLIParamBinding(flags=("--preset",), action="append", default=None, help="Preset name (repeatable)")),
        ParamSpec(key="name", kind="array", cli=CLIParamBinding(flags=("--name",), action="append", default=None, help="Alias of --preset")),
        ParamSpec(key="presets", kind="array", cli=CLIParamBinding(flags=("--presets",), default=None, help="Comma-separated preset names"), mcp=MCPParamBinding(items_type="string", description="Preset names to check together", order=0), ui=UIParamBinding(("presets", "names"))),
        ParamSpec(key="file", cli=CLIParamBinding(flags=("--file",), default=None, help="Environment JSON file"), mcp=MCPParamBinding(description="Optional environment JSON path", order=1)),
        ParamSpec(key="include_default_on", kind="boolean", cli=CLIParamBinding(flags=("--include-default-on",), action="store_true", help="Include live Default On ranges"), mcp=MCPParamBinding(description="Include live Default On ranges", order=2)),
    ),
    cli=CLICommandBinding(path=("compat", "check"), help="Report port conflicts (default)", parent_destinations=("compat_command",), parent_required=(False,)),
    mcp=MCPCommandBinding(name="compat_check", description="Check whether selected presets/environments can share ports safely. Returns conflicts JSON; non-compatible is an error when used as a gate.", catalog_position=14),
    ui=UICommandBinding(actions=("compat-check",), binder="compat"),
)


PRESET_SAVE_COMMAND = CommandSpec(
    id="preset_save",
    handler=HandlerRef("portskill.presets", "preset_save_result"),
    params=(
        ParamSpec(key="name", cli=CLIParamBinding(flags=("--name",), required=True), mcp=MCPParamBinding(required=True, order=0), ui=UIParamBinding(("name",), required=True, coercion="stripped_or_none")),
        ParamSpec(key="description", cli=CLIParamBinding(flags=("--description",), default=""), mcp=MCPParamBinding(order=1), ui=UIParamBinding(("description",))),
        ParamSpec(key="project", kind="array", cli=CLIParamBinding(flags=("--project",), action="append", default=None), mcp=MCPParamBinding(items_type="string", description="Optional project path filters when snapshotting live ranges", order=2)),
        ParamSpec(key="from_file", cli=CLIParamBinding(flags=("--from-file",), default=None, help="Load services from an environment JSON file instead of live ranges"), mcp=MCPParamBinding(description="Optional environment JSON path", order=3)),
        ParamSpec(key="empty", kind="boolean", cli=CLIParamBinding(flags=("--empty",), action="store_true", help="Create an empty environment (no services)")),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(path=("preset", "save"), parent_destinations=("preset_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="preset_save", description="Save a named preset into the registry (snapshot live ranges or --from-file environment JSON).", catalog_position=15),
    ui=UICommandBinding(actions=("preset-save",), binder="preset_save"),
)


PRESET_LIST_COMMAND = CommandSpec(
    id="preset_list",
    handler=HandlerRef("portskill.presets", "preset_list_result", "none"),
    cli=CLICommandBinding(path=("preset", "list"), parent_destinations=("preset_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="preset_list", description="List named presets stored in the registry.", catalog_position=16),
)


PRESET_SHOW_COMMAND = CommandSpec(
    id="preset_show",
    handler=HandlerRef("portskill.presets", "preset_show_result"),
    params=(ParamSpec(key="name", cli=CLIParamBinding(flags=("--name",), required=True)),),
    cli=CLICommandBinding(path=("preset", "show"), parent_destinations=("preset_command",), parent_required=(True,)),
)


PRESET_DELETE_COMMAND = CommandSpec(
    id="preset_delete",
    handler=HandlerRef("portskill.presets", "preset_delete_result"),
    params=(ParamSpec(key="name", cli=CLIParamBinding(flags=("--name",), required=True), mcp=MCPParamBinding(required=True), ui=UIParamBinding(("name",), required=True, coercion="stripped_or_none")),),
    cli=CLICommandBinding(path=("preset", "delete"), parent_destinations=("preset_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="preset_delete", description="Delete a named preset from the registry.", catalog_position=18),
    ui=UICommandBinding(actions=("preset-delete",), binder="declared_params"),
)


PRESET_APPLY_COMMAND = CommandSpec(
    id="preset_apply",
    handler=HandlerRef("portskill.presets", "preset_apply_result"),
    params=(
        ParamSpec(key="name", cli=CLIParamBinding(flags=("--name",), required=True), mcp=MCPParamBinding(required=True, order=0), ui=UIParamBinding(("name",), required=True, coercion="stripped_or_none")),
        ParamSpec(key="also_stop_off", kind="boolean", cli=CLIParamBinding(flags=("--also-stop-off",), action="store_true", help="Also stop running services whose default_state is off"), mcp=MCPParamBinding(order=1), ui=UIParamBinding(("alsoStopOff",), coercion="boolean")),
    ),
    cli=CLICommandBinding(path=("preset", "apply"), parent_destinations=("preset_command",), parent_required=(True,)),
    mcp=MCPCommandBinding(name="preset_apply", description="Apply a named preset (import services + apply-defaults). Optional also_stop_off.", catalog_position=17),
    ui=UICommandBinding(actions=("preset-apply",), binder="declared_params"),
)


PRESET_CHECK_COMMAND = CommandSpec(
    id="preset_check",
    handler=HandlerRef("portskill.presets", "preset_check_result"),
    params=(ParamSpec(key="name", kind="array", cli=CLIParamBinding(flags=("--name",), action="append", required=True, help="Preset name (repeatable) to check for port conflicts")),),
    cli=CLICommandBinding(path=("preset", "check"), parent_destinations=("preset_command",), parent_required=(True,)),
)


SET_DEFAULT_COMMAND = CommandSpec(
    id="set_default",
    handler=HandlerRef("portskill.defaults", "set_default_result"),
    params=(
        ParamSpec(key="range_id", cli=CLIParamBinding(flags=("--range-id",), required=True), mcp=MCPParamBinding(required=True, order=0), ui=UIParamBinding(("rangeId",), required=True)),
        ParamSpec(key="state", cli=CLIParamBinding(flags=("--state",), required=True, choices=("on", "off")), mcp=MCPParamBinding(required=True, enum=("on", "off"), order=1), ui=UIParamBinding(("state",), required=True)),
        ParamSpec(key="project", cli=CLIParamBinding(flags=("--project",), default=None), mcp=MCPParamBinding(order=2), ui=UIParamBinding(("project",), coercion="nonempty_string_or_none")),
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(path=("set-default",)),
    mcp=MCPCommandBinding(name="set_default", description="Set default_state on|off for a range (portskill set-default).", catalog_position=10),
    ui=UICommandBinding(actions=("set-default",), binder="set_default"),
)


SET_DEFAULTS_FROM_CURRENT_COMMAND = CommandSpec(
    id="set_defaults_from_current",
    handler=HandlerRef("portskill.defaults", "set_defaults_from_current_result"),
    params=(
        ParamSpec(key="environment", cli=CLIParamBinding(flags=("--environment",), default=None, help="History env scope")),
    ),
    cli=CLICommandBinding(path=("set-defaults-from-current",), help="Set default_state on/off from each local range's current active/inactive state"),
    ui=UICommandBinding(actions=("set-defaults-from-current", "capture-defaults"), binder="none"),
)


APPLY_DEFAULTS_COMMAND = CommandSpec(
    id="apply_defaults",
    handler=HandlerRef("portskill.apply_defaults", "apply_defaults_result", "apply_defaults"),
    params=(
        ParamSpec(key="project", kind="array", cli=CLIParamBinding(flags=("--project",), action="append", default=None), mcp=MCPParamBinding(items_type="string", description="Optional project path filters", order=0)),
        ParamSpec(key="also_stop_off", kind="boolean", cli=CLIParamBinding(flags=("--also-stop-off",), action="store_true", help="Also stop running services whose default_state is off"), mcp=MCPParamBinding(order=1)),
    ),
    cli=CLICommandBinding(path=("apply-defaults",)),
    mcp=MCPCommandBinding(name="apply_defaults", description="Start Default Services: start services marked default_state=on. Optional also_stop_off stops Off services that are running.", catalog_position=11),
    ui=UICommandBinding(actions=("apply-defaults",), binder="none"),
)


DEACTIVATE_COMMAND = CommandSpec(
    id="deactivate",
    handler=HandlerRef("portskill.deactivate", "deactivate_result"),
    params=(
        ParamSpec(key="project", kind="array", cli=CLIParamBinding(flags=("--project",), action="append", default=None), mcp=MCPParamBinding(items_type="string", description="Optional project path filters", order=0)),
        ParamSpec(key="preset", cli=CLIParamBinding(flags=("--preset",), default=None, help="Limit teardown to services in this named preset (instead of default_state=on)"), mcp=MCPParamBinding(description="Limit to services in this named preset instead of default_state=on", order=1), ui=UIParamBinding(("preset",), coercion="stripped_or_none")),
        ParamSpec(key="also_release", kind="boolean", cli=CLIParamBinding(flags=("--also-release",), action="store_true", help="Also release ranges after stop (default keeps them reserved)"), mcp=MCPParamBinding(description="If true, release ranges after stop (default keeps reserved)", order=2), ui=UIParamBinding(("alsoRelease", "also_release"), coercion="boolean")),
    ),
    cli=CLICommandBinding(path=("deactivate",), aliases=("exit-house", "leave"), help="Deactivate environment: stop Default On services (keep reserved unless --also-release)"),
    mcp=MCPCommandBinding(name="deactivate", description="Deactivate environment: stop running default_state=on services (or --preset cohort) via stop.sh + tracked pgid; keep ranges reserved unless also_release. Symmetric to apply_defaults.", catalog_position=12),
    ui=UICommandBinding(actions=("deactivate", "exit-house"), binder="deactivate"),
)


EXIT_HOUSE_COMMAND = CommandSpec(
    id="exit_house",
    handler=HandlerRef("portskill.deactivate", "deactivate_result"),
    params=(
        ParamSpec(key="project", kind="array", mcp=MCPParamBinding(items_type="string", order=0)),
        ParamSpec(key="preset", mcp=MCPParamBinding(order=1)),
        ParamSpec(key="also_release", kind="boolean", mcp=MCPParamBinding(order=2)),
    ),
    mcp=MCPCommandBinding(name="exit_house", description="Alias of deactivate (compat).", catalog_position=13),
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


SET_TAILNET_COMMAND = CommandSpec(
    id="set_tailnet",
    handler=HandlerRef("portskill.tailscale_commands", "set_tailnet_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(flags=("--range-id",), required=True),
            mcp=MCPParamBinding(required=True, order=0),
            ui=UIParamBinding(("rangeId",), required=True),
        ),
        ParamSpec(
            key="mode",
            cli=CLIParamBinding(
                flags=("--mode",),
                required=True,
                choices=("serve", "none", "funnel"),
            ),
            mcp=MCPParamBinding(
                required=True,
                enum=("serve", "none", "funnel"),
                order=1,
            ),
            ui=UIParamBinding(("mode",), required=True),
        ),
        ParamSpec(
            key="project",
            cli=CLIParamBinding(flags=("--project",), default=None),
            mcp=MCPParamBinding(order=2),
            ui=UIParamBinding(
                ("project",), coercion="nonempty_string_or_none"
            ),
        ),
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(
                flags=("--environment",),
                default=None,
                help="History env scope",
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("set-tailnet",),
        help=(
            "Set recorded Tailscale mode (serve|none|funnel); apply when range "
            "is active"
        ),
    ),
    mcp=MCPCommandBinding(
        name="set_tailnet",
        description=(
            "Set Tailscale mode for a range: serve|none|funnel. UI toggles Serve "
            "on/off; Funnel is CLI-only. Requires Browser Login when enabling "
            "serve/funnel."
        ),
        catalog_position=21,
    ),
    ui=UICommandBinding(actions=("set-tailnet",), binder="declared_params"),
)


TAILSCALE_STATUS_COMMAND = CommandSpec(
    id="tailscale_status",
    handler=HandlerRef(
        "portskill.tailscale_commands", "tailscale_status_result", binder="none"
    ),
    cli=CLICommandBinding(
        path=("tailscale", "status"),
        help="Wrap tailscale status --json",
        parent_destinations=("tailscale_command",),
        parent_required=(True,),
        parent_helps=(
            "Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
        ),
    ),
    mcp=MCPCommandBinding(
        name="tailscale_status",
        description=(
            "Tailscale status JSON wrapper (BackendState, Self, chip "
            "Connected|Needs login|Binary missing)."
        ),
        catalog_position=22,
    ),
    ui=UICommandBinding(actions=("tailscale-status",), binder="none"),
)


TAILSCALE_LOGIN_COMMAND = CommandSpec(
    id="tailscale_login",
    handler=HandlerRef("portskill.tailscale_commands", "tailscale_login_result"),
    params=(
        ParamSpec(
            key="no_open",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--no-open",),
                action="store_true",
                help="Do not open the system browser (still returns auth_url)",
            ),
            mcp=MCPParamBinding(
                description="If true, do not open the system browser", order=0
            ),
        ),
        ParamSpec(
            key="wait",
            kind="integer",
            cli=CLIParamBinding(
                flags=("--wait",),
                type_name="integer",
                default=90,
                help=(
                    "Seconds to poll status --json for BackendState Running "
                    "(default 90)"
                ),
            ),
            mcp=MCPParamBinding(
                description="Seconds to poll for Connected (0 = return URL immediately)",
                order=1,
            ),
            ui=UIParamBinding(("wait",), coercion="integer_or_zero"),
        ),
    ),
    cli=CLICommandBinding(
        path=("tailscale", "login"),
        help="Browser Login: open Tailscale auth URL and poll until Connected",
        parent_destinations=("tailscale_command",),
        parent_required=(True,),
        parent_helps=(
            "Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
        ),
    ),
    mcp=MCPCommandBinding(
        name="tailscale_login",
        description=(
            "Browser Login: harvest Tailscale auth URL, optionally open the system "
            "browser, poll until Connected."
        ),
        catalog_position=23,
    ),
    ui=UICommandBinding(actions=("tailscale-login",), binder="tailscale_login"),
)


TAILSCALE_LOGOUT_COMMAND = CommandSpec(
    id="tailscale_logout",
    handler=HandlerRef(
        "portskill.tailscale_commands", "tailscale_logout_result", binder="none"
    ),
    cli=CLICommandBinding(
        path=("tailscale", "logout"),
        help="Log out of Tailscale (tailscale logout)",
        parent_destinations=("tailscale_command",),
        parent_required=(True,),
        parent_helps=(
            "Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
        ),
    ),
    ui=UICommandBinding(actions=("tailscale-logout",), binder="none"),
)


TAILSCALE_SERVE_PORTSKILL_COMMAND = CommandSpec(
    id="tailscale_serve_portskill",
    handler=HandlerRef("portskill.tailscale_commands", "serve_portskill_result"),
    params=(
        ParamSpec(
            key="state",
            cli=CLIParamBinding(
                flags=("--state",),
                choices=("on", "off", "status"),
                default="status",
                help="on=configure Serve, off=teardown, status=report (default)",
            ),
            ui=UIParamBinding(("enabled",), coercion="parsed_boolean"),
        ),
        ParamSpec(
            key="no_persist",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--no-persist",),
                action="store_true",
                help="Do not write settings.serve_portskill_on_tailscale",
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("tailscale", "serve-portskill"),
        help=(
            "Tailscale Serve the Portskill listen port from listen.json (never "
            "Funnel). New installs default off."
        ),
        parent_destinations=("tailscale_command",),
        parent_required=(True,),
        parent_helps=(
            "Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
        ),
    ),
    ui=UICommandBinding(actions=("serve-portskill",), binder="serve_portskill"),
)


PORTS_DISCOVER_COMMAND = CommandSpec(
    id="ports_discover",
    handler=HandlerRef(
        "portskill.port_discovery", "discover_ports_result", binder="none"
    ),
    cli=CLICommandBinding(
        path=("discover", "ports"),
        help="JSON: listeners + serve mappings + missing vs registry",
        parent_destinations=("discover_command",),
        parent_required=(True,),
        parent_helps=(
            "Poll occupied local ports + Tailscale Serve; import missing into workspace",
        ),
    ),
    mcp=MCPCommandBinding(
        name="ports_discover",
        description=(
            "Poll local TCP listeners + Tailscale Serve mappings; diff against "
            "non-released registry ranges (missing vs claimed)."
        ),
        catalog_position=24,
    ),
    ui=UICommandBinding(actions=("discover-ports",), binder="none"),
)


PORTS_IMPORT_COMMAND = CommandSpec(
    id="ports_import",
    handler=HandlerRef("portskill.port_discovery", "discover_import_result"),
    params=(
        ParamSpec(
            key="ports",
            kind="array",
            mcp=MCPParamBinding(
                items_type="integer",
                description="Exact port numbers to import (preferred)",
                order=0,
            ),
            ui=UIParamBinding(("ports",)),
        ),
        ParamSpec(
            key="port",
            kind="integer",
            cli=CLIParamBinding(
                flags=("--port",),
                type_name="integer",
                default=None,
                help="Exact start port to claim",
            ),
            mcp=MCPParamBinding(
                description="Single port alias when ports[] omitted", order=1
            ),
            ui=UIParamBinding(("port",)),
        ),
        ParamSpec(
            key="all_missing",
            kind="boolean",
            cli=CLIParamBinding(
                flags=("--all-missing",),
                action="store_true",
                help="Import every missing discovery (use with care)",
            ),
            mcp=MCPParamBinding(
                description="If true, import every missing discovery (use with care)",
                order=2,
            ),
            ui=UIParamBinding(("all_missing",)),
        ),
        ParamSpec(
            key="notes",
            kind="object",
            mcp=MCPParamBinding(
                additional_properties_type="string",
                description="Optional map of port(string|int) -> note",
                order=3,
            ),
            ui=UIParamBinding(("notes",)),
        ),
        ParamSpec(
            key="note",
            cli=CLIParamBinding(flags=("--note",), default=None),
            mcp=MCPParamBinding(
                description="Note applied when importing a single port", order=4
            ),
            ui=UIParamBinding(("note",), coercion="stripped_or_none"),
        ),
        ParamSpec(
            key="tailnet",
            cli=CLIParamBinding(
                flags=("--tailnet",),
                choices=("serve", "none"),
                default=None,
                help=(
                    "serve|none (default: serve if already Serving, else none). "
                    "Never Funnel."
                ),
            ),
            mcp=MCPParamBinding(
                enum=("serve", "none"),
                description=(
                    "serve|none (default: serve if already Serving, else none). "
                    "Never Funnel."
                ),
                order=5,
            ),
            ui=UIParamBinding(("tailnet",), coercion="import_tailnet_mode"),
        ),
        ParamSpec(
            key="project",
            cli=CLIParamBinding(
                flags=("--project",),
                default=None,
                help="Project path (default: guess or .)",
            ),
            mcp=MCPParamBinding(
                description="Project path (default: guess or .)", order=6
            ),
            ui=UIParamBinding(("project",)),
        ),
    ),
    cli=CLICommandBinding(
        path=("discover", "import"),
        help="Claim exact port(s) via allocate --start N (never silent 20xxx realloc)",
        parent_destinations=("discover_command",),
        parent_required=(True,),
        parent_helps=(
            "Poll occupied local ports + Tailscale Serve; import missing into workspace",
        ),
    ),
    mcp=MCPCommandBinding(
        name="ports_import",
        description=(
            "Import missing listening/Serve ports into the workspace by claiming "
            "the exact start port (allocate --start N; never silently reallocates "
            "from the 20xxx pool). Tailnet mode serve only if already Serving or "
            "user opts in; default none. Never Funnel."
        ),
        catalog_position=25,
    ),
    ui=UICommandBinding(actions=("discover-import",), binder="declared_params"),
)


HISTORY_LIST_COMMAND = CommandSpec(
    id="history_list",
    handler=HandlerRef("portskill.history", "history_list_result"),
    params=(
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(
                flags=("--environment",),
                default=None,
                help="Env name (default: focused or __all__)",
            ),
            mcp=MCPParamBinding(
                description="Env name (default: focused or __all__)"
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("history", "list"),
        help="List Original + steps for an environment",
        parent_destinations=("history_command",),
        parent_required=(True,),
        parent_helps=("Photoshop-style environment undo stack (per focused env)",),
    ),
    mcp=MCPCommandBinding(
        name="history_list",
        description=(
            "List Photoshop-style environment history (Original + steps) for "
            "focused or named env."
        ),
        catalog_position=26,
    ),
)


HISTORY_RESTORE_COMMAND = CommandSpec(
    id="history_restore",
    handler=HandlerRef("portskill.history", "history_restore_result"),
    params=(
        ParamSpec(
            key="index",
            kind="integer",
            cli=CLIParamBinding(flags=("--index",), type_name="integer", required=True),
            mcp=MCPParamBinding(required=True, minimum=0, order=0),
            ui=UIParamBinding(("index",), required=True, coercion="integer"),
        ),
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(flags=("--environment",), default=None),
            mcp=MCPParamBinding(order=1),
            ui=UIParamBinding(
                ("environment", "name"), coercion="stripped_or_none"
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("history", "restore"),
        help="Restore snapshot at index (0=Original)",
        parent_destinations=("history_command",),
        parent_required=(True,),
        parent_helps=("Photoshop-style environment undo stack (per focused env)",),
    ),
    mcp=MCPCommandBinding(
        name="history_restore",
        description=(
            "Restore environment history snapshot at index (0 = Original / baseline)."
        ),
        catalog_position=27,
    ),
    ui=UICommandBinding(actions=("history-restore",), binder="declared_params"),
)


HISTORY_RESET_COMMAND = CommandSpec(
    id="history_reset",
    handler=HandlerRef("portskill.history", "history_reset_result"),
    params=(
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(flags=("--environment",), default=None),
            mcp=MCPParamBinding(),
            ui=UIParamBinding(
                ("environment", "name"), coercion="stripped_or_none"
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("history", "reset"),
        help="Restore Original (index 0)",
        parent_destinations=("history_command",),
        parent_required=(True,),
        parent_helps=("Photoshop-style environment undo stack (per focused env)",),
    ),
    mcp=MCPCommandBinding(
        name="history_reset",
        description="Reset environment to Original (history index 0).",
        catalog_position=28,
    ),
    ui=UICommandBinding(actions=("history-reset",), binder="declared_params"),
)


HISTORY_CLEAR_COMMAND = CommandSpec(
    id="history_clear",
    handler=HandlerRef("portskill.history", "history_clear_result"),
    params=(
        ParamSpec(
            key="environment",
            cli=CLIParamBinding(flags=("--environment",), default=None),
            ui=UIParamBinding(
                ("environment", "name"), coercion="stripped_or_none"
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("history", "clear"),
        help="Drop stack; keep live state; re-baseline",
        parent_destinations=("history_command",),
        parent_required=(True,),
        parent_helps=("Photoshop-style environment undo stack (per focused env)",),
    ),
    ui=UICommandBinding(actions=("history-clear",), binder="declared_params"),
)


WORKSPACE_DRAFT_SET_COMMAND = CommandSpec(
    id="workspace_draft_set",
    handler=HandlerRef("portskill.workspace", "workspace_draft_set_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(flags=("--range-id",), required=True),
            ui=UIParamBinding(("rangeId",), required=True),
        ),
        ParamSpec(
            key="start_script",
            cli=CLIParamBinding(flags=("--start-script",), default=None),
            ui=UIParamBinding(("startScript", "start_script")),
        ),
        ParamSpec(
            key="stop_script",
            cli=CLIParamBinding(flags=("--stop-script",), default=None),
            ui=UIParamBinding(("stopScript", "stop_script")),
        ),
        ParamSpec(
            key="command",
            cli=CLIParamBinding(flags=("--command",), default=None),
            ui=UIParamBinding(("command",)),
        ),
        ParamSpec(
            key="cwd",
            cli=CLIParamBinding(flags=("--cwd",), default=None),
            ui=UIParamBinding(("cwd",)),
        ),
    ),
    cli=CLICommandBinding(
        path=("workspace", "draft-set"),
        help="Stage edit-mode fields for a range",
        parent_destinations=("workspace_command",),
        parent_required=(True,),
        parent_helps=("Workspace draft edit-mode (Save/Revert metadata)",),
    ),
    ui=UICommandBinding(actions=("draft-stage",), binder="workspace_draft"),
)


WORKSPACE_DRAFT_SAVE_COMMAND = CommandSpec(
    id="workspace_draft_save",
    handler=HandlerRef("portskill.workspace", "workspace_draft_save_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(
                flags=("--range-id",),
                default=None,
                help="Apply only this range's draft (default: all)",
            ),
            ui=UIParamBinding(("rangeId",)),
        ),
        ParamSpec(
            key="keep_edit_mode",
            kind="boolean",
            cli=CLIParamBinding(flags=("--keep-edit-mode",), action="store_true"),
        ),
    ),
    cli=CLICommandBinding(
        path=("workspace", "draft-save"),
        help="Persist draft metadata into registry",
        parent_destinations=("workspace_command",),
        parent_required=(True,),
        parent_helps=("Workspace draft edit-mode (Save/Revert metadata)",),
    ),
    ui=UICommandBinding(actions=("draft-save",), binder="workspace_draft"),
)


WORKSPACE_DRAFT_REVERT_COMMAND = CommandSpec(
    id="workspace_draft_revert",
    handler=HandlerRef("portskill.workspace", "workspace_draft_revert_result"),
    params=(
        ParamSpec(
            key="range_id",
            cli=CLIParamBinding(
                flags=("--range-id",),
                default=None,
                help="Clear only this range's draft (default: all)",
            ),
            ui=UIParamBinding(("rangeId",)),
        ),
        ParamSpec(
            key="keep_edit_mode",
            kind="boolean",
            cli=CLIParamBinding(flags=("--keep-edit-mode",), action="store_true"),
        ),
    ),
    cli=CLICommandBinding(
        path=("workspace", "draft-revert"),
        help="Discard draft; restore last saved",
        parent_destinations=("workspace_command",),
        parent_required=(True,),
        parent_helps=("Workspace draft edit-mode (Save/Revert metadata)",),
    ),
    ui=UICommandBinding(actions=("draft-revert",), binder="workspace_draft"),
)


WORKSPACE_EDIT_MODE_COMMAND = CommandSpec(
    id="workspace_edit_mode",
    handler=HandlerRef("portskill.workspace", "workspace_edit_mode_result"),
    params=(
        ParamSpec(
            key="state",
            cli=CLIParamBinding(
                flags=("--state",), choices=("on", "off"), default="on"
            ),
        ),
    ),
    cli=CLICommandBinding(
        path=("workspace", "edit-mode"),
        help="Enter/leave edit mode",
        parent_destinations=("workspace_command",),
        parent_required=(True,),
        parent_helps=("Workspace draft edit-mode (Save/Revert metadata)",),
    ),
)


COMMAND_SPECS = (
    ALLOCATE_COMMAND,
    ACTIVATE_COMMAND,
    START_COMMAND,
    STOP_COMMAND,
    RELEASE_COMMAND,
    STATUS_COMMAND,
    PATH_COMMAND,
    DOCTOR_COMMAND,
    ENVIRONMENT_EXPORT_COMMAND,
    ENVIRONMENT_IMPORT_COMMAND,
    SET_DEFAULT_COMMAND,
    SET_DEFAULTS_FROM_CURRENT_COMMAND,
    APPLY_DEFAULTS_COMMAND,
    DEACTIVATE_COMMAND,
    EXIT_HOUSE_COMMAND,
    COMPAT_COMMAND,
    PRESET_SAVE_COMMAND,
    PRESET_LIST_COMMAND,
    PRESET_APPLY_COMMAND,
    PRESET_DELETE_COMMAND,
    ENVIRONMENT_CHECK_COMMAND,
    PRESET_SHOW_COMMAND,
    PRESET_CHECK_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    SET_TAILNET_COMMAND,
    TAILSCALE_STATUS_COMMAND,
    TAILSCALE_LOGIN_COMMAND,
    TAILSCALE_LOGOUT_COMMAND,
    TAILSCALE_SERVE_PORTSKILL_COMMAND,
    PORTS_DISCOVER_COMMAND,
    PORTS_IMPORT_COMMAND,
    HISTORY_LIST_COMMAND,
    HISTORY_RESTORE_COMMAND,
    HISTORY_RESET_COMMAND,
    HISTORY_CLEAR_COMMAND,
    WORKSPACE_DRAFT_SET_COMMAND,
    WORKSPACE_DRAFT_SAVE_COMMAND,
    WORKSPACE_DRAFT_REVERT_COMMAND,
    WORKSPACE_EDIT_MODE_COMMAND,
)


def command_spec(spec_id: str) -> CommandSpec:
    for spec in COMMAND_SPECS:
        if spec.id == spec_id:
            return spec
    raise KeyError(spec_id)
