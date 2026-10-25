"""Callable registry-settings domain shared by CLI, MCP, and server adapters."""
from __future__ import annotations

import json
from typing import Any

from .domain import DomainError, pool_bounds_from_environment
from .mcp_catalog import canonical_mcp_tool_name, mcp_tools_map_for_profile
from .registry import (
    CorruptRegistryError,
    mutate_registry,
    normalize_mcp_user_commands,
    normalize_settings,
)
from .registry_domain import normalize_registry


def _fail(reason: str, message: str) -> None:
    raise DomainError(reason, message)


def _on_off(value: Any, option: str) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    token = str(value).strip().lower()
    if token not in ("on", "off"):
        _fail("invalid_args", f"{option} must be on|off")
    return token


def _mutate(mutator):
    start, end = pool_bounds_from_environment()
    try:
        return mutate_registry(
            mutator,
            start=start,
            end=end,
            normalizer=normalize_registry,
        )
    except CorruptRegistryError as exc:
        raise DomainError("corrupt_registry", str(exc)) from exc
    except DomainError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise DomainError("settings_unavailable", str(exc)) from exc


def settings_get() -> dict:
    """Return normalized settings without emitting output or exiting."""

    def read(registry):
        return {
            "status": "ok",
            "settings": normalize_settings(registry.get("settings")),
        }, False, []

    return _mutate(read)


def settings_set(options: dict[str, Any] | None = None) -> dict:
    """Apply settings options and return normalized settings."""
    values = options or {}

    def mutate(registry):
        settings = normalize_settings(registry.get("settings"))
        changed = False

        auto_apply = values.get("auto_apply")
        if auto_apply is not None:
            token = str(auto_apply).strip()
            if token.lower() in ("off", "none", "null", ""):
                settings["auto_apply_preset"] = None
                settings["auto_apply_on_launch"] = False
            else:
                settings["auto_apply_preset"] = token
                settings["auto_apply_on_launch"] = True
            changed = True

        auto_apply_preset = values.get("auto_apply_preset")
        if auto_apply_preset is not None:
            token = str(auto_apply_preset).strip()
            settings["auto_apply_preset"] = (
                None if token.lower() in ("none", "off", "null", "") else token
            )
            changed = True

        for key, option in (
            ("auto_apply_on_launch", "--auto-apply-on-launch"),
            ("auto_exit_on_shutdown", "--auto-exit-on-shutdown"),
            ("stop_also_release", "--stop-also-release"),
            ("serve_portskill_on_tailscale", "--serve-portskill-on-tailscale"),
            ("handoff_enabled", "--handoff-enabled"),
        ):
            if values.get(key) is not None:
                settings[key] = _on_off(values[key], option) == "on"
                changed = True

        if values.get("default_tailnet") is not None:
            token = str(values["default_tailnet"]).strip().lower()
            if token not in ("serve", "none"):
                _fail("invalid_args", "--default-tailnet must be serve|none")
            settings["default_tailnet"] = token
            changed = True

        if values.get("overlap_policy") is not None:
            token = str(values["overlap_policy"]).strip().lower()
            if token not in ("allow", "deny"):
                _fail("invalid_args", "--overlap-policy must be allow|deny")
            settings["overlap_policy"] = token
            changed = True

        if values.get("require_compat") is not None:
            _on_off(values["require_compat"], "--require-compat")
            settings["require_compat"] = True
            changed = True

        if values.get("open_environment_tabs") is not None:
            raw = values["open_environment_tabs"]
            if isinstance(raw, str):
                tabs = (
                    []
                    if raw.strip().lower() in ("", "none", "off", "[]")
                    else [part.strip() for part in raw.split(",") if part.strip()]
                )
            elif isinstance(raw, list):
                tabs = [str(part).strip() for part in raw if str(part).strip()]
            else:
                _fail(
                    "invalid_args",
                    "--open-environment-tabs must be comma-separated names or none",
                )
            settings["open_environment_tabs"] = tabs
            changed = True

        if values.get("focused_environment") is not None:
            token = str(values["focused_environment"]).strip()
            settings["focused_environment"] = (
                None if token.lower() in ("none", "off", "null", "") else token
            )
            changed = True

        if values.get("open_tab") is not None:
            name = str(values["open_tab"]).strip()
            if not name:
                _fail("invalid_args", "--open-tab requires a name")
            tabs = list(settings.get("open_environment_tabs") or [])
            if name not in tabs:
                tabs.append(name)
            settings["open_environment_tabs"] = tabs
            settings["focused_environment"] = name
            changed = True

        if values.get("close_tab") is not None:
            name = str(values["close_tab"]).strip()
            tabs = [
                tab
                for tab in (settings.get("open_environment_tabs") or [])
                if tab != name
            ]
            settings["open_environment_tabs"] = tabs
            if settings.get("focused_environment") == name:
                settings["focused_environment"] = tabs[-1] if tabs else None
            changed = True

        if values.get("mcp_tools_profile") is not None:
            token = str(values["mcp_tools_profile"]).strip().lower()
            if token not in ("full", "lean"):
                _fail("invalid_args", "--mcp-tools-profile must be full|lean")
            settings["mcp_tools"] = mcp_tools_map_for_profile(
                token, settings.get("mcp_tools") or {}
            )
            settings["mcp_tools_profile"] = token
            changed = True

        mcp_tool_args = values.get("mcp_tool") or []
        if mcp_tool_args:
            tools = dict(settings.get("mcp_tools") or {})
            for item in mcp_tool_args:
                token = str(item).strip()
                if "=" not in token:
                    _fail("invalid_args", "--mcp-tool requires NAME=on|off")
                name, _, state = token.partition("=")
                name = name.strip()
                state = state.strip().lower()
                if not name:
                    _fail("invalid_args", "--mcp-tool requires a tool name")
                if state not in ("on", "off"):
                    _fail("invalid_args", "--mcp-tool state must be on|off")
                tools[canonical_mcp_tool_name(name)] = state == "on"
            settings["mcp_tools"] = tools
            changed = True

        commands_json = values.get("mcp_user_commands_json")
        if commands_json is not None:
            raw = str(commands_json).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                _fail(
                    "invalid_args",
                    f"--mcp-user-commands-json must be valid JSON: {exc}",
                )
            if not isinstance(parsed, dict):
                _fail(
                    "invalid_args", "--mcp-user-commands-json must be a JSON object"
                )
            settings["mcp_user_commands"] = normalize_mcp_user_commands(parsed)
            changed = True

        command_upsert = values.get("mcp_user_command_upsert")
        if command_upsert is not None:
            raw = str(command_upsert).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                _fail(
                    "invalid_args",
                    f"--mcp-user-command-upsert must be valid JSON: {exc}",
                )
            if not isinstance(parsed, dict):
                _fail(
                    "invalid_args", "--mcp-user-command-upsert must be a JSON object"
                )
            name = parsed.get("name")
            if name is not None and not isinstance(name, str):
                # A list or dict name is unhashable as a key: reject it here
                # instead of letting the TypeError surface as settings_unavailable.
                _fail(
                    "invalid_args", "--mcp-user-command-upsert name must be a string"
                )
            commands = dict(settings.get("mcp_user_commands") or {})
            one = normalize_mcp_user_commands({name or "cmd": parsed})
            if not one:
                _fail(
                    "invalid_args", "user command needs name + at least one valid step"
                )
            commands.update(one)
            settings["mcp_user_commands"] = normalize_mcp_user_commands(commands)
            changed = True

        command_delete = values.get("mcp_user_command_delete")
        if command_delete is not None:
            name = str(command_delete).strip()
            if not name:
                _fail(
                    "invalid_args", "--mcp-user-command-delete requires a name"
                )
            commands = dict(settings.get("mcp_user_commands") or {})
            commands.pop(name, None)
            settings["mcp_user_commands"] = normalize_mcp_user_commands(commands)
            tools = dict(settings.get("mcp_tools") or {})
            if name in tools:
                tools.pop(name, None)
                settings["mcp_tools"] = tools
            changed = True

        for key, option in (
            ("handoff_kit", "--handoff-kit"),
            ("handoff_skill", "--handoff-skill"),
        ):
            if values.get(key) is not None:
                token = str(values[key]).strip()
                settings[key] = (
                    None
                    if token.lower() in ("none", "off", "null", "")
                    else token
                )
                changed = True

        tools_json = values.get("mcp_tools_json")
        if tools_json is not None:
            raw = str(tools_json).strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                _fail(
                    "invalid_args", f"--mcp-tools-json must be valid JSON: {exc}"
                )
            if not isinstance(parsed, dict):
                _fail("invalid_args", "--mcp-tools-json must be a JSON object")
            tools = dict(settings.get("mcp_tools") or {})
            for key, value in parsed.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                name = canonical_mcp_tool_name(key.strip())
                if isinstance(value, bool):
                    tools[name] = value
                elif isinstance(value, str):
                    token = value.strip().lower()
                    tools[name] = (
                        token == "on"
                        if token in ("on", "off")
                        else token in ("1", "true", "yes")
                    )
                else:
                    tools[name] = bool(value)
            settings["mcp_tools"] = tools
            changed = True

        if not changed:
            _fail(
                "invalid_args",
                "settings set requires a recognized flag (--auto-apply-preset, tabs, focus, --mcp-tool, --mcp-tools-profile, …)",
            )

        preset_name = settings.get("auto_apply_preset")
        if settings.get("auto_apply_on_launch") and preset_name:
            presets = registry.get("presets") or {}
            if preset_name not in presets:
                _fail("preset_not_found", f"preset not found: {preset_name}")

        settings["require_compat"] = True
        registry["settings"] = normalize_settings(settings)
        return {"status": "ok", "settings": registry["settings"]}, True, []

    return _mutate(mutate)
