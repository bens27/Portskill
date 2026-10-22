#!/usr/bin/env python3
from __future__ import annotations
import argparse
from . import __version__
from .auth import (
    ensure_http_auth_token,
    http_auth_public_status,
    http_auth_result,
    http_auth_token,
    http_bearer_matches,
    regenerate_http_auth_token,
)
from .apply_defaults import apply_defaults_result as domain_apply_defaults_result
from .compatibility import (
    compat_report as domain_compat_report,
    compatibility_result as domain_compatibility_result,
    enforce_compatibility as domain_enforce_compatibility,
)
from .command_specs import (
    ACTIVATE_COMMAND,
    ALLOCATE_COMMAND,
    APPLY_DEFAULTS_COMMAND,
    CLIParamBinding,
    CommandSpec,
    DEACTIVATE_COMMAND,
    DOCTOR_COMMAND,
    MISSING,
    PATH_COMMAND,
    RELEASE_COMMAND,
    SET_DEFAULT_COMMAND,
    SET_DEFAULTS_FROM_CURRENT_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    START_COMMAND,
    STATUS_COMMAND,
    STOP_COMMAND,
)
from .command_runtime import execute_command
from .defaults import set_default_result, set_defaults_from_current_result  # noqa: F401 - compatibility reexports
from .deactivate import deactivate_result, item_matches_preset_service
from .http_cookies import HTTP_AUTH_COOKIE_NAME, HTTP_SESSION_COOKIE_NAME
from .history import (
    clear_history_stack,
    history_list_payload,
    history_clear_result,
    history_list_result,
    history_reset_result,
    history_restore_result,
    reset_history,
    restore_history,
)
from .handoff import fetch_kit_result
from .doctor import DOCTOR_FAIL_CLOSED_EXIT, DOCTOR_HARD_CHECKS
from .domain import (
    DomainError,
    pid_alive as domain_pid_alive,
    pool_bounds_from_environment,
    utc_now,
)
from .network import (
    ALLOW_NON_LOOPBACK_FLAG,
    bind_host_warning,
    is_loopback_host,
    listen_allows_non_loopback,
    non_loopback_refuse_message,
)
from .environments import (
    build_environment_payload as domain_build_environment_payload,
    environment_export_result,
    environment_import_result,
    environment_payload_from_preset as domain_environment_payload_from_preset,
    import_services_into_registry as domain_import_services_into_registry,
    load_environment_file as domain_load_environment_file,
)
from .lifecycle import (
    activate_item,
    funnel_of_listen_port,
    lifecycle,
    mark_item_active,
    needs_tailnet,
    pid_alive,
    placeholder_content,
    park_item_for_exit,
    process_group_pids,
    range_has_live_process,
    refuse_funnel_listen_message,
    refuse_remote_process,
    resolve_stop_also_release,
    release_item,
    resolve_project_relative,
    run_stop_script_if_ready,
    run_tailnet,
    should_use_sudo,
    tailscale_command,
    terminate_lifecycle_processes,
    validate_start_script,
    wait_for_processes_to_exit,
)
from .registry import (
    CorruptRegistryError,
    MCP_USER_COMMAND_MAX_STEPS,
    atomic_write_json,
    default_registry as initial_registry,
    default_settings,
    listen_path,
    mutate_registry,
    normalize_mcp_user_command_step,
    normalize_mcp_user_commands,
    normalize_overlap_policy,
    normalize_registry_schema,
    normalize_settings,
    parse_bool,
    read_listen,
    registry_path,
)
from .registry_domain import (
    DEFAULT_LIFECYCLE_DIR,
    DEFAULT_START_LOG,
    DEFAULT_START_SCRIPT,
    DEFAULT_STOP_LOG,
    DEFAULT_STOP_SCRIPT,
    HISTORY_ALL_KEY,
    HISTORY_STEPS_CAP,
    SESSION_HISTORY_CAP,
    default_sessions,
    default_lifecycle,
    default_machines,
    get_machine,
    history_is_modified,
    is_remote_range,
    migrate_legacy_project_files,
    normalize_default_state,
    normalize_environment_history,
    normalize_history_entry,
    normalize_lifecycle,
    normalize_machines,
    normalize_optional_str,
    normalize_preset_entry,
    normalize_preset_service,
    normalize_presets,
    normalize_range_extra,
    normalize_range_machine_fields,
    normalize_range_record,
    normalize_registry,
    normalize_session_preset,
    normalize_session_source,
    normalize_sessions,
    normalize_workspace_draft,
    project_path,
    resolve_range_host,
    resolve_range_scheme,
    resolve_range_url,
    workspace_draft_is_modified,
)
from .registry_mutations import (  # noqa: F401 - compatibility reexports
    activate_result,
    allocate_result,
    release_result,
)
from .registry_operations import (
    _cohort_range_refs,
    all_ranges,
    allocate_range,
    ensure_baseline,
    ensure_project,
    find_free_range,
    find_matching_unreleased,
    find_project_range,
    find_range_anywhere,
    locked_registry,
    maybe_push_history,
    mutate_registry_meta as domain_mutate_registry_meta,
    non_released_ranges,
    overlaps,
    pool_bounds,
    presets_claiming_ports,
    push_history_step,
    range_record,
    require_project_directory,
    resolve_history_env_name,
    snapshot_environment,
    write_local_project_file,
    write_local_project_files,
)
from .presets import (
    maybe_auto_apply_on_launch as domain_maybe_auto_apply_on_launch,
    preset_apply_for_launch,
    preset_apply_result,
    preset_check_result,
    preset_delete_result,
    preset_list_result,
    preset_save_result,
    preset_show_result,
)
from .process_commands import (
    iter_project_ranges,
    reclaim_range,
    run_start_all,
    run_stop_bulk,
    start_item_for_apply,
    start_result,
    stop_result,
)
from .service import launch_service, script_is_placeholder, service_launch
from .sessions import (
    begin_active_session as domain_begin_active_session,
    end_active_session as domain_end_active_session,
)
from .status import (
    enrich_range as enrich_range_for_status,
    observed_range_status,
    project_from_context,
    status_result,  # noqa: F401 - compatibility reexport
)
from .workspace import (
    workspace_draft_revert_result,
    workspace_draft_save_result,
    workspace_draft_set_result,
    workspace_edit_mode_result,
)
import copy
import json
import os
import pathlib
import shlex
import shutil
import signal
import uuid
from .port_discovery import discover_import_result, discover_ports_result
from .tailscale_commands import (  # noqa: F401 - re-exported for server/tests
    apply_portskill_tailscale_serve,
    build_portskill_serve_url,
    probe_portskill_serve_status,
    serve_portskill_result,
    set_tailnet_result,
    tailscale_login_result,
    tailscale_logout_result,
    tailscale_status_result,
)
from .tailscale import (
    resolve_tailscale_bin,
    tailscale_base_command,
)


VERSION = 1
DEFAULT_POOL_START = 20000
DEFAULT_POOL_END = 29999


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DomainError("invalid_args", message)


def emit(payload):
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))








def fail(reason, message=None, exit_code=2, **extra):
    payload = {"status": "error", "reason": reason}
    if message is not None:
        payload["message"] = message
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    emit(payload)
    raise SystemExit(exit_code)








MCP_USER_COMMAND_TOOL_NAMES = None  # filled lazily from mcp.TOOL_DEFS when validating


def overlap_denied_payload(active):
    label = (active or {}).get("preset") or "(ad-hoc)"
    return {
        "status": "error",
        "reason": "overlap_denied",
        "message": (
            f"Another environment is already active ({label}). "
            "Deactivate it first, or allow overlap."
        ),
        "active": active,
        "hint": "Run deactivate first, or: settings set --overlap-policy allow",
    }


def check_overlap_denied(registry):
    """Legacy session-overlap gate — disabled. Prefer require_compat + compat_check."""
    return None


def begin_active_session(registry, preset, source):
    return domain_begin_active_session(registry, preset, source)


def end_active_session(registry):
    return domain_end_active_session(registry)


def build_schedule_windows(registry, focus=None):
    """Opaque busy windows for schedule/busy view (no services list)."""
    sessions = normalize_sessions(registry.get("sessions"))
    focus_name = normalize_session_preset(focus)
    windows = []
    active = sessions.get("active")
    active_key = None
    if active and active.get("started_at"):
        active_key = (active.get("preset"), active.get("started_at"))
    for entry in sessions.get("history") or []:
        preset = entry.get("preset")
        if focus_name is not None and preset != focus_name:
            continue
        windows.append(
            {
                "preset": preset,
                "started_at": entry.get("started_at"),
                "ended_at": entry.get("ended_at"),
                "busy": True,
                "opaque": True,
            }
        )
    # Ensure open active window is present even if history was trimmed oddly
    if active and active.get("started_at"):
        if focus_name is None or active.get("preset") == focus_name:
            found_open = any(
                w.get("started_at") == active.get("started_at")
                and w.get("preset") == active.get("preset")
                and w.get("ended_at") is None
                for w in windows
            )
            if not found_open:
                windows.append(
                    {
                        "preset": active.get("preset"),
                        "started_at": active.get("started_at"),
                        "ended_at": None,
                        "busy": True,
                        "opaque": True,
                    }
                )
    windows.sort(key=lambda w: w.get("started_at") or "")
    return {
        "status": "ok",
        "overlap_policy": normalize_settings(registry.get("settings")).get("overlap_policy"),
        "active": active,
        "focus": focus_name,
        "windows": windows,
        "count": len(windows),
    }














































def run_compat_check(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    return domain_compat_report(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )






def enforce_compat_or_fail(
    registry,
    preset_names=None,
    services=None,
    include_default_on=False,
    project_filter=None,
):
    return domain_enforce_compatibility(
        registry,
        preset_names=preset_names,
        services=services,
        include_default_on=include_default_on,
        project_filter=project_filter,
    )




















































def cmd_allocate(args):
    return execute_command(ALLOCATE_COMMAND, vars(args))


def cmd_activate(args):
    return execute_command(ACTIVATE_COMMAND, vars(args))




def cmd_release(args):
    return execute_command(RELEASE_COMMAND, vars(args))


def cmd_start(args):
    return execute_command(START_COMMAND, vars(args))


def cmd_stop(args):
    return execute_command(STOP_COMMAND, vars(args))


















def build_environment_payload(registry, name, description="", project_filter=None):
    return domain_build_environment_payload(registry, name, description, project_filter)


def environment_payload_from_preset(registry, preset_name, export_name=None, description=None):
    return domain_environment_payload_from_preset(
        registry, preset_name, export_name=export_name, description=description
    )


def cmd_environment_export(args):
    options = {
        "name": args.name,
        "description": getattr(args, "description", None),
        "from_preset": getattr(args, "from_preset", None),
        "project": list(args.project) if args.project else None,
        "out": args.out,
    }
    payload = environment_export_result(options)
    if args.out:
        emit(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
    return


def load_environment_file(path):
    return domain_load_environment_file(path)


def import_services_into_registry(registry, services):
    return domain_import_services_into_registry(registry, services)


def cmd_environment_import(args):
    emit(environment_import_result({
        "file": args.file,
        "environment": getattr(args, "environment", None),
        "apply_defaults": bool(args.apply_defaults),
    }))
    return


def cmd_set_default(args):
    emit(execute_command(SET_DEFAULT_COMMAND, vars(args)))


def cmd_set_defaults_from_current(args):
    emit(execute_command(SET_DEFAULTS_FROM_CURRENT_COMMAND, vars(args)))




def stop_item_for_apply(item, project):
    from .apply_defaults import stop_item_for_apply as domain_stop_item_for_apply

    return domain_stop_item_for_apply(item, project)


def run_apply_defaults(
    project_filter=None,
    also_stop_off=False,
    *,
    session_preset=None,
    session_source="apply_defaults",
    record_session=True,
    enforce_overlap=True,
):
    return domain_apply_defaults_result(
        project_filter=project_filter,
        also_stop_off=also_stop_off,
        session_preset=session_preset,
        session_source=session_source,
        record_session=record_session,
        enforce_overlap=enforce_overlap,
    )


def cmd_apply_defaults(args):
    emit(execute_command(APPLY_DEFAULTS_COMMAND, vars(args)))


def run_exit_house(project_filter=None, preset_name=None, also_release=False, clear_session=True):
    return deactivate_result({
        "project": project_filter,
        "preset": preset_name,
        "also_release": also_release,
        "clear_session": clear_session,
    })


def cmd_deactivate(args):
    emit(execute_command(DEACTIVATE_COMMAND, vars(args)))


# Compat alias
cmd_exit_house = cmd_deactivate




def mutate_registry_meta(mutator):
    return domain_mutate_registry_meta(mutator)


def cmd_preset_save(args):
    emit(preset_save_result({
        "name": args.name,
        "description": getattr(args, "description", "") or "",
        "project": list(args.project) if args.project else None,
        "from_file": getattr(args, "from_file", None),
        "empty": bool(getattr(args, "empty", False)),
        "environment": getattr(args, "environment", None),
    }))
    return


def cmd_preset_list(args):
    emit(preset_list_result())
    return


def cmd_preset_show(args):
    emit(preset_show_result({"name": args.name}))
    return


def cmd_preset_delete(args):
    emit(preset_delete_result({"name": args.name}))
    return


def run_preset_apply(name, also_stop_off=False):
    return preset_apply_result({"name": name, "also_stop_off": also_stop_off})


def cmd_preset_apply(args):
    also_stop_off = bool(getattr(args, "also_stop_off", False))
    emit(run_preset_apply(args.name, also_stop_off=also_stop_off))


def cmd_preset_check(args):
    report = preset_check_result({"name": list(args.name) if getattr(args, "name", None) else []})
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)
    return


def cmd_preset(args):
    cmd = args.preset_command
    if cmd == "save":
        return cmd_preset_save(args)
    if cmd == "list":
        return cmd_preset_list(args)
    if cmd == "show":
        return cmd_preset_show(args)
    if cmd == "delete":
        return cmd_preset_delete(args)
    if cmd == "apply":
        return cmd_preset_apply(args)
    if cmd == "check":
        return cmd_preset_check(args)
    fail("invalid_args", "preset requires save|list|show|delete|apply|check")


def cmd_settings_get(args):
    return execute_command(SETTINGS_GET_COMMAND)


def cmd_settings_set(args):
    return execute_command(SETTINGS_SET_COMMAND, vars(args))


def cmd_settings(args):
    if args.settings_command == "get":
        return cmd_settings_get(args)
    if args.settings_command == "set":
        return cmd_settings_set(args)
    fail("invalid_args", "settings requires get or set")


def soft_preset_apply(name, also_stop_off=False):
    return preset_apply_for_launch(name, also_stop_off=also_stop_off)


def maybe_auto_apply_on_launch():
    return domain_maybe_auto_apply_on_launch()


def cmd_schedule(args):
    focus = getattr(args, "focus", None)

    def read(registry):
        return build_schedule_windows(registry, focus=focus), False, []

    emit(mutate_registry_meta(read))


def cmd_environment_check(args):
    report = domain_compatibility_result({
        "presets": list(getattr(args, "name", None) or []),
        "file": getattr(args, "file", None),
        "include_default_on": bool(getattr(args, "include_default_on", False)),
    })
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)
    return


def cmd_environment(args):
    if args.env_command == "export":
        return cmd_environment_export(args)
    if args.env_command == "import":
        return cmd_environment_import(args)
    if args.env_command == "check":
        return cmd_environment_check(args)
    fail("invalid_args", "environment requires export|import|check")


def cmd_compat(args):
    """compat check — port-conflict report for selected presets/environments."""
    names = []
    if getattr(args, "preset", None):
        names.extend(list(args.preset))
    if getattr(args, "name", None):
        names.extend(list(args.name))
    presets_csv = getattr(args, "presets", None)
    if isinstance(presets_csv, str) and presets_csv.strip():
        names.extend([p.strip() for p in presets_csv.split(",") if p.strip()])
    file_path = getattr(args, "file", None)
    include_default_on = bool(getattr(args, "include_default_on", False))

    report = domain_compatibility_result({
        "presets": names,
        "file": file_path,
        "include_default_on": include_default_on,
    })
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


def cmd_set_tailnet(args):
    """Update recorded tailnet mode; apply/tear down Serve when range is active."""
    return set_tailnet_result({
        "mode": args.mode,
        "range_id": args.range_id,
        "project": args.project,
        "environment": getattr(args, "environment", None),
    })


def cmd_tailscale_serve_portskill(args):
    """Serve (or stop serving) the Portskill listen port via Tailscale Serve — never Funnel."""
    return serve_portskill_result({
        "state": getattr(args, "state", None),
        "no_persist": bool(getattr(args, "no_persist", False)),
    })


def cmd_tailscale_status(args):
    return tailscale_status_result()


def cmd_tailscale_login(args):
    """Browser Login flow; exit 3 with a needs-input payload when login is incomplete."""
    return tailscale_login_result({
        "no_open": bool(getattr(args, "no_open", False)),
        "wait": getattr(args, "wait", None),
    })


def cmd_tailscale_logout(args):
    """Log out of Tailscale (CLI wrapper around `tailscale logout`)."""
    return tailscale_logout_result()


def cmd_workspace_draft_set(args):
    emit(workspace_draft_set_result(vars(args)))


def cmd_workspace_draft_save(args):
    emit(workspace_draft_save_result(vars(args)))


def cmd_workspace_draft_revert(args):
    emit(workspace_draft_revert_result(vars(args)))


def cmd_workspace_edit_mode(args):
    emit(workspace_edit_mode_result(vars(args)))


def cmd_workspace(args):
    cmd = getattr(args, "workspace_command", None)
    if cmd == "draft-set":
        return cmd_workspace_draft_set(args)
    if cmd == "draft-save":
        return cmd_workspace_draft_save(args)
    if cmd == "draft-revert":
        return cmd_workspace_draft_revert(args)
    if cmd == "edit-mode":
        return cmd_workspace_edit_mode(args)
    if cmd == "capture-defaults":
        return cmd_set_defaults_from_current(args)
    fail("invalid_args", f"unknown workspace command: {cmd}")


def cmd_http_auth(args):
    emit(http_auth_result({
        "command": getattr(args, "http_auth_command", None),
        "gate_mode": getattr(args, "gate_mode", None),
        "passkeys_action": getattr(args, "passkeys_action", None),
        "credential_id": getattr(args, "credential_id", None),
    }))


def cmd_tailscale(args):
    cmd = getattr(args, "tailscale_command", None)
    if cmd == "status":
        return cmd_tailscale_status(args)
    if cmd == "login":
        return cmd_tailscale_login(args)
    if cmd == "logout":
        return cmd_tailscale_logout(args)
    if args.tailscale_command == "serve-portskill":
        return cmd_tailscale_serve_portskill(args)
    fail("invalid_args", f"unknown tailscale command: {cmd}")


def cmd_doctor(args):
    return execute_command(DOCTOR_COMMAND, vars(args))


def cmd_handoff_fetch(args):
    emit(fetch_kit_result({"ref": getattr(args, "ref", None)}))


def cmd_history_list(args):
    emit(history_list_result({"environment": getattr(args, "environment", None)}))


def cmd_history_restore(args):
    emit(history_restore_result({
        "environment": getattr(args, "environment", None),
        "index": args.index,
    }))


def cmd_history_reset(args):
    emit(history_reset_result({"environment": getattr(args, "environment", None)}))


def cmd_history_clear(args):
    emit(history_clear_result({"environment": getattr(args, "environment", None)}))


def cmd_history(args):
    cmd = args.history_command
    if cmd == "list":
        return cmd_history_list(args)
    if cmd == "restore":
        return cmd_history_restore(args)
    if cmd == "reset":
        return cmd_history_reset(args)
    if cmd == "clear":
        return cmd_history_clear(args)
    fail("invalid_args", "history requires list|restore|reset|clear")


def cmd_machine(args):
    """Public machine CLI held — Coming soon (no mutate / no writes)."""
    print("Remote machines — Coming soon", file=__import__("sys").stderr)
    raise SystemExit(2)


def status_project_from_context(args):
    return project_from_context(args.project)


def cmd_path(args):
    """CLI mirror of MCP portskill."""
    return execute_command(PATH_COMMAND, vars(args))


def cmd_status(args):
    return execute_command(STATUS_COMMAND, vars(args))


def cmd_discover_ports(args):
    """Poll local listeners + Tailscale Serve; diff vs non-released registry ranges."""
    return discover_ports_result()


def cmd_discover_import(args):
    """Claim exact listening/Serve ports into the workspace via allocate --start N."""
    return discover_import_result({
        "port": getattr(args, "port", None),
        "all_missing": bool(getattr(args, "all_missing", False)),
        "tailnet": getattr(args, "tailnet", None),
        "note": getattr(args, "note", None),
        "project": getattr(args, "project", None),
    })


def cmd_discover(args):
    sub = getattr(args, "discover_command", None)
    if sub == "ports":
        return cmd_discover_ports(args)
    if sub == "import":
        return cmd_discover_import(args)
    fail("invalid_args", "discover requires ports|import")


def _add_spec_cli_parser(subparsers, spec: CommandSpec, *, handler):
    binding = spec.cli
    if binding is None or not binding.path:
        raise ValueError(f"command has no CLI path: {spec.id}")
    group_cache = getattr(subparsers, "_portskill_spec_groups", None)
    if group_cache is None:
        group_cache = {}
        setattr(subparsers, "_portskill_spec_groups", group_cache)
    command_subparsers = subparsers
    for depth, parent_name in enumerate(binding.path[:-1]):
        prefix = binding.path[: depth + 1]
        cached = group_cache.get(prefix)
        if cached is not None:
            command_subparsers = cached
            continue
        parent_parser = command_subparsers.add_parser(parent_name)
        destination = binding.parent_destinations[depth]
        required = binding.parent_required[depth]
        command_subparsers = parent_parser.add_subparsers(
            dest=destination, required=required
        )
        group_cache[prefix] = command_subparsers
    parser_kwargs = {}
    if binding.aliases:
        parser_kwargs["aliases"] = list(binding.aliases)
    if binding.help is not MISSING:
        parser_kwargs["help"] = binding.help
    if binding.description is not MISSING:
        parser_kwargs["description"] = binding.description
    command_parser = command_subparsers.add_parser(
        binding.path[-1], **parser_kwargs
    )
    ordered_parameters = sorted(
        enumerate(spec.params),
        key=lambda item: (
            item[0]
            if item[1].cli is None or item[1].cli.order is MISSING
            else item[1].cli.order
        ),
    )
    for _, parameter in ordered_parameters:
        cli_binding = parameter.cli
        if cli_binding is None:
            continue
        if cli_binding.positional:
            argument_names = (parameter.key,)
        else:
            argument_names = cli_binding.flags
        argument_kwargs = {}
        for key in (
            "dest", "action", "required", "default", "nargs", "help", "metavar"
        ):
            value = getattr(cli_binding, key)
            if value is not MISSING:
                argument_kwargs[key] = value
        if cli_binding.type_name is not MISSING:
            argument_kwargs["type"] = {
                "string": str,
                "integer": int,
                "number": float,
            }[cli_binding.type_name]
        if cli_binding.choices is not MISSING:
            argument_kwargs["choices"] = list(cli_binding.choices)
        command_parser.add_argument(*argument_names, **argument_kwargs)
    command_parser.set_defaults(func=handler)
    return command_parser


def parser():
    root = JsonArgumentParser(prog="portskill-cli")
    subparsers = root.add_subparsers(dest="command", required=True)

    _add_spec_cli_parser(subparsers, ALLOCATE_COMMAND, handler=cmd_allocate)
    _add_spec_cli_parser(subparsers, ACTIVATE_COMMAND, handler=cmd_activate)
    _add_spec_cli_parser(subparsers, RELEASE_COMMAND, handler=cmd_release)

    _add_spec_cli_parser(subparsers, START_COMMAND, handler=cmd_start)
    _add_spec_cli_parser(subparsers, STOP_COMMAND, handler=cmd_stop)

    _add_spec_cli_parser(subparsers, STATUS_COMMAND, handler=cmd_status)

    _add_spec_cli_parser(subparsers, PATH_COMMAND, handler=cmd_path)

    _add_spec_cli_parser(subparsers, DOCTOR_COMMAND, handler=cmd_doctor)

    handoff = subparsers.add_parser("handoff", help="Experimental Session Handoff kit management")
    handoff_sub = handoff.add_subparsers(dest="handoff_command", required=True)
    handoff_fetch = handoff_sub.add_parser(
        "fetch", help="Download the Session Handoff kit release into the Portskill config dir")
    handoff_fetch.add_argument("--ref", default=None,
                               help="Kit git ref (tag/branch/sha); default is the pinned release")
    handoff_fetch.set_defaults(func=cmd_handoff_fetch)

    http_auth = subparsers.add_parser(
        "http-auth",
        help="Optional helper: bearer token + opt-in passkey gate (default off)",
    )
    http_auth_sub = http_auth.add_subparsers(dest="http_auth_command", required=True)
    http_auth_show = http_auth_sub.add_parser(
        "show",
        help="Print the local HTTP bearer token (mints one if missing)",
    )
    http_auth_show.set_defaults(func=cmd_http_auth)
    http_auth_regen = http_auth_sub.add_parser(
        "regenerate",
        help="Mint a new HTTP bearer token and invalidate the old one",
    )
    http_auth_regen.set_defaults(func=cmd_http_auth)
    http_auth_gate = http_auth_sub.add_parser(
        "gate",
        help="Show or set the opt-in passkey HTTP gate (default off)",
    )
    http_auth_gate.add_argument(
        "gate_mode",
        nargs="?",
        choices=["on", "off", "show"],
        default="show",
        help="on|off|show (default show). Off keeps post-#14 open listen.",
    )
    http_auth_gate.set_defaults(func=cmd_http_auth)
    http_auth_keys = http_auth_sub.add_parser(
        "passkeys",
        help="List or delete local operator passkeys (not stored in registry.json)",
    )
    http_auth_keys.add_argument(
        "passkeys_action",
        nargs="?",
        choices=["list", "delete"],
        default="list",
    )
    http_auth_keys.add_argument("--id", dest="credential_id", default=None)
    http_auth_keys.set_defaults(func=cmd_http_auth)

    _add_spec_cli_parser(subparsers, SET_DEFAULT_COMMAND, handler=cmd_set_default)
    _add_spec_cli_parser(subparsers, SET_DEFAULTS_FROM_CURRENT_COMMAND, handler=cmd_set_defaults_from_current)
    _add_spec_cli_parser(subparsers, APPLY_DEFAULTS_COMMAND, handler=cmd_apply_defaults)
    _add_spec_cli_parser(subparsers, DEACTIVATE_COMMAND, handler=cmd_deactivate)

    schedule = subparsers.add_parser(
        "schedule",
        aliases=["busy"],
        help="(Inert) session history windows — prefer compat check for port conflicts",
    )
    schedule.add_argument(
        "--focus",
        default=None,
        help="Optional preset name to filter windows",
    )
    schedule.set_defaults(func=cmd_schedule)

    compat = subparsers.add_parser(
        "compat",
        help="Check whether selected environments can share ports safely",
    )
    compat_sub = compat.add_subparsers(dest="compat_command")
    compat_check = compat_sub.add_parser("check", help="Report port conflicts (default)")
    for p in (compat, compat_check):
        p.add_argument("--preset", action="append", default=None, help="Preset name (repeatable)")
        p.add_argument("--name", action="append", default=None, help="Alias of --preset")
        p.add_argument("--presets", default=None, help="Comma-separated preset names")
        p.add_argument("--file", default=None, help="Environment JSON file")
        p.add_argument(
            "--include-default-on",
            action="store_true",
            help="Include live Default On ranges",
        )
        p.set_defaults(func=cmd_compat)

    environment = subparsers.add_parser("environment")
    env_sub = environment.add_subparsers(dest="env_command", required=True)

    env_export = env_sub.add_parser("export")
    env_export.add_argument("--name", required=True)
    env_export.add_argument("--out", default=None)
    env_export.add_argument("--description", default="")
    env_export.add_argument("--project", action="append", default=None)
    env_export.add_argument(
        "--from-preset",
        default=None,
        help="Export services from a named in-registry preset instead of live ranges",
    )
    env_export.set_defaults(func=cmd_environment)

    env_import = env_sub.add_parser("import")
    env_import.add_argument("--file", required=True)
    env_import.add_argument(
        "--apply-defaults",
        action="store_true",
        help="After import, start services marked default_state=on",
    )
    env_import.add_argument("--environment", default=None, help="History env scope")
    env_import.set_defaults(func=cmd_environment)

    env_check = env_sub.add_parser("check")
    env_check.add_argument("--name", action="append", default=None, help="Preset name(s) to check")
    env_check.add_argument("--file", default=None, help="Environment JSON file to check")
    env_check.add_argument(
        "--include-default-on",
        action="store_true",
        help="Include live Default On ranges in the check",
    )
    env_check.set_defaults(func=cmd_environment)

    preset = subparsers.add_parser("preset")
    preset_sub = preset.add_subparsers(dest="preset_command", required=True)

    preset_save = preset_sub.add_parser("save")
    preset_save.add_argument("--name", required=True)
    preset_save.add_argument("--description", default="")
    preset_save.add_argument("--project", action="append", default=None)
    preset_save.add_argument(
        "--from-file",
        default=None,
        help="Load services from an environment JSON file instead of live ranges",
    )
    preset_save.add_argument("--empty", action="store_true", help="Create an empty environment (no services)")
    preset_save.add_argument("--environment", default=None, help="History env scope")
    preset_save.set_defaults(func=cmd_preset)

    preset_list = preset_sub.add_parser("list")
    preset_list.set_defaults(func=cmd_preset)

    preset_show = preset_sub.add_parser("show")
    preset_show.add_argument("--name", required=True)
    preset_show.set_defaults(func=cmd_preset)

    preset_delete = preset_sub.add_parser("delete")
    preset_delete.add_argument("--name", required=True)
    preset_delete.set_defaults(func=cmd_preset)

    preset_apply = preset_sub.add_parser("apply")
    preset_apply.add_argument("--name", required=True)
    preset_apply.add_argument(
        "--also-stop-off",
        action="store_true",
        help="Also stop running services whose default_state is off",
    )
    preset_apply.set_defaults(func=cmd_preset)

    preset_check = preset_sub.add_parser("check")
    preset_check.add_argument(
        "--name",
        action="append",
        required=True,
        help="Preset name (repeatable) to check for port conflicts",
    )
    preset_check.set_defaults(func=cmd_preset)

    _add_spec_cli_parser(subparsers, SETTINGS_GET_COMMAND, handler=cmd_settings)
    _add_spec_cli_parser(subparsers, SETTINGS_SET_COMMAND, handler=cmd_settings)

    set_tailnet = subparsers.add_parser(
        "set-tailnet",
        help="Set recorded Tailscale mode (serve|none|funnel); apply when range is active",
    )
    set_tailnet.add_argument("--range-id", required=True)
    set_tailnet.add_argument("--mode", required=True, choices=["serve", "none", "funnel"])
    set_tailnet.add_argument("--project", default=None)
    set_tailnet.add_argument("--environment", default=None, help="History env scope")
    set_tailnet.set_defaults(func=cmd_set_tailnet)

    tailscale = subparsers.add_parser(
        "tailscale",
        help="Tailscale helpers (status JSON, login). Funnel remains CLI --mode funnel.",
    )
    ts_sub = tailscale.add_subparsers(dest="tailscale_command", required=True)
    ts_status = ts_sub.add_parser("status", help="Wrap tailscale status --json")
    ts_status.set_defaults(func=cmd_tailscale)
    ts_login = ts_sub.add_parser(
        "login",
        help="Browser Login: open Tailscale auth URL and poll until Connected",
    )
    ts_login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the system browser (still returns auth_url)",
    )
    ts_login.add_argument(
        "--wait",
        type=int,
        default=90,
        help="Seconds to poll status --json for BackendState Running (default 90)",
    )
    ts_login.set_defaults(func=cmd_tailscale)
    ts_logout = ts_sub.add_parser(
        "logout",
        help="Log out of Tailscale (tailscale logout)",
    )
    ts_logout.set_defaults(func=cmd_tailscale)
    ts_serve_ps = ts_sub.add_parser(
        "serve-portskill",
        help=(
            "Tailscale Serve the Portskill listen port from listen.json (never Funnel). "
            "New installs default off."
        ),
    )
    ts_serve_ps.add_argument(
        "--state",
        choices=["on", "off", "status"],
        default="status",
        help="on=configure Serve, off=teardown, status=report (default)",
    )
    ts_serve_ps.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write settings.serve_portskill_on_tailscale",
    )
    ts_serve_ps.set_defaults(func=cmd_tailscale)

    workspace = subparsers.add_parser(
        "workspace",
        help="Workspace draft edit-mode (Save/Revert metadata)",
    )
    ws_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    ws_set = ws_sub.add_parser("draft-set", help="Stage edit-mode fields for a range")
    ws_set.add_argument("--range-id", required=True)
    ws_set.add_argument("--start-script", default=None)
    ws_set.add_argument("--stop-script", default=None)
    ws_set.add_argument("--command", default=None)
    ws_set.add_argument("--cwd", default=None)
    ws_set.set_defaults(func=cmd_workspace)
    ws_save = ws_sub.add_parser("draft-save", help="Persist draft metadata into registry")
    ws_save.add_argument("--range-id", default=None, help="Apply only this range's draft (default: all)")
    ws_save.add_argument("--keep-edit-mode", action="store_true")
    ws_save.set_defaults(func=cmd_workspace)
    ws_revert = ws_sub.add_parser("draft-revert", help="Discard draft; restore last saved")
    ws_revert.add_argument("--range-id", default=None, help="Clear only this range's draft (default: all)")
    ws_revert.add_argument("--keep-edit-mode", action="store_true")
    ws_revert.set_defaults(func=cmd_workspace)
    ws_edit = ws_sub.add_parser("edit-mode", help="Enter/leave edit mode")
    ws_edit.add_argument("--state", choices=["on", "off"], default="on")
    ws_edit.set_defaults(func=cmd_workspace)
    ws_cap = ws_sub.add_parser(
        "capture-defaults",
        help="Alias: set default_state from current active/inactive states",
    )
    ws_cap.add_argument("--environment", default=None, help="History env scope")
    ws_cap.set_defaults(func=cmd_workspace)

    history = subparsers.add_parser(
        "history",
        help="Photoshop-style environment undo stack (per focused env)",
    )
    history_sub = history.add_subparsers(dest="history_command", required=True)

    history_list = history_sub.add_parser("list", help="List Original + steps for an environment")
    history_list.add_argument("--environment", default=None, help="Env name (default: focused or __all__)")
    history_list.set_defaults(func=cmd_history)

    history_restore = history_sub.add_parser("restore", help="Restore snapshot at index (0=Original)")
    history_restore.add_argument("--index", type=int, required=True)
    history_restore.add_argument("--environment", default=None)
    history_restore.set_defaults(func=cmd_history)

    history_reset = history_sub.add_parser("reset", help="Restore Original (index 0)")
    history_reset.add_argument("--environment", default=None)
    history_reset.set_defaults(func=cmd_history)

    history_clear = history_sub.add_parser("clear", help="Drop stack; keep live state; re-baseline")
    history_clear.add_argument("--environment", default=None)
    history_clear.set_defaults(func=cmd_history)


    discover = subparsers.add_parser(
        "discover",
        help="Poll occupied local ports + Tailscale Serve; import missing into workspace",
    )
    discover_sub = discover.add_subparsers(dest="discover_command", required=True)
    disc_ports = discover_sub.add_parser(
        "ports",
        help="JSON: listeners + serve mappings + missing vs registry",
    )
    disc_ports.set_defaults(func=cmd_discover)
    disc_import = discover_sub.add_parser(
        "import",
        help="Claim exact port(s) via allocate --start N (never silent 20xxx realloc)",
    )
    disc_import.add_argument("--port", type=int, default=None, help="Exact start port to claim")
    disc_import.add_argument(
        "--all-missing",
        action="store_true",
        help="Import every missing discovery (use with care)",
    )
    disc_import.add_argument("--note", default=None)
    disc_import.add_argument(
        "--tailnet",
        choices=["serve", "none"],
        default=None,
        help="serve|none (default: serve if already Serving, else none). Never Funnel.",
    )
    disc_import.add_argument("--project", default=None, help="Project path (default: guess or .)")
    disc_import.set_defaults(func=cmd_discover)

    machine = subparsers.add_parser(
        "machine",
        help="Remote machines — Coming soon",
    )
    machine.add_argument(
        "machine_args",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )
    machine.set_defaults(func=cmd_machine)

    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = args.func(args)
        if isinstance(result, dict):
            emit(result)
        return 0
    except argparse.ArgumentError as exc:
        error = DomainError("invalid_args", str(exc))
    except DomainError as exc:
        error = exc
    emit(error.as_dict())
    return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
