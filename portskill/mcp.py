"""MCP protocol handlers (stdio + HTTP helpers) for Portskill."""
from __future__ import annotations

import concurrent.futures
import json
import sys
from typing import Any

from . import __version__
from .command_runtime import execute_command, validate_mcp_boolean_args
from .command_specs import (
    ACTIVATE_COMMAND,
    ALLOCATE_COMMAND,
    APPLY_DEFAULTS_COMMAND,
    DEACTIVATE_COMMAND,
    DOCTOR_COMMAND,
    ENVIRONMENT_EXPORT_COMMAND,
    ENVIRONMENT_IMPORT_COMMAND,
    HISTORY_LIST_COMMAND,
    HISTORY_RESET_COMMAND,
    HISTORY_RESTORE_COMMAND,
    HANDOFF_INSTALL_HELP_COMMAND,
    HANDOFF_LIST_COMMAND,
    HANDOFF_NEW_PATH_COMMAND,
    HANDOFF_RESOLVE_COMMAND,
    HANDOFF_RESUME_COMMAND,
    HANDOFF_SKILL_COMMAND,
    HANDOFF_STATUS_COMMAND,
    HANDOFF_SUPERSEDE_COMMAND,
    HANDOFF_TEMPLATE_COMMAND,
    EXIT_HOUSE_COMMAND,
    PATH_COMMAND,
    PORTS_DISCOVER_COMMAND,
    PORTS_IMPORT_COMMAND,
    PRESET_APPLY_COMMAND,
    PRESET_DELETE_COMMAND,
    PRESET_LIST_COMMAND,
    PRESET_SAVE_COMMAND,
    COMPAT_COMMAND,
    RELEASE_COMMAND,
    SET_DEFAULT_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    SET_TAILNET_COMMAND,
    START_COMMAND,
    STATUS_COMMAND,
    STOP_COMMAND,
    SWEEP_COMMAND,
    TAILSCALE_LOGIN_COMMAND,
    TAILSCALE_STATUS_COMMAND,
)
from .handoff import (  # noqa: F401 - call_handoff_tool compatibility reexport
    HANDOFF_TOOL_NAMES,
    call_handoff_tool,
    handoff_enabled,
)
from .mcp_catalog import (
    LEAN_MCP_TOOLS_ENABLED,
    PORTSKILL_TOOL_ALIASES,
    PORTSKILL_TOOL_DESCRIPTION,
    PORTSKILL_TOOL_NAME,
    PORTSKILL_TOOL_PITCH,
    TOOL_DEFS,
    canonical_mcp_tool_name,
    mcp_tools_map_for_profile,
    normalize_mcp_tools_profile,
    system_tool_names as _system_tool_names,
    tool_enabled as _tool_enabled,
)
from .registry import (
    MCP_TOOLS_PROFILES,
    MCP_USER_COMMAND_MAX_STEPS,
    listen_path,
    parse_bool,
    normalize_mcp_user_commands,
    read_listen,
    read_settings,
)
from .domain import DomainError
from . import command_chains
from .command_chains import group_chain_steps as _group_user_command_steps  # noqa: F401 - compatibility alias
from .port_discovery import discover_import_result, discover_ports_result
from .tailscale_commands import set_tailnet_result, tailscale_login_result, tailscale_status_result
from .apply_defaults import apply_defaults_result  # noqa: F401 - compatibility reexport
from .defaults import set_default_result  # noqa: F401 - compatibility reexport
from .deactivate import deactivate_result  # noqa: F401 - compatibility reexport
from .doctor import doctor_result  # noqa: F401 - compatibility reexport
from .settings import settings_get, settings_set  # noqa: F401 - compatibility reexports
from .status import status_result  # noqa: F401 - compatibility reexport
from .registry_mutations import (  # noqa: F401 - compatibility reexports
    activate_result,
    allocate_result,
    release_result,
)
from .process_commands import start_result, stop_result  # noqa: F401 - compatibility reexports
from .path import run_path  # noqa: F401 - compatibility reexport

# Named settings.mcp_tools profiles. Apply writes the existing enable map
# (missing key = enabled). Default is full so existing installs stay unchanged
# until the user or agent opts into lean.


SERVER_NAME = "portskill"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"

HANDOFF_COMMANDS = {
    spec.mcp.name: spec
    for spec in (
        HANDOFF_STATUS_COMMAND,
        HANDOFF_SKILL_COMMAND,
        HANDOFF_TEMPLATE_COMMAND,
        HANDOFF_LIST_COMMAND,
        HANDOFF_RESOLVE_COMMAND,
        HANDOFF_NEW_PATH_COMMAND,
        HANDOFF_RESUME_COMMAND,
        HANDOFF_SUPERSEDE_COMMAND,
        HANDOFF_INSTALL_HELP_COMMAND,
    )
}


def _load_settings_from_registry() -> dict:
    """Read settings from the same registry.json path the CLI uses."""
    return read_settings()


def _mcp_tools_prefs(registry_or_settings=None) -> dict:
    """Return settings.mcp_tools map (name -> bool). Missing key = enabled."""
    return command_chains.mcp_tools_prefs(
        registry_or_settings, load_settings=_load_settings_from_registry
    )


def _load_user_commands(registry_or_settings=None) -> dict:
    """Return settings.mcp_user_commands map."""
    return command_chains.user_commands(
        registry_or_settings, load_settings=_load_settings_from_registry
    )


def user_command_tool_def(cmd: dict) -> dict:
    """MCP tool descriptor for a user command (not a fake system TOOL_DEFS entry)."""
    name = cmd.get("name") or "user-command"
    desc = cmd.get("description") or f"User MCP command chain ({name})"
    steps = cmd.get("steps") or []
    step_summary = ", ".join(
        f"{s.get('tool')}[{s.get('mode') or 'series'}]" for s in steps if isinstance(s, dict)
    )
    if step_summary:
        desc = f"{desc} — steps: {step_summary}"
    return {
        "name": name,
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "x-portskill-kind": "user-command",
            "readOnlyHint": False,
        },
        "x-portskill-kind": "user-command",
    }


def enabled_tool_defs(registry_or_settings=None) -> list:
    """Filtered TOOL_DEFS + enabled user commands via settings.mcp_tools."""
    prefs = _mcp_tools_prefs(registry_or_settings)
    out = [
        t
        for t in TOOL_DEFS
        if isinstance(t, dict)
        and isinstance(t.get("name"), str)
        and _tool_enabled(prefs, t["name"])
    ]
    system_names = _system_tool_names()
    for name, cmd in sorted(_load_user_commands(registry_or_settings).items()):
        if not isinstance(cmd, dict):
            continue
        if name in system_names:
            # Never shadow a system tool
            continue
        if not _tool_enabled(prefs, name):
            continue
        out.append(user_command_tool_def(cmd))
    return out


def _run_one_chain_step(step: dict, prefs: dict, system_names) -> dict:
    check = command_chains.validate_chain_step(
        step,
        prefs,
        system_names,
        policy=command_chains.RUN_CHAIN_POLICY,
        load_user_commands=_load_user_commands,
    )
    if check.rejection is not None:
        return check.rejection
    result = call_tool(check.target, check.arguments)
    is_err = bool(result.get("isError"))
    structured = result.get("structuredContent")
    return {
        "ok": not is_err,
        "tool": check.tool,
        "mode": check.mode,
        "arguments": check.arguments,
        "isError": is_err,
        "result": structured if structured is not None else result,
    }


def call_user_command(name: str, cmd: dict) -> dict:
    """Run a user command chain; series in order, parallel groups via ThreadPoolExecutor."""
    prefs = _mcp_tools_prefs()
    system_names = command_chains.chain_tool_specs()
    normalized = normalize_mcp_user_commands({name: cmd}).get(name)
    if not normalized:
        body = {"ok": False, "error": "invalid_user_command", "name": name}
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": True,
        }
    steps = list(normalized.get("steps") or [])[:MCP_USER_COMMAND_MAX_STEPS]
    step_results: list = []
    any_error = False
    for kind, group in _group_user_command_steps(steps):
        if kind == "parallel" and len(group) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(group))) as pool:
                futs = [
                    pool.submit(_run_one_chain_step, step, prefs, system_names) for step in group
                ]
                for fut in futs:
                    one = fut.result()
                    step_results.append(one)
                    if not one.get("ok"):
                        any_error = True
        else:
            for step in group:
                one = _run_one_chain_step(step, prefs, system_names)
                step_results.append(one)
                if not one.get("ok"):
                    any_error = True
    body = {
        "ok": not any_error,
        "kind": "user-command",
        "name": name,
        "description": normalized.get("description") or "",
        "steps": step_results,
        "x-portskill-kind": "user-command",
    }
    return {
        "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
        "structuredContent": body,
        "isError": any_error,
    }


def tool_error_result(exc: Exception) -> dict:
    """Map an in-process tool failure onto the existing MCP error contract."""
    if isinstance(exc, DomainError):
        body = exc.as_dict()
    else:
        body = {"status": "error", "message": str(exc) or exc.__class__.__name__}
    body.setdefault("ok", False)
    return {
        "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
        "structuredContent": body,
        "isError": True,
    }


def _import_tailnet_mode(value: Any) -> str | None:
    """MCP never imports with Funnel: anything but serve becomes none."""
    if not value:
        return None
    mode = str(value)
    return mode if mode == "serve" else "none"


def _tailscale_or_discovery_options(name: str, args: dict) -> dict:
    """Normalize MCP arguments in the legacy argument-check order.

    Raises KeyError/TypeError/ValueError at the same argument as the removed
    CLI translator did, so callers keep the legacy ``{ok: false, message}``
    argument-error envelope.
    """
    if name == "set_tailnet":
        range_id = str(args["range_id"])
        mode = str(args["mode"])
        return {"range_id": range_id, "mode": mode, "project": str(args["project"]) if args.get("project") else None}
    if name == "tailscale_login":
        return {
            "no_open": bool(args.get("no_open")),
            "wait": int(args["wait"]) if args.get("wait") is not None else None,
        }
    if name != "ports_import":
        return {}
    if args.get("all_missing"):
        port = None
    elif args.get("port") is not None:
        port = int(args["port"])
    elif isinstance(args.get("ports"), list) and len(args.get("ports") or []) == 1:
        port = int(args["ports"][0])
    else:
        raise ValueError("ports_import requires ports[], port, or all_missing")
    return {
        "port": port,
        "all_missing": bool(args.get("all_missing")),
        "note": str(args["note"]) if args.get("note") else None,
        "tailnet": _import_tailnet_mode(args.get("tailnet")),
        "project": str(args["project"]) if args.get("project") else None,
    }


def _tailscale_or_discovery_result(name: str, options: dict) -> dict:
    """In-process Tailscale / discovery tools; DomainError maps via tool_error_result."""
    spec = {
        "set_tailnet": SET_TAILNET_COMMAND,
        "tailscale_status": TAILSCALE_STATUS_COMMAND,
        "tailscale_login": TAILSCALE_LOGIN_COMMAND,
        "ports_discover": PORTS_DISCOVER_COMMAND,
        "ports_import": PORTS_IMPORT_COMMAND,
    }[name]
    return execute_command(spec, options)


def call_tool(name: str, arguments: dict) -> dict:
    """Call a tool without allowing one request failure to escape its transport."""
    try:
        return _call_tool(name, arguments)
    except Exception as exc:
        return tool_error_result(exc)


def _call_tool(name: str, arguments: dict) -> dict:
    """Return MCP tools/call result payload (result object, not full JSON-RPC)."""
    args = arguments or {}
    # Typed booleans before any branch: "false" must never read as true.
    validate_mcp_boolean_args(name, args)
    if name in HANDOFF_TOOL_NAMES:
        settings = _load_settings_from_registry()
        if not _tool_enabled(_mcp_tools_prefs(settings), name):
            body = {"ok": False, "error": "tool_disabled", "message": "Session Handoff is disabled. Enable it under Experimental (Beta)."}
            return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body, "isError": True}
        return execute_command(HANDOFF_COMMANDS[name], args)
    if name == "status":
        body = execute_command(STATUS_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "environment_export":
        exported = execute_command(ENVIRONMENT_EXPORT_COMMAND, args)
        body = exported if args.get("out") else {"status": "ok", "environment": exported}
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "environment_import":
        body = execute_command(ENVIRONMENT_IMPORT_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "set_default":
        body = execute_command(SET_DEFAULT_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "apply_defaults":
        body = execute_command(APPLY_DEFAULTS_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name in ("deactivate", "exit_house"):
        spec = DEACTIVATE_COMMAND if name == "deactivate" else EXIT_HOUSE_COMMAND
        body = execute_command(spec, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "compat_check":
        body = execute_command(COMPAT_COMMAND, args)
        if not body.get("compatible"):
            body["ok"] = False
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": not bool(body.get("compatible")),
        }
    if name in ("preset_save", "preset_list", "preset_apply", "preset_delete"):
        operation = {
            "preset_save": PRESET_SAVE_COMMAND,
            "preset_list": PRESET_LIST_COMMAND,
            "preset_apply": PRESET_APPLY_COMMAND,
            "preset_delete": PRESET_DELETE_COMMAND,
        }[name]
        body = execute_command(operation, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name in ("history_list", "history_restore", "history_reset"):
        operation = {
            "history_list": HISTORY_LIST_COMMAND,
            "history_restore": HISTORY_RESTORE_COMMAND,
            "history_reset": HISTORY_RESET_COMMAND,
        }[name]
        body = execute_command(operation, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "sweep":
        body = execute_command(SWEEP_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "doctor":
        doctor_args = dict(args)
        doctor_args.setdefault("project", ".")
        body = execute_command(DOCTOR_COMMAND, doctor_args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "settings_get":
        body = execute_command(SETTINGS_GET_COMMAND)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name == "settings_set":
        body = execute_command(SETTINGS_SET_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name in ("allocate", "activate", "release"):
        spec = {
            "allocate": ALLOCATE_COMMAND,
            "activate": ACTIVATE_COMMAND,
            "release": RELEASE_COMMAND,
        }[name]
        body = execute_command(spec, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name in ("start", "stop"):
        spec = START_COMMAND if name == "start" else STOP_COMMAND
        body = execute_command(spec, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    if name in (PORTSKILL_TOOL_NAME, *sorted(PORTSKILL_TOOL_ALIASES)):
        body = execute_command(PATH_COMMAND, args)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    # Multi-port import: invoke discover import once per port with optional per-port notes
    if name == "ports_import":
        ports = args.get("ports")
        notes_map = args.get("notes") if isinstance(args.get("notes"), dict) else {}
        if isinstance(ports, list) and len(ports) > 1 and not args.get("all_missing"):
            imported = []
            skipped = []
            errors = []
            for p in ports:
                try:
                    p_int = int(p)
                except (TypeError, ValueError):
                    errors.append({"port": p, "reason": "invalid_port"})
                    continue
                note = notes_map.get(str(p_int), notes_map.get(p_int, args.get("note")))
                one_args = {
                    "port": p_int,
                    "tailnet": args.get("tailnet"),
                    "project": args.get("project"),
                }
                if note:
                    one_args["note"] = note
                one = call_tool("ports_import", one_args)
                body = one.get("structuredContent") if isinstance(one.get("structuredContent"), dict) else {}
                if one.get("isError"):
                    errors.append({"port": p_int, "result": body})
                else:
                    imported.extend(body.get("imported") or [])
                    skipped.extend(body.get("skipped") or [])
            body = {
                "status": "ok" if not errors else "partial",
                "ok": not bool(errors),
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "count_imported": len(imported),
            }
            return {
                "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
                "structuredContent": body,
                "isError": bool(errors) and not imported,
            }
    if name in ("set_tailnet", "tailscale_status", "tailscale_login", "ports_discover", "ports_import"):
        try:
            options = _tailscale_or_discovery_options(name, args)
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": json.dumps({"ok": False, "message": str(exc)})}],
                "isError": True,
            }
        body = _tailscale_or_discovery_result(name, options)
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body,
            "isError": False,
        }
    return {
        "content": [{"type": "text", "text": json.dumps({"ok": False, "message": f"unknown tool: {name}"})}],
        "isError": True,
    }


def mcp_handle(message: dict) -> dict | None:
    """Handle one JSON-RPC MCP message. Returns response dict, or None for notifications."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = message.get("method")
    msg_id = message.get("id", None)
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Portskill MCP. portskill is one MCP tool for your agent to handle all port management functions. "
                    "When the user asks to register, host, serve or run something on Portskill, call "
                    "portskill mode=start (with command, and tailnet=serve if they want a Tailscale link); "
                    "do not stop after allocate. "
                    "Happy path: portskill, start, stop, release, and status. "
                    "allocate remains available. activate is an internal primitive the orchestrator may call; "
                    "it is off in lean and is not a happy-path peer (settings set --mcp-tool activate=on). "
                    "Other tools: doctor/environment_export/environment_import/set_default/apply_defaults/"
                    "deactivate/compat_check/preset_save/preset_list/preset_apply/preset_delete/"
                    "settings_get/settings_set/set_tailnet/tailscale_status/tailscale_login/"
                    "history_list/history_restore/history_reset/ports_discover/ports_import. "
                    "These wrap the Portskill CLI against ~/.config/portskill/registry.json "
                    "(or PORTSKILL_REGISTRY_PATH). Experimental (Beta): Session Handoff is disabled by default; "
                    "enable it in the UI or CLI before using its flat tool names "
                    "handoff_status/handoff_skill/handoff_template/handoff_list/handoff_resolve/"
                    "handoff_new_path/handoff_resume/handoff_supersede/handoff_install_help "
                    "(not nested session-handoff/*) and wrap the separate Session Handoff kit ledger. "
                    "User commands (x-portskill-kind:user-command) chain "
                    "enabled system tools (series/parallel; no nesting). On needs_input (Tailnet), "
                    "re-call with tailnet=serve|funnel|none. Prefer stop over release when a process "
                    "may still be running. stop honors settings.stop_also_release (default true)."
                ),
            },
        }
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "ping":
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": enabled_tool_defs()}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        system_names = _system_tool_names()
        user_cmds = _load_user_commands()
        canonical = canonical_mcp_tool_name(name) if isinstance(name, str) else name
        known = set(system_names) | set(user_cmds.keys()) | set(PORTSKILL_TOOL_ALIASES)
        if not isinstance(name, str) or name not in known:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        enabled_names = {t["name"] for t in enabled_tool_defs()}
        if name not in enabled_names and canonical not in enabled_names:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32001, "message": f"tool_disabled: {name}"},
            }
        dispatch = canonical if canonical in system_names else name
        if dispatch in user_cmds and dispatch not in system_names:
            result = call_user_command(dispatch, user_cmds[dispatch])
        else:
            result = call_tool(dispatch, arguments if isinstance(arguments, dict) else {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def mcp_stdio_loop() -> int:
    """Classic MCP stdio: newline-delimited JSON-RPC on stdin/stdout."""
    stdin = sys.stdin
    stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        while True:
            line = stdin.readline()
            if line == "":
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()
                continue
            resp = mcp_handle(message)
            if resp is not None:
                stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
                stdout.flush()
    finally:
        sys.stdout = stdout
    return 0


_LISTEN_RUNTIME: dict | None = None


def set_listen_runtime(info: dict | None) -> None:
    """Called by server after bind so GET /mcp can include absolute URLs."""
    global _LISTEN_RUNTIME
    _LISTEN_RUNTIME = dict(info) if isinstance(info, dict) else None


def _listen_enrichment() -> dict:
    info = _LISTEN_RUNTIME
    if not isinstance(info, dict) or not info.get("mcp_url"):
        # Fall back to on-disk listen.json (agents / menubar / post-restart)
        info = read_listen()
    if not isinstance(info, dict):
        return {}
    out = {}
    for key in (
        "host",
        "port",
        "ui_url",
        "mcp_url",
        "mcp_post",
        "mcp_get_discovery",
        "listen_path",
        "registry_path",
        "listening",
        "setup",
        "stdio",
    ):
        if key in info and info[key] is not None:
            out[key] = info[key]
    if "listen_path" not in out:
        out["listen_path"] = str(listen_path())
    if "setup" not in out:
        out["setup"] = {
            "cursor_mcp_stdio_hint": (
                "Stdio MCP is an available agent install option — "
                "examples/mcp.stdio.json or python3 -m portskill --mcp-stdio"
            ),
            "cursor_mcp_http_hint": (
                "HTTP MCP uses the same local listener as the HTML UI. "
                "Access does not require a token unless the optional passkey gate is enabled. "
                "Sharing Portskill's listen port with Tailscale Serve or Funnel is not a "
                "substitute for authentication, and Funnel of Portskill's own listen port is blocked."
            ),
            "tools_endpoint": "initialize / tools/list / tools/call via JSON-RPC on /mcp",
        }
    return out


def discovery_payload() -> dict:
    payload = {
        "ok": True,
        "transport": "streamable-http-json",
        "endpoint": "/mcp",
        "protocolVersion": PROTOCOL_VERSION,
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "tools": [t["name"] for t in enabled_tool_defs()],
        "stdio": "python3 -m portskill --mcp-stdio",
    }
    payload.update(_listen_enrichment())
    return payload
