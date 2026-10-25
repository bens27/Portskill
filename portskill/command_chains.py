"""Shared user-command chain analysis, driven by CommandSpecs.

One validator serves every caller: the side-effect-free chain dry run (UI /
HTTP ``mcp-user-command-dry-run``), the MCP chain runtime
(``mcp.call_user_command``) and the UI save (``mcp-user-command-save``). The
callers intentionally keep different strictness (decision C on a0a96c4b);
every difference is an explicit field of ``ChainPolicy`` rather than a
separate code path.

Import contract: this module must not import cli, mcp or server at any scope,
and must not load handler/owner modules at import time. The handoff gate is
imported lazily inside ``mcp_tools_prefs``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .command_specs import COMMAND_SPECS, CommandSpec
from .mcp_catalog import _mcp_tool_definition, canonical_mcp_tool_name, tool_enabled
from .registry import parse_bool, read_settings

ENABLED_LOOKUP_EXACT = "exact"
ENABLED_LOOKUP_CATALOG = "catalog"
ENABLED_LOOKUP_NONE = "none"


@dataclass(frozen=True)
class ChainPolicy:
    """How strictly one caller validates a chain step.

    resolve_aliases: map one-cut aliases (portskill_path) and padding to the
        catalog name before the unknown-tool check and dispatch.
    normalize_mode: strip/lower the step mode and map anything else to series;
        otherwise report ``step.get("mode") or "series"`` untouched.
    clear_stop_arguments: drop the arguments of ``stop`` steps before checks.
    enabled_lookup: ``exact`` reads only the raw step tool key from prefs
        (missing = enabled); ``catalog`` uses mcp_catalog.tool_enabled, which
        dual-reads portskill / portskill_path; ``none`` skips the check (and
        the settings read) entirely.
    forbid_nested: reject a step naming a saved user command (checked after
        the unknown-tool check, so it only fires for system-named commands).
    tolerate_user_command_read_errors: swallow failures while loading saved
        user commands for the nested check instead of raising.
    check_arguments: apply required / unknown / light type / enum checks from
        the spec-derived MCP input schema.
    include_error_codes: add a machine ``error`` code to rejections.
    unknown_message: formatted with ``tool`` and ``step``.
    """

    name: str
    resolve_aliases: bool
    normalize_mode: bool
    clear_stop_arguments: bool
    enabled_lookup: str
    tolerate_user_command_read_errors: bool
    forbid_nested: bool
    check_arguments: bool
    include_error_codes: bool
    unknown_message: str
    nested_message: str
    disabled_message: str


# Dry run: strict on arguments, literal on names and prefs. Known, kept
# difference: with mcp_tools {"portskill_path": false} a "portskill" step passes
# here (exact lookup) but the runtime refuses it (catalog lookup).
DRY_RUN_CHAIN_POLICY = ChainPolicy(
    name="dry-run",
    resolve_aliases=False,
    normalize_mode=True,
    clear_stop_arguments=True,
    enabled_lookup=ENABLED_LOOKUP_EXACT,
    tolerate_user_command_read_errors=True,
    forbid_nested=True,
    check_arguments=True,
    include_error_codes=False,
    unknown_message="unknown or non-system tool: {tool!r}",
    nested_message="nested user commands forbidden",
    disabled_message="tool disabled in settings.mcp_tools: {tool}",
)

# Runtime: resolves aliases and dual-reads prefs, leaves argument checking to
# the owners it dispatches to.
RUN_CHAIN_POLICY = ChainPolicy(
    name="run",
    resolve_aliases=True,
    normalize_mode=False,
    clear_stop_arguments=False,
    enabled_lookup=ENABLED_LOOKUP_CATALOG,
    tolerate_user_command_read_errors=False,
    forbid_nested=True,
    check_arguments=False,
    include_error_codes=True,
    unknown_message="Step tool must be an existing system TOOL_DEFS name (got {tool!r})",
    nested_message="v1: user commands cannot nest other user commands",
    disabled_message="Step tool disabled in settings.mcp_tools: {tool}",
)

# UI save: names only. No alias resolution, no prefs or user-command reads, no
# argument checks; mode kept raw, stop arguments dropped. Known, kept
# differences: disabled tools save fine, a system-named user command is not
# treated as nested. A non-string step tool (a list or dict included) is
# rejected as unknown, like any other bad name. The CLI owner normalizes mode
# and truncates to 12 steps after this.
SAVE_CHAIN_POLICY = ChainPolicy(
    name="save",
    resolve_aliases=False,
    normalize_mode=False,
    clear_stop_arguments=True,
    enabled_lookup=ENABLED_LOOKUP_NONE,
    tolerate_user_command_read_errors=False,
    forbid_nested=False,
    check_arguments=False,
    include_error_codes=False,
    unknown_message="each step.tool must be a system TOOL_DEFS name (got {step!r})",
    nested_message="nested user commands forbidden",
    disabled_message="tool disabled in settings.mcp_tools: {tool}",
)

# Same set as the historical re.fullmatch(r"[A-Za-z0-9_-]+", name), ASCII only.
_SAVE_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")

_ERROR_CODES = {
    "unknown": "unknown_or_non_system_tool",
    "nested": "nested_user_command_forbidden",
    "disabled": "tool_disabled",
}


def mcp_tools_prefs(registry_or_settings=None, *, load_settings: Callable[[], Any] | None = None) -> dict:
    """Return settings.mcp_tools map (name -> bool). Missing key = enabled."""
    from .handoff import HANDOFF_TOOL_NAMES, handoff_enabled  # noqa: PLC0415 - no eager owner load

    if registry_or_settings is None:
        settings = (load_settings or read_settings)()
    else:
        settings = registry_or_settings
    if not isinstance(settings, dict):
        settings = {}
    if isinstance(settings.get("settings"), dict):
        settings = settings["settings"]
    prefs = settings.get("mcp_tools", settings)
    if not isinstance(prefs, dict):
        prefs = {}
    out: dict[str, bool] = {}
    for key, val in prefs.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip()
        out[name] = parse_bool(val)
    if not handoff_enabled(settings):
        out.update({name: False for name in HANDOFF_TOOL_NAMES})
    return out


def user_commands(registry_or_settings=None, *, load_settings: Callable[[], Any] | None = None) -> dict:
    """Return the raw settings.mcp_user_commands map."""
    settings = None
    if registry_or_settings is None:
        settings = (load_settings or read_settings)()
    elif isinstance(registry_or_settings, dict):
        if "settings" in registry_or_settings and isinstance(registry_or_settings.get("settings"), dict):
            settings = registry_or_settings["settings"]
        elif "mcp_user_commands" in registry_or_settings:
            return registry_or_settings.get("mcp_user_commands") or {}
        else:
            settings = registry_or_settings
    if not isinstance(settings, dict):
        return {}
    raw = settings.get("mcp_user_commands")
    return raw if isinstance(raw, dict) else {}


def chain_tool_specs() -> dict[str, CommandSpec]:
    """System tools a chain step may name, read from the specs at call time."""
    return {spec.mcp.name: spec for spec in COMMAND_SPECS if spec.mcp is not None}


def chain_tool_names() -> set[str]:
    return set(chain_tool_specs())


def group_chain_steps(steps: list) -> list[tuple[str, list]]:
    """Group consecutive parallel steps; series steps are singleton groups."""
    groups: list[tuple[str, list]] = []
    pending_parallel: list = []
    for step in steps:
        mode = (step.get("mode") or "series").strip().lower()
        if mode == "parallel":
            pending_parallel.append(step)
            continue
        if pending_parallel:
            groups.append(("parallel", pending_parallel))
            pending_parallel = []
        groups.append(("series", [step]))
    if pending_parallel:
        groups.append(("parallel", pending_parallel))
    return groups


@dataclass(frozen=True)
class ChainStepCheck:
    """Outcome of validating one step. ``rejection`` is the caller-facing record."""

    tool: Any
    mode: Any
    arguments: dict
    target: Any
    rejection: dict | None = None
    argument_errors: tuple[str, ...] = ()


def _step_mode(step: dict, policy: ChainPolicy) -> Any:
    mode = step.get("mode") or "series"
    if not policy.normalize_mode:
        return mode
    if not isinstance(mode, str):
        mode = "series"
    mode = mode.strip().lower()
    if mode not in ("series", "parallel"):
        mode = "series"
    return mode


def _reject(policy: ChainPolicy, kind: str, tool: Any, mode: Any, message: str) -> dict:
    record = {"ok": False, "tool": tool, "mode": mode}
    if policy.include_error_codes:
        record["error"] = _ERROR_CODES[kind]
    record["message"] = message
    return record


def _is_enabled(policy: ChainPolicy, prefs: Mapping, tool: Any, dispatch: Any) -> bool:
    if policy.enabled_lookup == ENABLED_LOOKUP_EXACT:
        return True if tool not in prefs else bool(prefs.get(tool))
    if policy.enabled_lookup == ENABLED_LOOKUP_CATALOG:
        return tool_enabled(prefs, dispatch)
    if policy.enabled_lookup == ENABLED_LOOKUP_NONE:
        return True
    raise ValueError(f"unknown enabled_lookup: {policy.enabled_lookup!r}")


def _is_nested(policy: ChainPolicy, tool: Any, load_user_commands: Callable[[], dict]) -> bool:
    if not policy.tolerate_user_command_read_errors:
        return tool in load_user_commands()
    try:
        return tool in load_user_commands()
    except Exception:  # noqa: BLE001 - dry run never fails on unreadable settings
        return False


def _argument_errors(spec: CommandSpec, arguments: dict) -> list[str]:
    schema = _mcp_tool_definition(spec).get("inputSchema")
    schema = schema if isinstance(schema, dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    missing = []
    for key in required:
        if key not in arguments or arguments.get(key) in (None, ""):
            missing.append(key)
    unknown = [k for k in arguments.keys() if props and k not in props]
    msgs = []
    if missing:
        msgs.append("missing required: " + ", ".join(missing))
    if unknown:
        msgs.append("unknown args: " + ", ".join(unknown))
    # light type checks
    for key, val in arguments.items():
        if key not in props:
            continue
        p = props.get(key) or {}
        typ = p.get("type")
        if typ == "integer" and not isinstance(val, int):
            msgs.append(f"{key}: expected integer")
        elif typ == "number" and not isinstance(val, (int, float)):
            msgs.append(f"{key}: expected number")
        elif typ == "boolean" and not isinstance(val, bool):
            msgs.append(f"{key}: expected boolean")
        elif typ == "string" and not isinstance(val, str):
            msgs.append(f"{key}: expected string")
        elif typ == "array" and not isinstance(val, list):
            msgs.append(f"{key}: expected array")
        elif typ == "object" and not isinstance(val, dict):
            msgs.append(f"{key}: expected object")
        enum = p.get("enum")
        if isinstance(enum, list) and val not in enum:
            msgs.append(f"{key}: must be one of {enum}")
    return msgs


def validate_chain_step(
    step: dict,
    prefs: Mapping,
    tools: Mapping[str, CommandSpec],
    *,
    policy: ChainPolicy,
    load_user_commands: Callable[[], dict],
) -> ChainStepCheck:
    """Check one chain step: unknown -> nested -> disabled -> arguments."""
    tool = step.get("tool")
    arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
    mode = _step_mode(step, policy)
    if policy.clear_stop_arguments and tool == "stop":
        arguments = {}
    if policy.resolve_aliases:
        dispatch = canonical_mcp_tool_name(tool) if isinstance(tool, str) else tool
    else:
        dispatch = tool
    # A non-string tool (a list or dict included) is always unknown: checked
    # first, so an unhashable value never reaches the catalog lookup.
    if not isinstance(tool, str) or (tool not in tools and dispatch not in tools):
        message = policy.unknown_message.format(tool=tool, step=step)
        return ChainStepCheck(tool, mode, arguments, None, _reject(policy, "unknown", tool, mode, message))
    if policy.forbid_nested and _is_nested(policy, tool, load_user_commands):
        rejection = _reject(policy, "nested", tool, mode, policy.nested_message)
        return ChainStepCheck(tool, mode, arguments, None, rejection)
    if not _is_enabled(policy, prefs, tool, dispatch):
        message = policy.disabled_message.format(tool=tool)
        return ChainStepCheck(tool, mode, arguments, None, _reject(policy, "disabled", tool, mode, message))
    target = dispatch if dispatch in tools else tool
    errors = tuple(_argument_errors(tools[target], arguments)) if policy.check_arguments else ()
    return ChainStepCheck(tool, mode, arguments, target, None, errors)


def _summary(groups_out: list) -> str:
    parts = []
    for g in groups_out:
        tools = [t for t in (g.get("tools") or []) if t]
        if g.get("kind") == "parallel" and len(tools) > 1:
            if len(tools) == 2:
                parts.append(f"{tools[0]} and {tools[1]} in parallel")
            else:
                parts.append(", ".join(tools[:-1]) + f", and {tools[-1]} in parallel")
        elif tools:
            parts.append(tools[0])
    if not parts:
        return ""
    if len(parts) == 1:
        return f"Run {parts[0]}."
    return "Run " + ", then ".join(parts[:-1]) + f", then {parts[-1]}."


def dry_run_chain(steps: list, *, policy: ChainPolicy | None = None) -> dict:
    """Validate a user-command chain without calling tools (no side effects)."""
    policy = DRY_RUN_CHAIN_POLICY if policy is None else policy
    tools = chain_tool_specs()
    prefs = {}
    try:
        prefs = mcp_tools_prefs() or {}
    except Exception:  # noqa: BLE001
        prefs = {}

    step_results = []
    any_error = False
    for step in steps or []:
        if not isinstance(step, dict):
            step_results.append(
                {"ok": False, "tool": None, "mode": "series", "message": "step must be an object"}
            )
            any_error = True
            continue
        check = validate_chain_step(
            step, prefs, tools, policy=policy, load_user_commands=user_commands
        )
        if check.rejection is not None:
            step_results.append(check.rejection)
            any_error = True
            continue
        ok = not check.argument_errors
        if not ok:
            any_error = True
        step_results.append(
            {
                "ok": ok,
                "tool": check.tool,
                "mode": check.mode,
                "arguments": check.arguments,
                "message": "; ".join(check.argument_errors) if check.argument_errors else "ok",
            }
        )

    groups_out = []
    try:
        for kind, group in group_chain_steps(
            [
                {
                    "tool": s.get("tool"),
                    "mode": s.get("mode") or "series",
                    "arguments": s.get("arguments") if isinstance(s.get("arguments"), dict) else {},
                }
                for s in (steps or [])
                if isinstance(s, dict)
            ]
        ):
            groups_out.append(
                {"kind": kind, "tools": [g.get("tool") for g in group], "count": len(group)}
            )
    except Exception:  # noqa: BLE001
        groups_out = []

    return {
        "ok": not any_error and bool(step_results),
        "dry_run": True,
        "executed": False,
        "steps": step_results,
        "groups": groups_out,
        "summary": _summary(groups_out),
        "message": "validated without executing tools" if not any_error else "validation failed",
    }


def saved_chain_command(body: Mapping, *, policy: ChainPolicy | None = None) -> tuple[dict | None, str | None]:
    """Validate a UI save request. Return ``(command, None)`` or ``(None, message)``.

    Checks run in the historical order: name present, name charset, no system
    shadowing, non-empty step list, then each step until the first rejection.
    """
    policy = SAVE_CHAIN_POLICY if policy is None else policy
    tools = chain_tool_specs()
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "expected name"
    name = name.strip()
    if not set(name) <= _SAVE_NAME_CHARS:
        return None, "name must be letters, numbers, _ or -"
    if name in tools:
        return None, f"cannot shadow system tool: {name}"
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "expected non-empty steps"
    prefs = mcp_tools_prefs() if policy.enabled_lookup != ENABLED_LOOKUP_NONE else {}
    cleaned_steps = []
    for step in steps:
        if not isinstance(step, dict):
            return None, policy.unknown_message.format(tool=None, step=step)
        check = validate_chain_step(
            step, prefs, tools, policy=policy, load_user_commands=user_commands
        )
        if check.rejection is not None:
            return None, check.rejection["message"]
        if check.argument_errors:
            return None, "; ".join(check.argument_errors)
        cleaned_steps.append({"tool": check.tool, "arguments": check.arguments, "mode": check.mode})
    description = body.get("description")
    return {
        "name": name,
        "description": description if isinstance(description, str) else "",
        "steps": cleaned_steps,
    }, None
