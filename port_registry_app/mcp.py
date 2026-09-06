"""MCP protocol handlers (stdio + HTTP helpers) for Portskill."""
from __future__ import annotations

import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

from . import __version__
from .handoff import HANDOFF_TOOL_DEFS, HANDOFF_TOOL_NAMES, call_handoff_tool

SERVER_NAME = "portskill"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent

TOOL_DEFS = [
    {
        "name": "allocate",
        "description": "Reserve free ports for a project (port-registry allocate).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "description": "Number of ports"},
                "project": {"type": "string", "description": "Project directory (default .)"},
                "note": {"type": "string"},
                "tailnet": {
                    "type": "string",
                    "enum": ["serve", "funnel", "none"],
                    "description": "Required to avoid exit-3 needs_input; omit to be asked",
                },
                "machine": {
                    "type": "string",
                    "description": "Registry machine id (default local). Remotes are registry+links only.",
                },
                "host": {
                    "type": "string",
                    "description": "Optional advertise host override for port hyperlinks",
                },
                "scheme": {
                    "type": "string",
                    "enum": ["http", "https"],
                    "description": "URL scheme for port hyperlinks (default http)",
                },
            },
            "required": ["count"],
        },
    },
    {
        "name": "activate",
        "description": "Mark an allocated range active (port-registry activate).",
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
        "name": "start",
        "description": "Run project start.sh and activate the range (port-registry start).",
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
        "description": "Stop tracked process group and release the range (port-registry stop).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": ["range_id"],
        },
    },
    {
        "name": "release",
        "description": (
            "Free a range that is not running (port-registry release). "
            "Refuses with process_still_running if pid/pgid still live — use stop instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "range_id": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": ["range_id"],
        },
    },
    {
        "name": "status",
        "description": "Inspect registry state (port-registry status).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Optional project filter"},
            },
        },
    },
    {
        "name": "doctor",
        "description": "Environment checks: registry, Tailscale bin, app files, placeholder start.sh, default_state counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
            },
        },
    },
    {
        "name": "environment_export",
        "description": "Export a portable environment JSON of registered services (port-registry environment export).",
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
        "description": "Import an environment JSON into the registry (port-registry environment import). Does not auto-start unless apply_defaults is true.",
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
        "description": "Set default_state on|off for a range (port-registry set-default).",
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
        "description": "Activate environment: start services marked default_state=on. Optional also_stop_off stops Off services that are running.",
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
    {
        "name": "settings_get",
        "description": "Read registry settings (auto_apply_preset, auto_apply_on_launch, auto_exit_on_shutdown, require_compat).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "settings_set",
        "description": "Update auto-apply settings. Use auto_apply as shortcut (name enables launch apply; off clears).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "auto_apply_preset": {
                    "type": "string",
                    "description": "Preset name, or none/off to clear",
                },
                "auto_apply_on_launch": {"type": "boolean"},
                "auto_apply": {
                    "type": "string",
                    "description": "Shortcut: preset name enables launch apply; off clears",
                },
                "auto_exit_on_shutdown": {
                    "type": "boolean",
                    "description": "If true, Portskill UI attempts deactivate on SIGINT/SIGTERM",
                },
                "require_compat": {
                    "type": "boolean",
                    "description": "If true, refuse activate/apply/allocate that would collide on ports",
                },
                "overlap_policy": {
                    "type": "string",
                    "enum": ["allow", "deny"],
                    "description": "Legacy; prefer require_compat",
                },
                "open_environment_tabs": {
                    "type": "string",
                    "description": "Comma-separated open environment tab names, or none",
                },
                "focused_environment": {
                    "type": "string",
                    "description": "Focused environment name, or none",
                },
                "open_tab": {"type": "string", "description": "Open and focus an environment tab"},
                "close_tab": {"type": "string", "description": "Close an environment tab (does not delete)"},
            },
        },
    },
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





def _registry_path() -> pathlib.Path:
    configured = os.environ.get("PORT_REGISTRY_PATH", "~/.config/port-registry/registry.json")
    return pathlib.Path(configured).expanduser()


def _load_settings_from_registry() -> dict:
    """Read settings from the same registry.json path the CLI uses."""
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    settings = data.get("settings")
    return settings if isinstance(settings, dict) else {}


def _mcp_tools_prefs(registry_or_settings=None) -> dict:
    """Return settings.mcp_tools map (name -> bool). Missing key = enabled."""
    prefs = None
    if registry_or_settings is None:
        settings = _load_settings_from_registry()
        prefs = settings.get("mcp_tools")
    elif isinstance(registry_or_settings, dict):
        if "settings" in registry_or_settings and isinstance(registry_or_settings.get("settings"), dict):
            prefs = registry_or_settings["settings"].get("mcp_tools")
        elif "mcp_tools" in registry_or_settings and isinstance(registry_or_settings.get("mcp_tools"), dict):
            prefs = registry_or_settings.get("mcp_tools")
        else:
            # Treat as a raw mcp_tools map or settings dict
            if "mcp_tools" in registry_or_settings:
                prefs = registry_or_settings.get("mcp_tools")
            else:
                prefs = registry_or_settings
    if not isinstance(prefs, dict):
        return {}
    out: dict[str, bool] = {}
    for key, val in prefs.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip()
        if isinstance(val, bool):
            out[name] = val
        elif isinstance(val, str):
            out[name] = val.strip().lower() in ("1", "true", "yes", "on")
        else:
            out[name] = bool(val)
    return out


def _tool_enabled(prefs: dict, name: str) -> bool:
    """Missing key = enabled (default true)."""
    if name not in prefs:
        return True
    return bool(prefs[name])


def _system_tool_names() -> set[str]:
    return {
        t["name"]
        for t in TOOL_DEFS
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    }


def _load_user_commands(registry_or_settings=None) -> dict:
    """Return settings.mcp_user_commands map."""
    settings = None
    if registry_or_settings is None:
        settings = _load_settings_from_registry()
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


def _cli_env() -> dict:
    env = os.environ.copy()
    root = str(PACKAGE_ROOT)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else root + os.pathsep + existing
    return env


def run_cli(argv: list[str]) -> tuple[int, Any, str]:
    """Invoke port_registry_app.cli; return (exit_code, parsed_json_or_None, raw_stdout)."""
    cmd = [sys.executable, "-m", "port_registry_app.cli", *argv]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, env=_cli_env(), check=False
        )
    except OSError as exc:
        return 2, {"status": "error", "reason": "cli_exec_failed", "message": str(exc)}, ""
    stdout = (completed.stdout or "").strip()
    payload: Any = None
    if stdout:
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            payload = None
    return completed.returncode, payload, stdout


def tool_argv(name: str, arguments: dict) -> list[str]:
    args = arguments or {}
    if name == "allocate":
        argv = ["allocate", "--count", str(int(args["count"]))]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        if args.get("note"):
            argv += ["--note", str(args["note"])]
        if args.get("tailnet"):
            argv += ["--tailnet", str(args["tailnet"])]
        if args.get("machine"):
            argv += ["--machine", str(args["machine"])]
        if args.get("host"):
            argv += ["--host", str(args["host"])]
        if args.get("scheme"):
            argv += ["--scheme", str(args["scheme"])]
        return argv
    if name == "activate":
        argv = ["activate", "--range-id", str(args["range_id"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        if args.get("tailnet"):
            argv += ["--tailnet", str(args["tailnet"])]
        return argv
    if name == "start":
        argv = ["start", "--range-id", str(args["range_id"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        if args.get("tailnet"):
            argv += ["--tailnet", str(args["tailnet"])]
        return argv
    if name == "stop":
        argv = ["stop", "--range-id", str(args["range_id"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "release":
        argv = ["release", "--range-id", str(args["range_id"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "status":
        argv = ["status"]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "doctor":
        argv = ["doctor"]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "environment_export":
        argv = ["environment", "export", "--name", str(args["name"])]
        if args.get("out"):
            argv += ["--out", str(args["out"])]
        if args.get("description"):
            argv += ["--description", str(args["description"])]
        projects = args.get("project")
        if isinstance(projects, str) and projects:
            argv += ["--project", projects]
        elif isinstance(projects, list):
            for p in projects:
                argv += ["--project", str(p)]
        return argv
    if name == "environment_import":
        argv = ["environment", "import", "--file", str(args["file"])]
        if args.get("apply_defaults"):
            argv.append("--apply-defaults")
        return argv
    if name == "set_default":
        argv = ["set-default", "--range-id", str(args["range_id"]), "--state", str(args["state"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "apply_defaults":
        argv = ["apply-defaults"]
        projects = args.get("project")
        if isinstance(projects, str) and projects:
            argv += ["--project", projects]
        elif isinstance(projects, list):
            for p in projects:
                argv += ["--project", str(p)]
        if args.get("also_stop_off"):
            argv.append("--also-stop-off")
        return argv
    if name in ("deactivate", "exit_house"):
        argv = ["deactivate"]
        projects = args.get("project")
        if isinstance(projects, str) and projects:
            argv += ["--project", projects]
        elif isinstance(projects, list):
            for p in projects:
                argv += ["--project", str(p)]
        if args.get("preset"):
            argv += ["--preset", str(args["preset"])]
        if args.get("also_release"):
            argv.append("--also-release")
        return argv
    if name == "compat_check":
        argv = ["compat", "check"]
        presets = args.get("presets") or args.get("preset") or []
        if isinstance(presets, str):
            presets = [presets]
        for p in presets:
            if p:
                argv += ["--preset", str(p)]
        if args.get("file"):
            argv += ["--file", str(args["file"])]
        if args.get("include_default_on"):
            argv.append("--include-default-on")
        if len([a for a in argv if a == "--preset"]) == 0 and not args.get("file"):
            argv.append("--include-default-on")
        return argv
    if name == "preset_save":
        argv = ["preset", "save", "--name", str(args["name"])]
        if args.get("description"):
            argv += ["--description", str(args["description"])]
        if args.get("from_file"):
            argv += ["--from-file", str(args["from_file"])]
        projects = args.get("project")
        if isinstance(projects, str) and projects:
            argv += ["--project", projects]
        elif isinstance(projects, list):
            for p in projects:
                argv += ["--project", str(p)]
        return argv
    if name == "preset_list":
        return ["preset", "list"]
    if name == "preset_apply":
        argv = ["preset", "apply", "--name", str(args["name"])]
        if args.get("also_stop_off"):
            argv.append("--also-stop-off")
        return argv
    if name == "preset_delete":
        return ["preset", "delete", "--name", str(args["name"])]
    if name == "settings_get":
        return ["settings", "get"]
    if name == "settings_set":
        argv = ["settings", "set"]
        if args.get("auto_apply") is not None:
            argv += ["--auto-apply", str(args["auto_apply"])]
        if args.get("auto_apply_preset") is not None:
            argv += ["--auto-apply-preset", str(args["auto_apply_preset"])]
        if "auto_apply_on_launch" in args and args.get("auto_apply_on_launch") is not None:
            argv += ["--auto-apply-on-launch", "on" if args.get("auto_apply_on_launch") else "off"]
        if "auto_exit_on_shutdown" in args and args.get("auto_exit_on_shutdown") is not None:
            argv += ["--auto-exit-on-shutdown", "on" if args.get("auto_exit_on_shutdown") else "off"]
        if "require_compat" in args and args.get("require_compat") is not None:
            argv += ["--require-compat", "on" if args.get("require_compat") else "off"]
        if args.get("overlap_policy") is not None:
            argv += ["--overlap-policy", str(args["overlap_policy"])]
        if args.get("open_environment_tabs") is not None:
            argv += ["--open-environment-tabs", str(args["open_environment_tabs"])]
        if args.get("focused_environment") is not None:
            argv += ["--focused-environment", str(args["focused_environment"])]
        if args.get("open_tab") is not None:
            argv += ["--open-tab", str(args["open_tab"])]
        if args.get("close_tab") is not None:
            argv += ["--close-tab", str(args["close_tab"])]
        return argv
    if name == "set_tailnet":
        argv = ["set-tailnet", "--range-id", str(args["range_id"]), "--mode", str(args["mode"])]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "tailscale_status":
        return ["tailscale", "status"]
    if name == "tailscale_login":
        argv = ["tailscale", "login"]
        if args.get("no_open"):
            argv.append("--no-open")
        if args.get("wait") is not None:
            argv += ["--wait", str(int(args["wait"]))]
        return argv
    if name == "ports_discover":
        return ["discover", "ports"]
    if name == "ports_import":
        args = arguments or {}
        if args.get("all_missing"):
            argv = ["discover", "import", "--all-missing"]
        elif args.get("port") is not None:
            argv = ["discover", "import", "--port", str(int(args["port"]))]
        elif isinstance(args.get("ports"), list) and len(args.get("ports") or []) == 1:
            argv = ["discover", "import", "--port", str(int(args["ports"][0]))]
        elif isinstance(args.get("ports"), list) and len(args.get("ports") or []) > 1:
            # Multi handled in call_tool — return sentinel argv
            return ["__ports_import_multi__"]
        else:
            raise ValueError("ports_import requires ports[], port, or all_missing")
        if args.get("note"):
            argv += ["--note", str(args["note"])]
        if args.get("tailnet"):
            mode = str(args["tailnet"])
            if mode == "funnel":
                mode = "none"
            if mode not in ("serve", "none"):
                mode = "none"
            argv += ["--tailnet", mode]
        if args.get("project"):
            argv += ["--project", str(args["project"])]
        return argv
    if name == "history_list":
        argv = ["history", "list"]
        if args.get("environment"):
            argv += ["--environment", str(args["environment"])]
        return argv
    if name == "history_restore":
        argv = ["history", "restore", "--index", str(int(args["index"]))]
        if args.get("environment"):
            argv += ["--environment", str(args["environment"])]
        return argv
    if name == "history_reset":
        argv = ["history", "reset"]
        if args.get("environment"):
            argv += ["--environment", str(args["environment"])]
        return argv
    raise ValueError(f"unknown tool: {name}")



def _group_user_command_steps(steps: list) -> list[tuple[str, list]]:
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


def _run_one_chain_step(step: dict, prefs: dict, system_names: set[str]) -> dict:
    tool = step.get("tool")
    arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
    mode = step.get("mode") or "series"
    if not isinstance(tool, str) or tool not in system_names:
        return {
            "ok": False,
            "tool": tool,
            "mode": mode,
            "error": "unknown_or_non_system_tool",
            "message": f"Step tool must be an existing system TOOL_DEFS name (got {tool!r})",
        }
    if tool in _load_user_commands():
        return {
            "ok": False,
            "tool": tool,
            "mode": mode,
            "error": "nested_user_command_forbidden",
            "message": "v1: user commands cannot nest other user commands",
        }
    if not _tool_enabled(prefs, tool):
        return {
            "ok": False,
            "tool": tool,
            "mode": mode,
            "error": "tool_disabled",
            "message": f"Step tool disabled in settings.mcp_tools: {tool}",
        }
    result = call_tool(tool, arguments)
    is_err = bool(result.get("isError"))
    structured = result.get("structuredContent")
    return {
        "ok": not is_err,
        "tool": tool,
        "mode": mode,
        "arguments": arguments,
        "isError": is_err,
        "result": structured if structured is not None else result,
    }


def call_user_command(name: str, cmd: dict) -> dict:
    """Run a user command chain; series in order, parallel groups via ThreadPoolExecutor."""
    from .cli import MCP_USER_COMMAND_MAX_STEPS, normalize_mcp_user_commands

    prefs = _mcp_tools_prefs()
    system_names = _system_tool_names()
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


def call_tool(name: str, arguments: dict) -> dict:
    """Return MCP tools/call result payload (result object, not full JSON-RPC)."""
    args = arguments or {}
    if name in HANDOFF_TOOL_NAMES:
        return call_handoff_tool(name, args, _load_settings_from_registry())
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
    try:
        argv = tool_argv(name, arguments or {})
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "content": [{"type": "text", "text": json.dumps({"ok": False, "message": str(exc)})}],
            "isError": True,
        }
    if argv == ["__ports_import_multi__"]:
        return {
            "content": [{"type": "text", "text": json.dumps({"ok": False, "message": "multi-port routing failed"})}],
            "isError": True,
        }
    code, payload, raw = run_cli(argv)
    if code == 0:
        if name == "environment_export" and payload is None and raw:
            # Full environment JSON printed to stdout when --out omitted
            try:
                env_body = json.loads(raw)
            except json.JSONDecodeError:
                env_body = None
            if isinstance(env_body, dict) and env_body.get("kind") == "port-registry-environment":
                body = {"status": "ok", "environment": env_body}
                return {
                    "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
                    "structuredContent": body,
                    "isError": False,
                }
        body = payload if payload is not None else {"ok": True, "raw": raw}
        return {
            "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
            "structuredContent": body if isinstance(body, dict) else {"ok": True},
            "isError": False,
        }
    if code == 3:
        needs = payload if isinstance(payload, dict) else {"status": "needs_input", "raw": raw}
        needs = dict(needs)
        needs["ok"] = False
        needs["needs_input"] = True
        if "resume_hint" not in needs:
            needs["resume_hint"] = "--tailnet"
        return {
            "content": [{"type": "text", "text": json.dumps(needs, sort_keys=True)}],
            "structuredContent": needs,
            "isError": True,
        }
    err = payload if isinstance(payload, dict) else {"status": "error", "message": raw or f"exit {code}"}
    if isinstance(err, dict):
        err = dict(err)
        err.setdefault("ok", False)
    return {
        "content": [{"type": "text", "text": json.dumps(err, sort_keys=True)}],
        "structuredContent": err if isinstance(err, dict) else {"ok": False},
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
                    "Portskill MCP: tools allocate/activate/start/stop/release/status/doctor/environment_export/environment_import/set_default/apply_defaults/deactivate/compat_check/preset_save/preset_list/preset_apply/preset_delete/settings_get/settings_set/set_tailnet/tailscale_status/tailscale_login/history_list/history_restore/history_reset/ports_discover/ports_import "
                    "wrap the Portskill CLI against ~/.config/port-registry/registry.json "
                    "(or PORT_REGISTRY_PATH). Session Handoff tools use flat names "
                    "handoff_status/handoff_skill/handoff_template/handoff_list/handoff_resolve/"
                    "handoff_new_path/handoff_resume/handoff_supersede/handoff_install_help "
                    "(not nested session-handoff/*) and wrap the vendored kit ledger "
                    "(vendor/session-handoff-kit). User commands (x-portskill-kind:user-command) chain "
                    "enabled system tools (series/parallel; no nesting). On needs_input (Tailnet), "
                    "re-call with tailnet=serve|funnel|none. Prefer stop over release when a process "
                    "may still be running."
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
        known = set(system_names) | set(user_cmds.keys())
        if not isinstance(name, str) or name not in known:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        enabled_names = {t["name"] for t in enabled_tool_defs()}
        if name not in enabled_names:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32001, "message": f"tool_disabled: {name}"},
            }
        if name in user_cmds and name not in system_names:
            result = call_user_command(name, user_cmds[name])
        else:
            result = call_tool(name, arguments if isinstance(arguments, dict) else {})
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
        from .cli import listen_path as _listen_path

        path = _listen_path()
        if path.is_file():
            try:
                disk = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                disk = None
            if isinstance(disk, dict):
                info = disk
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
        from .cli import listen_path as _listen_path

        out["listen_path"] = str(_listen_path())
    if "setup" not in out:
        out["setup"] = {
            "cursor_mcp_stdio_hint": (
                "Preferred for agents: stdio MCP — examples/mcp.stdio.json "
                "or python3 -m port_registry_app --mcp-stdio"
            ),
            "cursor_mcp_http_hint": (
                "HTTP MCP is local-trust dogfood only and requires "
                "Authorization: Bearer (even on loopback). Prefer stdio for agents. "
                "Tailscale is not authentication."
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
        "stdio": "python3 -m port_registry_app --mcp-stdio",
    }
    payload.update(_listen_enrichment())
    return payload
