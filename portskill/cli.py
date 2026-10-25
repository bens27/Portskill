#!/usr/bin/env python3
from __future__ import annotations
import argparse
from . import __version__
from .auth import (
    ensure_http_auth_token,
    http_auth_public_status,
    http_auth_token,
    http_bearer_matches,
    regenerate_http_auth_token,
)
from .apply_defaults import apply_defaults_result as domain_apply_defaults_result
from .compatibility import (
    compat_report as domain_compat_report,
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
    ENVIRONMENT_CHECK_COMMAND,
    ENVIRONMENT_EXPORT_COMMAND,
    ENVIRONMENT_IMPORT_COMMAND,
    HISTORY_CLEAR_COMMAND,
    HISTORY_LIST_COMMAND,
    HISTORY_RESET_COMMAND,
    HISTORY_RESTORE_COMMAND,
    HANDOFF_FETCH_COMMAND,
    HTTP_AUTH_GATE_COMMAND,
    HTTP_AUTH_PASSKEYS_COMMAND,
    HTTP_AUTH_REGENERATE_COMMAND,
    HTTP_AUTH_SHOW_COMMAND,
    MACHINE_COMMAND,
    MISSING,
    PATH_COMMAND,
    PORTS_DISCOVER_COMMAND,
    PORTS_IMPORT_COMMAND,
    PRESET_APPLY_COMMAND,
    PRESET_CHECK_COMMAND,
    PRESET_DELETE_COMMAND,
    PRESET_LIST_COMMAND,
    PRESET_SAVE_COMMAND,
    PRESET_SHOW_COMMAND,
    COMPAT_COMMAND,
    RELEASE_COMMAND,
    SCHEDULE_COMMAND,
    SET_DEFAULT_COMMAND,
    SET_DEFAULTS_FROM_CURRENT_COMMAND,
    SETTINGS_GET_COMMAND,
    SETTINGS_SET_COMMAND,
    SET_TAILNET_COMMAND,
    START_COMMAND,
    STATUS_COMMAND,
    STOP_COMMAND,
    SWEEP_COMMAND,
    TAILSCALE_LOGIN_COMMAND,
    TAILSCALE_LOGOUT_COMMAND,
    TAILSCALE_SERVE_PORTSKILL_COMMAND,
    TAILSCALE_STATUS_COMMAND,
    WORKSPACE_DRAFT_REVERT_COMMAND,
    WORKSPACE_DRAFT_SAVE_COMMAND,
    WORKSPACE_DRAFT_SET_COMMAND,
    WORKSPACE_EDIT_MODE_COMMAND,
)
from .command_runtime import execute_command
from .defaults import set_default_result, set_defaults_from_current_result  # noqa: F401 - compatibility reexports
from .deactivate import deactivate_result, item_matches_preset_service
from .http_cookies import HTTP_AUTH_COOKIE_NAME, HTTP_SESSION_COOKIE_NAME
from .history import clear_history_stack, history_list_payload, reset_history, restore_history
from .schedule import build_schedule_windows  # compatibility reexport
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
    payload = execute_command(ENVIRONMENT_EXPORT_COMMAND, vars(args))
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
    emit(execute_command(ENVIRONMENT_IMPORT_COMMAND, vars(args)))
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
    emit(execute_command(PRESET_SAVE_COMMAND, vars(args)))
    return


def cmd_preset_list(args):
    emit(execute_command(PRESET_LIST_COMMAND, vars(args)))
    return


def cmd_preset_show(args):
    emit(execute_command(PRESET_SHOW_COMMAND, vars(args)))
    return


def cmd_preset_delete(args):
    emit(execute_command(PRESET_DELETE_COMMAND, vars(args)))
    return


def run_preset_apply(name, also_stop_off=False):
    return preset_apply_result({"name": name, "also_stop_off": also_stop_off})


def cmd_preset_apply(args):
    emit(execute_command(PRESET_APPLY_COMMAND, vars(args)))


def cmd_preset_check(args):
    report = execute_command(PRESET_CHECK_COMMAND, vars(args))
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
    emit(execute_command(SCHEDULE_COMMAND, vars(args)))


def cmd_environment_check(args):
    report = execute_command(ENVIRONMENT_CHECK_COMMAND, vars(args))
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
    for key in ("preset", "name"):
        value = getattr(args, key, None)
        if value:
            names.extend(value if isinstance(value, list) else [value])
    presets_csv = getattr(args, "presets", None)
    if isinstance(presets_csv, str) and presets_csv.strip():
        names.extend(
            part.strip() for part in presets_csv.split(",") if part.strip()
        )
    report = execute_command(COMPAT_COMMAND, {
        "presets": names,
        "file": getattr(args, "file", None),
        "include_default_on": bool(getattr(args, "include_default_on", False)),
    })
    emit(report)
    if not report.get("compatible"):
        raise SystemExit(2)


def cmd_set_tailnet(args):
    """Update recorded tailnet mode; apply/tear down Serve when range is active."""
    return execute_command(SET_TAILNET_COMMAND, vars(args))


def cmd_tailscale_serve_portskill(args):
    """Serve (or stop serving) the Portskill listen port via Tailscale Serve — never Funnel."""
    return execute_command(TAILSCALE_SERVE_PORTSKILL_COMMAND, vars(args))


def cmd_tailscale_status(args):
    return execute_command(TAILSCALE_STATUS_COMMAND, vars(args))


def cmd_tailscale_login(args):
    """Browser Login flow; exit 3 with a needs-input payload when login is incomplete."""
    return execute_command(TAILSCALE_LOGIN_COMMAND, vars(args))


def cmd_tailscale_logout(args):
    """Log out of Tailscale (CLI wrapper around `tailscale logout`)."""
    return execute_command(TAILSCALE_LOGOUT_COMMAND, vars(args))


def cmd_workspace_draft_set(args):
    emit(execute_command(WORKSPACE_DRAFT_SET_COMMAND, vars(args)))


def cmd_workspace_draft_save(args):
    emit(execute_command(WORKSPACE_DRAFT_SAVE_COMMAND, vars(args)))


def cmd_workspace_draft_revert(args):
    emit(execute_command(WORKSPACE_DRAFT_REVERT_COMMAND, vars(args)))


def cmd_workspace_edit_mode(args):
    emit(execute_command(WORKSPACE_EDIT_MODE_COMMAND, vars(args)))


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
    spec = {
        "show": HTTP_AUTH_SHOW_COMMAND,
        "regenerate": HTTP_AUTH_REGENERATE_COMMAND,
        "gate": HTTP_AUTH_GATE_COMMAND,
        "passkeys": HTTP_AUTH_PASSKEYS_COMMAND,
    }[args.http_auth_command]
    emit(execute_command(spec, vars(args)))


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
    emit(execute_command(HANDOFF_FETCH_COMMAND, vars(args)))


def cmd_history_list(args):
    emit(execute_command(HISTORY_LIST_COMMAND, vars(args)))


def cmd_history_restore(args):
    emit(execute_command(HISTORY_RESTORE_COMMAND, vars(args)))


def cmd_history_reset(args):
    emit(execute_command(HISTORY_RESET_COMMAND, vars(args)))


def cmd_history_clear(args):
    emit(execute_command(HISTORY_CLEAR_COMMAND, vars(args)))


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
    result = execute_command(MACHINE_COMMAND, vars(args))
    print(result["message"], file=__import__("sys").stderr)
    raise SystemExit(2)


def status_project_from_context(args):
    return project_from_context(args.project)


def cmd_path(args):
    """CLI mirror of MCP portskill."""
    return execute_command(PATH_COMMAND, vars(args))


def cmd_status(args):
    return execute_command(STATUS_COMMAND, vars(args))


def cmd_sweep(args):
    """Report stale ranges; release them only with --release."""
    return execute_command(SWEEP_COMMAND, vars(args))


def cmd_discover_ports(args):
    """Poll local listeners + Tailscale Serve; diff vs non-released registry ranges."""
    return execute_command(PORTS_DISCOVER_COMMAND, vars(args))


def cmd_discover_import(args):
    """Claim exact listening/Serve ports into the workspace via allocate --start N."""
    return execute_command(PORTS_IMPORT_COMMAND, vars(args))


def cmd_discover(args):
    sub = getattr(args, "discover_command", None)
    if sub == "ports":
        return cmd_discover_ports(args)
    if sub == "import":
        return cmd_discover_import(args)
    fail("invalid_args", "discover requires ports|import")


def _add_spec_cli_arguments(command_parser, spec: CommandSpec):
    """Add a command spec's declared arguments to an existing parser."""
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
                if key == "help" and value == "==SUPPRESS==":
                    value = argparse.SUPPRESS
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
    return command_parser


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
        parent_kwargs = {}
        if depth < len(binding.parent_helps):
            parent_help = binding.parent_helps[depth]
            if parent_help is not MISSING:
                parent_kwargs["help"] = parent_help
        parent_parser = command_subparsers.add_parser(parent_name, **parent_kwargs)
        destination = binding.parent_destinations[depth]
        required = binding.parent_required[depth]
        command_subparsers = parent_parser.add_subparsers(
            dest=destination, required=required
        )
        if binding.parent_runs_command:
            _add_spec_cli_arguments(parent_parser, spec)
            parent_parser.set_defaults(func=handler)
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
    _add_spec_cli_arguments(command_parser, spec)
    command_parser.set_defaults(func=handler)
    return command_parser


def _add_spec_cli_alternate_parsers(subparsers, spec: CommandSpec, *, handler):
    """Register a spec's alternate CLI paths under groups that already exist."""
    group_cache = getattr(subparsers, "_portskill_spec_groups", {})
    for alternate in spec.cli.alternate_paths:
        group_subparsers = (
            subparsers if len(alternate.path) == 1 else group_cache[alternate.path[:-1]]
        )
        parser_kwargs = {}
        if alternate.help is not MISSING:
            parser_kwargs["help"] = alternate.help
        alternate_parser = group_subparsers.add_parser(
            alternate.path[-1], **parser_kwargs
        )
        _add_spec_cli_arguments(alternate_parser, spec)
        alternate_parser.set_defaults(func=handler)


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

    _add_spec_cli_parser(subparsers, SWEEP_COMMAND, handler=cmd_sweep)

    _add_spec_cli_parser(subparsers, HANDOFF_FETCH_COMMAND, handler=cmd_handoff_fetch)
    _add_spec_cli_parser(subparsers, HTTP_AUTH_SHOW_COMMAND, handler=cmd_http_auth)
    _add_spec_cli_parser(subparsers, HTTP_AUTH_REGENERATE_COMMAND, handler=cmd_http_auth)
    _add_spec_cli_parser(subparsers, HTTP_AUTH_GATE_COMMAND, handler=cmd_http_auth)
    _add_spec_cli_parser(subparsers, HTTP_AUTH_PASSKEYS_COMMAND, handler=cmd_http_auth)

    _add_spec_cli_parser(subparsers, SET_DEFAULT_COMMAND, handler=cmd_set_default)
    _add_spec_cli_parser(subparsers, SET_DEFAULTS_FROM_CURRENT_COMMAND, handler=cmd_set_defaults_from_current)
    _add_spec_cli_parser(subparsers, APPLY_DEFAULTS_COMMAND, handler=cmd_apply_defaults)
    _add_spec_cli_parser(subparsers, DEACTIVATE_COMMAND, handler=cmd_deactivate)

    _add_spec_cli_parser(subparsers, SCHEDULE_COMMAND, handler=cmd_schedule)

    _add_spec_cli_parser(subparsers, COMPAT_COMMAND, handler=cmd_compat)

    _add_spec_cli_parser(subparsers, ENVIRONMENT_EXPORT_COMMAND, handler=cmd_environment)
    _add_spec_cli_parser(subparsers, ENVIRONMENT_IMPORT_COMMAND, handler=cmd_environment)
    _add_spec_cli_parser(subparsers, ENVIRONMENT_CHECK_COMMAND, handler=cmd_environment)

    _add_spec_cli_parser(subparsers, PRESET_SAVE_COMMAND, handler=cmd_preset)
    _add_spec_cli_parser(subparsers, PRESET_LIST_COMMAND, handler=cmd_preset)
    _add_spec_cli_parser(subparsers, PRESET_SHOW_COMMAND, handler=cmd_preset)
    _add_spec_cli_parser(subparsers, PRESET_DELETE_COMMAND, handler=cmd_preset)
    _add_spec_cli_parser(subparsers, PRESET_APPLY_COMMAND, handler=cmd_preset)
    _add_spec_cli_parser(subparsers, PRESET_CHECK_COMMAND, handler=cmd_preset)

    _add_spec_cli_parser(subparsers, SETTINGS_GET_COMMAND, handler=cmd_settings)
    _add_spec_cli_parser(subparsers, SETTINGS_SET_COMMAND, handler=cmd_settings)

    _add_spec_cli_parser(subparsers, SET_TAILNET_COMMAND, handler=cmd_set_tailnet)
    _add_spec_cli_parser(subparsers, TAILSCALE_STATUS_COMMAND, handler=cmd_tailscale)
    _add_spec_cli_parser(subparsers, TAILSCALE_LOGIN_COMMAND, handler=cmd_tailscale)
    _add_spec_cli_parser(subparsers, TAILSCALE_LOGOUT_COMMAND, handler=cmd_tailscale)
    _add_spec_cli_parser(
        subparsers, TAILSCALE_SERVE_PORTSKILL_COMMAND, handler=cmd_tailscale
    )

    _add_spec_cli_parser(subparsers, WORKSPACE_DRAFT_SET_COMMAND, handler=cmd_workspace)
    _add_spec_cli_parser(subparsers, WORKSPACE_DRAFT_SAVE_COMMAND, handler=cmd_workspace)
    _add_spec_cli_parser(subparsers, WORKSPACE_DRAFT_REVERT_COMMAND, handler=cmd_workspace)
    _add_spec_cli_parser(subparsers, WORKSPACE_EDIT_MODE_COMMAND, handler=cmd_workspace)
    _add_spec_cli_alternate_parsers(
        subparsers, SET_DEFAULTS_FROM_CURRENT_COMMAND, handler=cmd_workspace
    )

    _add_spec_cli_parser(subparsers, HISTORY_LIST_COMMAND, handler=cmd_history)
    _add_spec_cli_parser(subparsers, HISTORY_RESTORE_COMMAND, handler=cmd_history)
    _add_spec_cli_parser(subparsers, HISTORY_RESET_COMMAND, handler=cmd_history)
    _add_spec_cli_parser(subparsers, HISTORY_CLEAR_COMMAND, handler=cmd_history)


    _add_spec_cli_parser(subparsers, PORTS_DISCOVER_COMMAND, handler=cmd_discover)
    _add_spec_cli_parser(subparsers, PORTS_IMPORT_COMMAND, handler=cmd_discover)

    _add_spec_cli_parser(subparsers, MACHINE_COMMAND, handler=cmd_machine)

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
