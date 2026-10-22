"""Canonical Portskill configuration paths and registry persistence.

This module is deliberately stdlib-only and independent of the CLI, server, and
MCP adapters.  Those adapters may add domain-specific normalization, but all of
them share the same paths, settings schema, locking, and atomic file writes.
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import stat
import tempfile
from collections.abc import Callable
from typing import Any


VERSION = 1
DEFAULT_POOL_START = 20000
DEFAULT_POOL_END = 29999
DEFAULT_CONFIG_DIR = "~/.config/portskill"

REGISTRY_FILENAME = "registry.json"
LISTEN_FILENAME = "listen.json"
HTTP_AUTH_FILENAME = "http_auth.json"
HTTP_PASSKEY_FILENAME = "http_passkey.json"
HTTP_SESSIONS_FILENAME = "http_sessions.json"
HANDOFF_SKILL_FILENAME = "handoff-skill.md"

MCP_USER_COMMAND_MAX_STEPS = 12
MCP_TOOLS_PROFILES = ("full", "lean")
TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


class CorruptRegistryError(ValueError):
    """Raised when a registry exists but cannot safely be decoded."""

    def __init__(self, path: pathlib.Path, error: json.JSONDecodeError) -> None:
        self.path = path
        self.error = error
        super().__init__(
            f"registry JSON is corrupt at {path}: {error.msg} "
            f"(line {error.lineno} col {error.colno}); fix or restore the file — "
            "refusing to wipe"
        )


def _env_path(name: str) -> pathlib.Path | None:
    configured = os.environ.get(name)
    if configured and configured.strip():
        return pathlib.Path(configured).expanduser()
    return None


def config_dir() -> pathlib.Path:
    """Return the shared config directory.

    PORTSKILL_CONFIG_DIR is canonical.  When it is absent, the parent of the
    legacy PORTSKILL_REGISTRY_PATH remains the shared directory so existing
    isolated setups keep all sibling artifacts together.
    """
    configured = _env_path("PORTSKILL_CONFIG_DIR")
    if configured is not None:
        return configured
    legacy_registry = _env_path("PORTSKILL_REGISTRY_PATH")
    if legacy_registry is not None:
        return legacy_registry.parent
    return pathlib.Path(DEFAULT_CONFIG_DIR).expanduser()


def _artifact_path(env_name: str, filename: str) -> pathlib.Path:
    return _env_path(env_name) or (config_dir() / filename)


def registry_path() -> pathlib.Path:
    return _artifact_path("PORTSKILL_REGISTRY_PATH", REGISTRY_FILENAME)


def listen_path() -> pathlib.Path:
    return _artifact_path("PORTSKILL_LISTEN_PATH", LISTEN_FILENAME)


def http_auth_path() -> pathlib.Path:
    return _artifact_path("PORTSKILL_HTTP_AUTH_PATH", HTTP_AUTH_FILENAME)


def http_passkey_path() -> pathlib.Path:
    return _artifact_path("PORTSKILL_HTTP_PASSKEY_PATH", HTTP_PASSKEY_FILENAME)


def http_sessions_path() -> pathlib.Path:
    return _artifact_path("PORTSKILL_HTTP_SESSIONS_PATH", HTTP_SESSIONS_FILENAME)


def handoff_skill_path() -> pathlib.Path:
    return config_dir() / HANDOFF_SKILL_FILENAME


def backups_dir() -> pathlib.Path:
    return config_dir() / "backups"


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def normalize_profile(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in MCP_TOOLS_PROFILES:
        return value.strip().lower()
    return "full"


def normalize_overlap_policy(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in ("allow", "deny"):
        return value.strip().lower()
    return "allow"


def normalize_mcp_user_command_step(step: Any) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    tool = step.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None
    mode = step.get("mode") or "series"
    mode = mode.strip().lower() if isinstance(mode, str) else "series"
    if mode not in ("series", "parallel"):
        mode = "series"
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    return {"tool": tool.strip(), "arguments": arguments, "mode": mode}


def normalize_mcp_user_commands(raw: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, dict):
            continue
        name = key.strip()
        command_name = value.get("name")
        if not isinstance(command_name, str) or not command_name.strip():
            command_name = name
        description = value.get("description")
        if not isinstance(description, str):
            description = ""
        steps: list[dict[str, Any]] = []
        raw_steps = value.get("steps")
        if isinstance(raw_steps, list):
            for raw_step in raw_steps[:MCP_USER_COMMAND_MAX_STEPS]:
                normalized = normalize_mcp_user_command_step(raw_step)
                if normalized is not None:
                    steps.append(normalized)
        if not steps:
            continue
        out[command_name.strip()] = {
            "name": command_name.strip(),
            "description": description.strip(),
            "steps": steps,
        }
    return out


def default_settings() -> dict[str, Any]:
    return {
        "auto_apply_preset": None,
        "auto_apply_on_launch": False,
        "auto_exit_on_shutdown": False,
        "overlap_policy": "allow",
        "require_compat": True,
        "open_environment_tabs": [],
        "focused_environment": None,
        "mcp_tools": {},
        "mcp_tools_profile": "full",
        "stop_also_release": True,
        "default_tailnet": "none",
        "mcp_user_commands": {},
        "serve_portskill_on_tailscale": False,
        "handoff_enabled": False,
        "handoff_kit": None,
        "handoff_skill": None,
    }


def normalize_settings(settings: Any) -> dict[str, Any]:
    normalized = default_settings()
    if not isinstance(settings, dict):
        return normalized

    preset = settings.get("auto_apply_preset")
    if isinstance(preset, str):
        preset = preset.strip()
        if not preset or preset.lower() in ("none", "off", "null"):
            preset = None
    elif preset is not None:
        preset = None
    normalized["auto_apply_preset"] = preset
    normalized["auto_apply_on_launch"] = parse_bool(
        settings.get("auto_apply_on_launch"), False
    )
    normalized["auto_exit_on_shutdown"] = parse_bool(
        settings.get("auto_exit_on_shutdown"), False
    )
    normalized["overlap_policy"] = normalize_overlap_policy(
        settings.get("overlap_policy")
    )
    # Product rule: compatibility checks are always enabled.
    normalized["require_compat"] = True

    clean_tabs: list[str] = []
    tabs = settings.get("open_environment_tabs")
    if isinstance(tabs, list):
        for value in tabs:
            if isinstance(value, str):
                name = value.strip()
                if name and name not in clean_tabs:
                    clean_tabs.append(name)
    normalized["open_environment_tabs"] = clean_tabs
    focused = settings.get("focused_environment")
    normalized["focused_environment"] = (
        focused.strip() if isinstance(focused, str) and focused.strip() else None
    )

    tools: dict[str, bool] = {}
    raw_tools = settings.get("mcp_tools")
    if isinstance(raw_tools, dict):
        for key, value in raw_tools.items():
            if isinstance(key, str) and key.strip():
                tools[key.strip()] = parse_bool(value)
    normalized["mcp_tools"] = tools
    normalized["mcp_tools_profile"] = normalize_profile(
        settings.get("mcp_tools_profile")
    )
    normalized["stop_also_release"] = parse_bool(
        settings.get("stop_also_release"), True
    )
    tailnet = settings.get("default_tailnet")
    normalized["default_tailnet"] = (
        "serve"
        if isinstance(tailnet, str) and tailnet.strip().lower() == "serve"
        else "none"
    )
    normalized["serve_portskill_on_tailscale"] = parse_bool(
        settings.get("serve_portskill_on_tailscale"), False
    )
    normalized["mcp_user_commands"] = normalize_mcp_user_commands(
        settings.get("mcp_user_commands")
    )
    normalized["handoff_enabled"] = parse_bool(
        settings.get("handoff_enabled"), False
    )
    for key in ("handoff_kit", "handoff_skill"):
        value = settings.get(key)
        normalized[key] = (
            value.strip() if isinstance(value, str) and value.strip() else None
        )
    return normalized


def default_registry(
    start: int = DEFAULT_POOL_START, end: int = DEFAULT_POOL_END
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "pool": {"start": start, "end": end},
        "projects": {},
        "presets": {},
        "settings": default_settings(),
        "sessions": {"active": None, "history": []},
        "machines": {
            "local": {
                "id": "local",
                "label": "This Mac",
                "host": "127.0.0.1",
                "kind": "local",
            }
        },
        "environment_history": {},
        "workspace_draft": {"ranges": {}, "edit_mode": False},
    }


def normalize_registry_schema(
    data: Any,
    start: int | None = None,
    end: int | None = None,
) -> dict[str, Any]:
    """Apply the shared, additive portion of the registry schema.

    Default reads preserve valid stored pool bounds.  Passing bounds explicitly
    is a deliberate override used by the CLI's configured pool settings.
    """
    if not isinstance(data, dict):
        data = {}
    stored_pool = data.get("pool")
    if not isinstance(stored_pool, dict):
        stored_pool = {}
    try:
        pool_start = int(
            stored_pool.get("start", DEFAULT_POOL_START)
            if start is None
            else start
        )
    except (TypeError, ValueError):
        pool_start = DEFAULT_POOL_START
    try:
        pool_end = int(
            stored_pool.get("end", DEFAULT_POOL_END) if end is None else end
        )
    except (TypeError, ValueError):
        pool_end = DEFAULT_POOL_END
    data["version"] = VERSION
    data["pool"] = {"start": pool_start, "end": pool_end}
    for key in ("projects", "presets", "environment_history"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    if not isinstance(data.get("sessions"), dict):
        data["sessions"] = {"active": None, "history": []}
    if not isinstance(data.get("machines"), dict):
        data["machines"] = default_registry(pool_start, pool_end)["machines"]
    if not isinstance(data.get("workspace_draft"), dict):
        data["workspace_draft"] = {"ranges": {}, "edit_mode": False}
    data["settings"] = normalize_settings(data.get("settings"))
    return data


def _schema_needs_persist(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return True
    return any(
        not isinstance(raw.get(key), dict)
        for key in (
            "projects",
            "presets",
            "settings",
            "sessions",
            "machines",
            "environment_history",
            "workspace_draft",
        )
    )


def read_registry(
    *,
    start: int | None = None,
    end: int | None = None,
    normalizer: Callable[
        [Any, int | None, int | None], dict[str, Any]
    ] = normalize_registry_schema,
) -> dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        initial_start = DEFAULT_POOL_START if start is None else start
        initial_end = DEFAULT_POOL_END if end is None else end
        return normalizer(default_registry(initial_start, initial_end), start, end)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return normalizer(data, start, end)


def read_settings() -> dict[str, Any]:
    """Best-effort settings read for discovery adapters such as MCP."""
    try:
        return read_registry()["settings"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return default_settings()


def atomic_write_text(
    path: pathlib.Path,
    text: str,
    *,
    mode: int | None = None,
) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = mode
    if target_mode is None:
        try:
            target_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            pass
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path = pathlib.Path(temp_name)
        if target_mode is not None:
            os.chmod(temp_path, target_mode)
        os.replace(temp_path, path)
        temp_name = None
        if mode is not None:
            os.chmod(path, mode)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return path
    finally:
        if temp_name is not None:
            try:
                pathlib.Path(temp_name).unlink()
            except OSError:
                pass


def atomic_write_json(
    path: pathlib.Path,
    payload: Any,
    *,
    compact: bool = False,
    mode: int | None = None,
) -> pathlib.Path:
    if compact:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    else:
        raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return atomic_write_text(path, raw, mode=mode)


def read_listen() -> dict[str, Any] | None:
    path = listen_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_listen(payload: dict[str, Any]) -> pathlib.Path:
    return atomic_write_json(listen_path(), payload)


def mutate_registry(
    mutator: Callable[[dict[str, Any]], tuple[Any, bool, Any]],
    *,
    start: int = DEFAULT_POOL_START,
    end: int = DEFAULT_POOL_END,
    normalizer: Callable[[Any, int, int], dict[str, Any]] = normalize_registry_schema,
    after_write: Callable[[dict[str, Any], Any], None] | None = None,
) -> Any:
    """Lock, normalize, mutate, and atomically replace registry.json.

    The mutator returns ``(result, should_write, change_context)``.  The optional
    callback runs while the cross-process lock is still held and can update
    derived local project mirrors using that context.
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            raw_obj: Any = None
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                if raw.strip():
                    try:
                        raw_obj = json.loads(raw)
                    except json.JSONDecodeError as error:
                        raise CorruptRegistryError(path, error) from error
            registry = (
                raw_obj if raw_obj is not None else default_registry(start, end)
            )
            needs_schema_persist = _schema_needs_persist(raw_obj)
            registry = normalizer(registry, start, end)
            result, should_write, change_context = mutator(registry)
            if should_write or needs_schema_persist:
                atomic_write_json(path, registry, compact=True)
                if after_write is not None:
                    after_write(registry, change_context)
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
