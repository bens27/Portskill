"""Adapter-independent MCP tool catalog and enablement policy."""
from __future__ import annotations

from .command_specs import (
    ACTIVATE_COMMAND,
    ALLOCATE_COMMAND,
    APPLY_DEFAULTS_COMMAND,
    COMMAND_SPECS,
    DEACTIVATE_COMMAND,
    DOCTOR_COMMAND,
    ENVIRONMENT_EXPORT_COMMAND,
    ENVIRONMENT_IMPORT_COMMAND,
    EXIT_HOUSE_COMMAND,
    MISSING,
    PATH_COMMAND,
    PORTSKILL_TOOL_DESCRIPTION,
    PORTSKILL_TOOL_PITCH,
    PRESET_APPLY_COMMAND,
    PRESET_DELETE_COMMAND,
    PRESET_LIST_COMMAND,
    PRESET_SAVE_COMMAND,
    COMPAT_COMMAND,
    RELEASE_COMMAND,
    SET_DEFAULT_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    START_COMMAND,
    STATUS_COMMAND,
    STOP_COMMAND,
    CommandSpec,
)
from .handoff import HANDOFF_TOOL_DEFS
from .registry import normalize_profile

PORTSKILL_TOOL_NAME = "portskill"


PORTSKILL_TOOL_ALIASES = frozenset({"portskill_path"})


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
    _mcp_tool_definition(START_COMMAND),
    _mcp_tool_definition(STOP_COMMAND),
    _mcp_tool_definition(RELEASE_COMMAND),
    _mcp_tool_definition(STATUS_COMMAND),
    _mcp_tool_definition(PATH_COMMAND),
    _mcp_tool_definition(DOCTOR_COMMAND),
    _mcp_tool_definition(ENVIRONMENT_EXPORT_COMMAND),
    _mcp_tool_definition(ENVIRONMENT_IMPORT_COMMAND),
    _mcp_tool_definition(SET_DEFAULT_COMMAND),
    _mcp_tool_definition(APPLY_DEFAULTS_COMMAND),
    _mcp_tool_definition(DEACTIVATE_COMMAND),
    _mcp_tool_definition(EXIT_HOUSE_COMMAND),
    _mcp_tool_definition(COMPAT_COMMAND),
    _mcp_tool_definition(PRESET_SAVE_COMMAND),
    _mcp_tool_definition(PRESET_LIST_COMMAND),
    _mcp_tool_definition(PRESET_APPLY_COMMAND),
    _mcp_tool_definition(PRESET_DELETE_COMMAND),
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
