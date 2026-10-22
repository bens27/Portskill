"""Adapter-independent MCP tool catalog and enablement policy."""
from __future__ import annotations

from .command_specs import (
    ACTIVATE_COMMAND,
    ALLOCATE_COMMAND,
    COMMAND_SPECS,
    DOCTOR_COMMAND,
    MISSING,
    RELEASE_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    STATUS_COMMAND,
    CommandSpec,
)
from .handoff import HANDOFF_TOOL_DEFS
from .registry import normalize_profile

PORTSKILL_TOOL_NAME = "portskill"


PORTSKILL_TOOL_ALIASES = frozenset({"portskill_path"})


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


def _mcp_tool_definition(spec: CommandSpec) -> dict:
    binding = spec.mcp
    if binding is None:
        raise ValueError(f"command has no MCP binding: {spec.id}")
    properties = {}
    required = []
    ordered_parameters = sorted(
        enumerate(spec.params),
        key=lambda item: (
            item[0]
            if item[1].mcp is None or item[1].mcp.order is MISSING
            else item[1].mcp.order
        ),
    )
    for _, parameter in ordered_parameters:
        param_binding = parameter.mcp
        if param_binding is None:
            continue
        name = parameter.key if param_binding.name is MISSING else param_binding.name
        schema = {
            "type": parameter.kind
            if param_binding.json_type is MISSING
            else param_binding.json_type,
        }
        if param_binding.enum is not MISSING:
            schema["enum"] = list(param_binding.enum)
        if param_binding.minimum is not MISSING:
            schema["minimum"] = param_binding.minimum
        if param_binding.items_type is not MISSING:
            schema["items"] = {"type": param_binding.items_type}
        if param_binding.additional_properties_type is not MISSING:
            schema["additionalProperties"] = {
                "type": param_binding.additional_properties_type
            }
        if param_binding.description is not MISSING:
            schema["description"] = param_binding.description
        properties[name] = schema
        if param_binding.required:
            required.append(name)
    input_schema = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    definition = {
        "name": binding.name,
        "description": binding.description,
        "inputSchema": input_schema,
    }
    if binding.annotations is not MISSING:
        definition["annotations"] = dict(binding.annotations)
    return definition


_SPEC_LEAN_MCP_TOOLS = frozenset(
    spec.mcp.name
    for spec in COMMAND_SPECS
    if spec.mcp is not None and spec.mcp.lean
)


LEAN_MCP_TOOLS_ENABLED = frozenset(
    {
        PORTSKILL_TOOL_NAME,
        "stop",
    }
) | _SPEC_LEAN_MCP_TOOLS


TOOL_DEFS = [
    _mcp_tool_definition(ALLOCATE_COMMAND),
    _mcp_tool_definition(ACTIVATE_COMMAND),
    {
        "name": "start",
        "description": "Low-level: run start.sh for an already-reserved range_id. To register and serve a new project, use portskill mode=start instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "project": {"type": "string"},
                "tailnet": {"type": "string", "enum": ["serve", "funnel", "none"]},
            },
            "required": ["range_id"],
        },
    },
    {
        "name": "stop",
        "description": (
            "Stop the tracked process group (stop.sh / pgid). "
            "Whether the range is released follows settings.stop_also_release (default true). "
            "When false, the range stays reserved and release is the explicit free. "
            "Optional also_release overrides the setting for this call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "project": {"type": "string"},
                "also_release": {
                    "type": "boolean",
                    "description": "Override settings.stop_also_release for this call",
                },
            },
            "required": ["range_id"],
        },
    },
    _mcp_tool_definition(RELEASE_COMMAND),
    _mcp_tool_definition(STATUS_COMMAND),
    {
        "name": "portskill",
        "description": PORTSKILL_TOOL_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["start", "stop", "release", "restart", "status"],
                    "description": (
                        "start = register and serve (allocate, wire, activate, start, tailnet); "
                        "stop, release, restart, or status"
                    ),
                },
                "project": {"type": "string", "description": "Project directory (default .)"},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Ports to allocate when start/restart needs a new range (default 1)",
                },
                "range_id": {"type": "string"},
                "note": {"type": "string"},
                "tailnet": {
                    "type": "string",
                    "enum": ["serve", "funnel", "none"],
                    "description": (
                        "Optional user-service Tailnet. Omitted follows settings.default_tailnet (none unless set to serve); none skips Serve. "
                        "serve requires login (needs_input to resume). "
                        "Never Funnels Portskill listen."
                    ),
                },
                "command": {"type": "string", "description": "Shell command that launches the service, e.g. npm run dev -- --port $PORT. Needed for start when start.sh is still a placeholder"},
                "cwd": {"type": "string"},
                "default_state": {"type": "string", "enum": ["on", "off"]},
                "start": {"type": "integer", "description": "Optional explicit start port when allocating"},
            },
            "required": ["mode"],
        },
    },
    _mcp_tool_definition(DOCTOR_COMMAND),
    {
        "name": "environment_export",
        "description": "Export a portable environment JSON of registered services (portskill environment export).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Environment name (e.g. ui-work)"},
                "out": {"type": "string", "description": "Optional output path; omit to return JSON in the tool result"},
                "project": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional project path filters",
                },
                "description": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "environment_import",
        "description": "Import an environment JSON into the registry (portskill environment import). Does not auto-start unless apply_defaults is true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Path to environment JSON"},
                "apply_defaults": {
                    "type": "boolean",
                    "description": "If true, run apply-defaults after import",
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "set_default",
        "description": "Set default_state on|off for a range (portskill set-default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "state": {"type": "string", "enum": ["on", "off"]},
                "project": {"type": "string"},
            },
            "required": ["range_id", "state"],
        },
    },
    {
        "name": "apply_defaults",
        "description": "Start Default Services: start services marked default_state=on. Optional also_stop_off stops Off services that are running.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional project path filters",
                },
                "also_stop_off": {"type": "boolean"},
            },
        },
    },
    {
        "name": "deactivate",
        "description": "Deactivate environment: stop running default_state=on services (or --preset cohort) via stop.sh + tracked pgid; keep ranges reserved unless also_release. Symmetric to apply_defaults.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional project path filters",
                },
                "preset": {
                    "type": "string",
                    "description": "Limit to services in this named preset instead of default_state=on",
                },
                "also_release": {
                    "type": "boolean",
                    "description": "If true, release ranges after stop (default keeps reserved)",
                },
            },
        },
    },
    {
        "name": "exit_house",
        "description": "Alias of deactivate (compat).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "array", "items": {"type": "string"}},
                "preset": {"type": "string"},
                "also_release": {"type": "boolean"},
            },
        },
    },
    {
        "name": "compat_check",
        "description": "Check whether selected presets/environments can share ports safely. Returns conflicts JSON; non-compatible is an error when used as a gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "presets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Preset names to check together",
                },
                "file": {"type": "string", "description": "Optional environment JSON path"},
                "include_default_on": {
                    "type": "boolean",
                    "description": "Include live Default On ranges",
                },
            },
        },
    },
    {
        "name": "preset_save",
        "description": "Save a named preset into the registry (snapshot live ranges or --from-file environment JSON).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "project": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional project path filters when snapshotting live ranges",
                },
                "from_file": {"type": "string", "description": "Optional environment JSON path"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "preset_list",
        "description": "List named presets stored in the registry.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "preset_apply",
        "description": "Apply a named preset (import services + apply-defaults). Optional also_stop_off.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "also_stop_off": {"type": "boolean"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "preset_delete",
        "description": "Delete a named preset from the registry.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    _mcp_tool_definition(SETTINGS_GET_COMMAND),
    _mcp_tool_definition(SETTINGS_SET_COMMAND),
    {
        "name": "set_tailnet",
        "description": "Set Tailscale mode for a range: serve|none|funnel. UI toggles Serve on/off; Funnel is CLI-only. Requires Browser Login when enabling serve/funnel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["serve", "none", "funnel"]},
                "project": {"type": "string"},
            },
            "required": ["range_id", "mode"],
        },
    },
    {
        "name": "tailscale_status",
        "description": "Tailscale status JSON wrapper (BackendState, Self, chip Connected|Needs login|Binary missing).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tailscale_login",
        "description": "Browser Login: harvest Tailscale auth URL, optionally open the system browser, poll until Connected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "no_open": {"type": "boolean", "description": "If true, do not open the system browser"},
                "wait": {"type": "integer", "description": "Seconds to poll for Connected (0 = return URL immediately)"},
            },
        },
    },
    {
        "name": "ports_discover",
        "description": "Poll local TCP listeners + Tailscale Serve mappings; diff against non-released registry ranges (missing vs claimed).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "ports_import",
        "description": (
            "Import missing listening/Serve ports into the workspace by claiming the exact start port "
            "(allocate --start N; never silently reallocates from the 20xxx pool). "
            "Tailnet mode serve only if already Serving or user opts in; default none. Never Funnel."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ports": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Exact port numbers to import (preferred)",
                },
                "port": {
                    "type": "integer",
                    "description": "Single port alias when ports[] omitted",
                },
                "all_missing": {
                    "type": "boolean",
                    "description": "If true, import every missing discovery (use with care)",
                },
                "notes": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional map of port(string|int) -> note",
                },
                "note": {"type": "string", "description": "Note applied when importing a single port"},
                "tailnet": {
                    "type": "string",
                    "enum": ["serve", "none"],
                    "description": "serve|none (default: serve if already Serving, else none). Never Funnel.",
                },
                "project": {"type": "string", "description": "Project path (default: guess or .)"},
            },
        },
    },
    {
        "name": "history_list",
        "description": "List Photoshop-style environment history (Original + steps) for focused or named env.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "environment": {"type": "string", "description": "Env name (default: focused or __all__)"},
            },
        },
    },
    {
        "name": "history_restore",
        "description": "Restore environment history snapshot at index (0 = Original / baseline).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "environment": {"type": "string"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "history_reset",
        "description": "Reset environment to Original (history index 0).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "environment": {"type": "string"},
            },
        },
    },
    *HANDOFF_TOOL_DEFS,
]


def canonical_mcp_tool_name(name: str) -> str:
    """Map one-cut aliases (portskill_path) to the tools/list name (portskill)."""
    if isinstance(name, str) and name.strip() in PORTSKILL_TOOL_ALIASES:
        return PORTSKILL_TOOL_NAME
    return name.strip() if isinstance(name, str) else name


def tool_enabled(prefs: dict, name: str) -> bool:
    """Missing key = enabled (default true). Dual-read portskill / portskill_path."""
    if not isinstance(name, str) or not name.strip():
        return True
    canonical = canonical_mcp_tool_name(name)
    if canonical in prefs:
        return bool(prefs[canonical])
    if canonical == PORTSKILL_TOOL_NAME:
        for alias in PORTSKILL_TOOL_ALIASES:
            if alias in prefs:
                return bool(prefs[alias])
    if name in prefs:
        return bool(prefs[name])
    return True


def system_tool_names() -> set[str]:
    return {
        t["name"]
        for t in TOOL_DEFS
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    }


def normalize_mcp_tools_profile(value) -> str:
    """Return full|lean. Missing or unknown values default to full (opt-in lean)."""
    return normalize_profile(value)


def mcp_tools_map_for_profile(profile: str, existing: dict | None = None) -> dict[str, bool]:
    """Expand a named profile into the settings.mcp_tools enable map.

    full: drop known system-tool keys (missing = enabled). User-command keys stay.
    lean: enable LEAN_MCP_TOOLS_ENABLED; disable other system tools including handoff_*.
    """
    token = normalize_mcp_tools_profile(profile)
    current = existing if isinstance(existing, dict) else {}
    system = system_tool_names()
    alias_or_system = set(system) | set(PORTSKILL_TOOL_ALIASES)
    preserved = {
        key: bool(val)
        for key, val in current.items()
        if isinstance(key, str) and key.strip() and key.strip() not in alias_or_system
    }
    if token == "full":
        return preserved
    applied = {name: name in LEAN_MCP_TOOLS_ENABLED for name in sorted(system)}
    applied.update(preserved)
    return applied
