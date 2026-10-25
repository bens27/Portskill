"""portskill — skip-aware happy-path orchestrator (MCP + CLI).

MCP tools/list name is portskill (compat alias portskill_path still dispatches).
CLI remains path --mode … (aliases portskill-path / portskill_path).
Modes: start|stop|release|restart|status. activate is an internal primitive.
Skip predicates are deterministic Python rules. Remotes remain HOLD.
Never Funnel Portskill's listen/UI/MCP port. Lean vs full mcp_tools profiles live in settings.
Stop mode honors settings.stop_also_release (default true). Restart's stop phase
keeps the range reserved so allocate can reuse it.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

from .domain import DomainError
from .lifecycle import (
    RESTORE_PENDING_KEY,
    activate_item,
    park_item_for_exit,
    teardown_applied_tailnet,
    range_has_live_process,
    read_listen_port_from_disk,
    refuse_remote_process,
    release_item,
    resolve_stop_also_release,
    scaffold_lifecycle_scripts,
)
from .registry import normalize_settings
from .registry_domain import (
    DEFAULT_START_SCRIPT,
    DEFAULT_STOP_SCRIPT,
    is_remote_range,
    normalize_default_state,
    normalize_machines,
    normalize_optional_str,
    project_path,
)
from .service import script_is_placeholder
from .teardown_marker import refuse_teardown_in_progress, teardown_marker
from .status import enrich_range as enrich_range_for_status, project_from_context
from .tailscale import probe_tailscale_status
from .registry_operations import (
    allocate_range,
    ensure_baseline,
    find_matching_unreleased,
    find_project_range,
    locked_registry,
    maybe_push_history,
    require_project_directory,
    resolve_history_env_name,
)

PATH_MODES = ("start", "stop", "release", "restart", "status")

PHASES_BY_MODE = {
    "start": ("allocate", "wire", "activate", "start", "tailnet"),
    "stop": ("stop",),
    "release": ("release",),
    "restart": ("stop", "allocate", "wire", "activate", "start", "tailnet"),
    "status": ("status",),
}

# Stable skip reasons (inspectable; tests assert these strings).
SKIP_ALLOCATE_RESERVED = "already_reserved"
SKIP_WIRE_ALREADY = "already_wired"
SKIP_ACTIVATE_ACTIVE = "already_active"
SKIP_START_RUNNING = "already_running"
SKIP_START_PLACEHOLDER = "start_script_placeholder"
SKIP_STOP_NOT_RUNNING = "not_running"
SKIP_RELEASE_ALREADY = "already_released"
SKIP_TAILNET_NOT_REQUESTED = "not_requested"
SKIP_TAILNET_NOT_LOGGED_IN = "not_logged_in"
SKIP_TAILNET_FUNNEL_LISTEN = "funnel_listen"
SKIP_TAILNET_ALREADY = "already_served"


@dataclass(frozen=True)
class PathSkipFacts:
    """Snapshot used by skip predicates. No I/O — callers assemble this."""

    has_unreleased_range: bool
    range_state: str | None
    scripts_exist: bool
    wire_pending: bool
    live_process: bool
    start_ready: bool
    tailnet_requested: bool
    tailnet_logged_in: bool
    tailnet_already_configured: bool
    funnel_listen: bool


def tailnet_is_requested(mode: str | None) -> bool:
    if not isinstance(mode, str):
        return False
    return mode.strip().lower() in ("serve", "funnel")


def skip_reason(phase: str, facts: PathSkipFacts) -> str | None:
    """Return a skip reason or None if the phase should run. Deterministic."""
    if phase == "allocate":
        return SKIP_ALLOCATE_RESERVED if facts.has_unreleased_range else None
    if phase == "wire":
        if facts.scripts_exist and not facts.wire_pending:
            return SKIP_WIRE_ALREADY
        return None
    if phase == "activate":
        if facts.range_state == "active":
            return SKIP_ACTIVATE_ACTIVE
        return None
    if phase == "start":
        if facts.live_process:
            return SKIP_START_RUNNING
        if not facts.start_ready:
            return SKIP_START_PLACEHOLDER
        return None
    if phase == "tailnet":
        # Never Funnel Portskill listen — even when explicitly requested.
        if facts.funnel_listen:
            return SKIP_TAILNET_FUNNEL_LISTEN
        if not facts.tailnet_requested:
            return SKIP_TAILNET_NOT_REQUESTED
        if not facts.tailnet_logged_in:
            return SKIP_TAILNET_NOT_LOGGED_IN
        if facts.tailnet_already_configured:
            return SKIP_TAILNET_ALREADY
        return None
    if phase == "stop":
        if not facts.live_process and facts.range_state != "active":
            return SKIP_STOP_NOT_RUNNING
        return None
    if phase == "release":
        if (not facts.has_unreleased_range) or facts.range_state == "released":
            return SKIP_RELEASE_ALREADY
        return None
    if phase == "status":
        return None
    return None


def needs_input_for_tailnet_skip(reason: str | None, facts: PathSkipFacts) -> bool:
    """Resume hook: requested Serve/Funnel but Tailscale is not logged in."""
    return reason == SKIP_TAILNET_NOT_LOGGED_IN and facts.tailnet_requested


def _phase_entry(phase: str, *, skipped: str | None = None, detail: Any = None) -> dict:
    entry: dict[str, Any] = {"phase": phase}
    if skipped:
        entry["reason"] = skipped
    if detail is not None:
        entry["detail"] = detail
    return entry


def _tailnet_needs_input(ran: list, skipped: list, result: dict) -> dict:
    return {
        "status": "needs_input",
        "input": "tailscale_login",
        "needs_input": True,
        "reason": "tailscale_auth_required",
        "prompt": (
            "Tailscale Serve requires login. Complete Browser Login "
            "(or `portskill-cli tailscale login`), then resume portskill "
            "with the same mode/tailnet."
        ),
        "options": ["retry_after_login", "resume_tailnet_none"],
        "resume_hint": "tailscale login",
        "ran": ran,
        "skipped": skipped,
        "result": result,
    }


# RESTORE_PENDING_KEY (lifecycle) marks a mapping that a stop without release
# took down, or that a restart could not re-apply, so the next restart (or the
# advertised resume) still brings it back.


def _recorded_tailnet(item: dict | None) -> dict | None:
    """Serve/Funnel mapping a restart must bring back, or None.

    Only a mapping that was actually configured counts, or one a stop without
    release or a failed restart left pending: a mode recorded at allocate time
    but never applied (login skipped, then declined) stays off.
    """
    if not isinstance(item, dict) or item.get("state") == "released":
        return None
    tailnet = item.get("tailnet")
    if not isinstance(tailnet, dict):
        return None
    if not tailnet.get("configured_at") and not tailnet.get(RESTORE_PENDING_KEY):
        return None
    mode = tailnet.get("mode")
    if not tailnet_is_requested(mode):
        return None
    return {
        "mode": mode.strip().lower(),
        "port": tailnet.get("port") or item.get("start"),
        "previous_configured_at": tailnet.get("configured_at"),
    }


def _set_restore_pending(item: dict, pending: bool) -> bool:
    """Set or clear the pending-restore marker; True when the item changed."""
    tailnet = item.get("tailnet")
    if not isinstance(tailnet, dict):
        return False
    if pending:
        if tailnet.get(RESTORE_PENDING_KEY) is True:
            return False
        tailnet[RESTORE_PENDING_KEY] = True
        return True
    return tailnet.pop(RESTORE_PENDING_KEY, None) is not None


def _tailnet_restore_needs_input(
    ran: list,
    skipped: list,
    result: dict,
    restore: dict,
    tailscale: dict,
    failure: dict | None = None,
) -> dict:
    """Restart could not re-apply the recorded mapping: ask, never drop it."""
    mode = restore["mode"]
    port = restore.get("port")
    if failure is not None:
        reason, needed = "tailnet_restore_failed", "tailnet_restore"
        why = failure.get("message") or failure.get("reason") or "tailscale command failed"
        fix = "Fix Tailscale"
    elif tailscale.get("state") == "binary_missing":
        reason, needed = "tailscale_bin_missing", "tailscale_install"
        why = "Tailscale binary not found"
        fix = "Install Tailscale or set PORTSKILL_TAILSCALE_BIN"
    else:
        reason, needed = "tailscale_auth_required", "tailscale_login"
        why = "Tailscale is not logged in"
        fix = "Complete Browser Login (or `portskill-cli tailscale login`)"
    payload = {
        "status": "needs_input",
        "input": needed,
        "needs_input": True,
        "reason": reason,
        "prompt": (
            f"Restart kept the recorded Tailscale {mode} mapping for port {port} "
            f"but could not re-apply it ({why}). {fix}, then restart again "
            f"(or resume with `--tailnet {mode}`) to re-apply it, "
            "or resume with `--tailnet none` to leave it off."
        ),
        "options": [mode, "none"],
        "resume_hint": "--tailnet",
        "tailnet_restore": {k: v for k, v in restore.items() if k != "source"},
        "ran": ran,
        "skipped": skipped,
        "result": result,
    }
    if failure is not None:
        payload["error"] = failure
    return payload


def _scripts_exist(project: str) -> bool:
    root = pathlib.Path(project)
    return (root / DEFAULT_START_SCRIPT).is_file() and (root / DEFAULT_STOP_SCRIPT).is_file()


def _start_ready(item: dict | None, project: str) -> bool:
    if item is not None and normalize_optional_str(item.get("command")):
        return True
    path = pathlib.Path(project) / DEFAULT_START_SCRIPT
    if not path.is_file():
        return False
    return not script_is_placeholder(path)


def _wire_pending(item: dict | None, command: str | None, cwd: str | None, default_state: str | None) -> bool:
    if item is None:
        return bool(command or cwd or default_state)
    if command and normalize_optional_str(command) != normalize_optional_str(item.get("command")):
        return True
    if cwd and normalize_optional_str(cwd) != normalize_optional_str(item.get("cwd")):
        return True
    if default_state is not None:
        wanted = normalize_default_state(default_state)
        if wanted != normalize_default_state(item.get("default_state")):
            return True
    return False


def _select_item(registry: dict, project: str, range_id: str | None, note: str | None):
    if range_id:
        item = find_project_range(registry, project, range_id)
        if item is not None and item.get("state") != "released":
            return item
        return None
    if note:
        item = find_matching_unreleased(registry, project, note)
        if item is not None:
            return item
    entry = registry.get("projects", {}).get(project) or {}
    for item in entry.get("ranges") or []:
        if item.get("state") != "released":
            return item
    return None


def _listen_port() -> int | None:
    return read_listen_port_from_disk()


def _facts_from_state(
    *,
    item: dict | None,
    project: str,
    requested_tailnet: str | None,
    command: str | None,
    cwd: str | None,
    default_state: str | None,
    logged_in: bool,
    listen_port: int | None,
) -> PathSkipFacts:
    state = item.get("state") if isinstance(item, dict) else None
    has = bool(item is not None and state != "released")
    live = bool(item is not None and range_has_live_process(item))
    tn = (item.get("tailnet") or {}) if isinstance(item, dict) else {}
    recorded = tn.get("mode") if isinstance(tn, dict) else None
    configured = bool(isinstance(tn, dict) and tn.get("configured_at") and recorded)
    req = (requested_tailnet or "").strip().lower() if isinstance(requested_tailnet, str) else ""
    already = bool(configured and req in ("serve", "funnel") and recorded == req)
    start_port = item.get("start") if isinstance(item, dict) else None
    funnel_listen = False
    if req == "funnel" and start_port is not None and listen_port is not None:
        try:
            funnel_listen = int(start_port) == int(listen_port)
        except (TypeError, ValueError):
            funnel_listen = False
    return PathSkipFacts(
        has_unreleased_range=has,
        range_state=state if isinstance(state, str) else None,
        scripts_exist=_scripts_exist(project),
        wire_pending=_wire_pending(item, command, cwd, default_state),
        live_process=live,
        start_ready=_start_ready(item, project),
        tailnet_requested=tailnet_is_requested(requested_tailnet),
        tailnet_logged_in=bool(logged_in),
        tailnet_already_configured=already,
        funnel_listen=funnel_listen,
    )


def _apply_wire(item, project, command, cwd, default_state):
    scaffold_lifecycle_scripts(project)
    changed = []
    if command is not None:
        item["command"] = normalize_optional_str(command)
        changed.append("command")
    if cwd is not None:
        item["cwd"] = normalize_optional_str(cwd)
        changed.append("cwd")
    if default_state is not None:
        item["default_state"] = normalize_default_state(default_state)
        changed.append("default_state")
    return changed


def _launch_start(item, project):
    from .service import launch_service

    outcome = launch_service(item, project)
    if not outcome.ok:
        detail = outcome.error_detail()
        raise DomainError(detail.pop("reason"), detail.pop("message", None), **detail)
    return {
        "source": outcome.source,
        "pid": outcome.pid,
        "pgid": outcome.pgid,
    }


def run_path(
    *,
    mode: str,
    project: str,
    count: int = 1,
    range_id: str | None = None,
    note: str | None = None,
    tailnet: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
    default_state: str | None = None,
    start: int | None = None,
    environment: str | None = None,
) -> dict:
    """Execute path mode. Returns payload; caller emits / maps exit codes."""
    from .lifecycle import mark_item_active

    mode = (mode or "").strip().lower()
    if mode not in PATH_MODES:
        raise DomainError("invalid_args", f"path mode must be one of {', '.join(PATH_MODES)}")

    project = project_path(project or ".")
    if mode != "status":
        require_project_directory(project)

    ts = probe_tailscale_status()
    logged_in = bool(ts.get("logged_in"))
    listen_port = _listen_port()
    requested = (tailnet or "").strip().lower() if isinstance(tailnet, str) and tailnet.strip() else None
    if requested == "":
        requested = None

    ran: list[dict] = []
    skipped: list[dict] = []
    needs_input = False
    restore_tailnet: dict | None = None
    restore_failure: dict | None = None
    stop_failure: dict | None = None
    path_warnings: list[dict] = []

    def mutate(registry):
        nonlocal needs_input, requested, restore_tailnet, restore_failure, stop_failure
        hist_key = resolve_history_env_name(registry, environment)
        ensure_baseline(registry, hist_key)
        changed: list[str] = []
        item = _select_item(registry, project, range_id, note) if mode != "status" else None
        if item is not None and is_remote_range(registry, item):
            refuse_remote_process(registry, item, action=f"path {mode}")
        # Explicit tailnet wins. Restart then re-applies the range's configured
        # (or pending) Serve/Funnel mapping, which its stop phase tears down;
        # otherwise start/restart follow settings.default_tailnet. An explicit
        # value equal to the recorded mode is the same restore (the resume).
        if mode == "restart":
            recorded = _recorded_tailnet(item)
            if recorded is not None and requested in (None, recorded["mode"]):
                restore_tailnet = recorded
                if requested is None:
                    restore_tailnet["source"] = "recorded"
                requested = recorded["mode"]
        if requested is None and mode in ("start", "restart"):
            if normalize_settings(registry.get("settings")).get("default_tailnet") == "serve":
                requested = "serve"

        result: dict[str, Any] = {"range": item, "project": project, "mode": mode}

        if mode == "status":
            selected = project_from_context(project)
            if selected is None:
                ranges = []
                for entry in registry["projects"].values():
                    ranges.extend(entry.get("ranges", []))
                projects = registry["projects"]
            else:
                ranges = registry["projects"].get(selected, {}).get("ranges", [])
                projects = {selected: registry["projects"][selected]} if selected in registry["projects"] else {}
            try:
                ts_self = ts.get("Self")
            except Exception:
                ts_self = None
            enriched = [enrich_range_for_status(registry, r, tailscale_self=ts_self) for r in ranges]
            ran.append(_phase_entry("status", detail="ok"))
            result = {
                "status": "ok",
                "ranges": enriched,
                "pool": registry["pool"],
                "projects": projects,
                "machines": normalize_machines(registry.get("machines")),
            }
            return {
                "status": "ok",
                "mode": mode,
                "ran": ran,
                "skipped": skipped,
                "result": result,
            }, []

        for phase in PHASES_BY_MODE[mode]:
            item = _select_item(registry, project, range_id or (item.get("id") if item else None), note)
            facts = _facts_from_state(
                item=item,
                project=project,
                requested_tailnet=requested,
                command=command,
                cwd=cwd,
                default_state=default_state,
                logged_in=logged_in,
                listen_port=listen_port,
            )
            reason = skip_reason(phase, facts)
            if reason:
                skipped.append(_phase_entry(phase, skipped=reason))
                if phase == "tailnet" and needs_input_for_tailnet_skip(reason, facts):
                    needs_input = True
                    if restore_tailnet is not None and item is not None:
                        _set_restore_pending(item, True)
                        changed.append(project)
                elif phase == "tailnet" and reason == SKIP_TAILNET_ALREADY and item is not None:
                    # Still applied (e.g. the stop phase's `off` failed): nothing to restore.
                    # Not while sweep is removing that mapping: the parked range must
                    # keep restore_pending until sweep's second write settles it.
                    if teardown_marker(item) is None and _set_restore_pending(item, False):
                        changed.append(project)
                continue

            if phase == "allocate":
                item, warnings = allocate_range(
                    registry,
                    project,
                    count or 1,
                    note,
                    requested or "none",
                    start_port=start,
                )
                changed.append(project)
                detail = {"range_id": item.get("id"), "start": item.get("start"), "end": item.get("end")}
                if warnings:
                    detail["warnings"] = warnings
                ran.append(_phase_entry(phase, detail=detail))
            elif phase == "wire":
                if item is None:
                    raise DomainError("not_found", "path wire needs an allocated range")
                applied = _apply_wire(item, project, command, cwd, default_state)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"applied": applied}))
            elif phase == "activate":
                if item is None:
                    raise DomainError("not_found", "path activate needs an allocated range")
                refuse_teardown_in_progress(item)
                mark_item_active(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"range_id": item.get("id")}))
            elif phase == "start":
                if item is None:
                    raise DomainError("not_found", "path start needs an allocated range")
                if item.get("state") == "released":
                    raise DomainError("range_released", "released ranges cannot be started; allocate a fresh range")
                launched = _launch_start(item, project)
                if item.get("state") != "active":
                    mark_item_active(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail=launched))
            elif phase == "tailnet":
                if item is None:
                    raise DomainError("not_found", "path tailnet needs an allocated range")
                applied = requested or "none"
                if restore_tailnet is None:
                    activate_item(item, project, item.get("id"), applied)
                    _set_restore_pending(item, False)
                    changed.append(project)
                    ran.append(_phase_entry(phase, detail={"mode": applied, "port": item.get("start")}))
                    continue
                # The service is already back up: a failed re-apply must not
                # roll the registry back, and the mapping stays pending.
                try:
                    activate_item(item, project, item.get("id"), applied)
                except DomainError as exc:
                    restore_failure = exc.as_dict()
                    needs_input = True
                    _set_restore_pending(item, True)
                    changed.append(project)
                    skipped.append(_phase_entry(phase, skipped="restore_failed"))
                    continue
                _set_restore_pending(item, False)
                changed.append(project)
                detail = {"mode": applied, "port": item.get("start")}
                if restore_tailnet.get("source"):
                    detail["source"] = restore_tailnet["source"]
                ran.append(_phase_entry(phase, detail=detail))
            elif phase == "stop":
                if item is None:
                    skipped.append(_phase_entry(phase, skipped=SKIP_STOP_NOT_RUNNING))
                    continue
                # Restart must keep the range so allocate can skip and reuse it.
                # Mode stop honors settings.stop_also_release (default true).
                also = False if mode == "restart" else resolve_stop_also_release(registry)
                ok, detail = park_item_for_exit(item, project, also_release=also)
                if not ok and "range" not in detail:
                    raise DomainError(detail.get("reason") or "stop_failed", detail.get("message"), **{
                        k: v for k, v in detail.items() if k not in ("reason", "message")
                    })
                changed.append(project)
                if ok:
                    ran.append(_phase_entry(phase, detail=detail))
                    continue
                # The process is gone: commit the parked range instead of
                # rolling back to a dead pid. Restart carries on (its tailnet
                # phase re-applies or asks); stop reports the error.
                stop_failure = {k: v for k, v in detail.items() if k != "range"}
                ran.append(_phase_entry(phase, detail={
                    k: detail[k] for k in ("action", "reason", "range_id") if k in detail
                }))
                if mode != "restart":
                    break
            elif phase == "release":
                if item is None:
                    skipped.append(_phase_entry(phase, skipped=SKIP_RELEASE_ALREADY))
                    continue
                if range_has_live_process(item):
                    raise DomainError(
                        "process_still_running",
                        "range still has a live pid/pgid; use path --mode stop (not release)",
                    )
                release_item(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"range_id": item.get("id")}))

        # Declining the mapping (after the phases: a restart's stop phase marks
        # it pending again) drops a pending restore and takes down a mapping
        # a failed teardown left applied, so no later restart re-applies it.
        if requested == "none" and item is not None and item.get("state") != "released":
            if _set_restore_pending(item, False):
                changed.append(project)
            tailnet_record = item.get("tailnet")
            if isinstance(tailnet_record, dict) and tailnet_record.get("configured_at"):
                failure = teardown_applied_tailnet(item)
                if failure is None:
                    tailnet_record["mode"] = "none"
                    tailnet_record["port"] = None
                    changed.append(project)
                else:
                    path_warnings.append({
                        "reason": "tailnet_teardown_failed",
                        "message": (
                            f"Could not remove the Tailscale {tailnet_record.get('mode')} mapping "
                            f"for port {tailnet_record.get('port') or item.get('start')}; it stays "
                            "recorded. Fix Tailscale, then run again with `--tailnet none`."
                        ),
                        "range_id": item.get("id"),
                        "error": failure,
                    })
        if changed:
            maybe_push_history(registry, f"path-{mode}", hist_key)
        rid = range_id or (item.get("id") if isinstance(item, dict) else None)
        if rid:
            found = find_project_range(registry, project, rid)
            if found is not None:
                item = found
        else:
            item = _select_item(registry, project, None, note)
        result = {"range": item, "project": project, "mode": mode}
        payload = {
            "status": "needs_input" if needs_input else "ok",
            "mode": mode,
            "ran": ran,
            "skipped": skipped,
            "result": result,
        }
        if needs_input and restore_tailnet is not None:
            payload = _tailnet_restore_needs_input(
                ran, skipped, result, restore_tailnet, ts, restore_failure,
            )
        elif needs_input:
            payload = _tailnet_needs_input(ran, skipped, result)
        if stop_failure is not None:
            if mode == "restart":
                path_warnings.insert(0, stop_failure)
            else:
                payload = {
                    **stop_failure,
                    "status": "error",
                    "mode": mode,
                    "ran": ran,
                    "skipped": skipped,
                    "result": result,
                }
        if path_warnings:
            payload.setdefault("warnings", []).extend(path_warnings)
        return payload, sorted(set(changed))

    return locked_registry(mutate)


def cmd_path_from_args(args) -> dict:
    payload = run_path(
        mode=getattr(args, "mode", None),
        project=getattr(args, "project", None) or ".",
        count=int(getattr(args, "count", 1) or 1),
        range_id=getattr(args, "range_id", None),
        note=getattr(args, "note", None),
        tailnet=getattr(args, "tailnet", None),
        command=getattr(args, "command", None),
        cwd=getattr(args, "cwd", None),
        default_state=getattr(args, "default_state", None),
        start=getattr(args, "start", None),
        environment=getattr(args, "environment", None),
    )
    if payload.get("needs_input") or payload.get("status") == "needs_input":
        raise DomainError("needs_input", exit_code=3, payload=payload)
    if payload.get("status") == "error":
        # Raised after the lock: the registry change is already committed.
        raise DomainError(payload.get("reason") or "path_failed", payload.get("message"), payload=payload)
    return payload
