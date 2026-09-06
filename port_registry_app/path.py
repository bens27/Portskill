"""portskill_path — skip-aware happy-path orchestrator (MCP + CLI).

Cut-0: mode start|stop|release|restart|status. Fine primitives stay callable.
Skip predicates are deterministic Python rules. Remotes remain HOLD.
Never Funnel Portskill's listen/UI/MCP port. Lean vs full mcp_tools profiles live in settings.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

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
            "(or `portskill-cli tailscale login`), then resume portskill_path "
            "with the same mode/tailnet."
        ),
        "options": ["retry_after_login", "resume_tailnet_none"],
        "resume_hint": "tailscale login",
        "ran": ran,
        "skipped": skipped,
        "result": result,
    }


def _scripts_exist(project: str) -> bool:
    from .cli import DEFAULT_START_SCRIPT, DEFAULT_STOP_SCRIPT

    root = pathlib.Path(project)
    return (root / DEFAULT_START_SCRIPT).is_file() and (root / DEFAULT_STOP_SCRIPT).is_file()


def _start_ready(item: dict | None, project: str) -> bool:
    from .cli import (
        DEFAULT_START_SCRIPT,
        normalize_optional_str,
        script_is_placeholder,
    )

    if item is not None and normalize_optional_str(item.get("command")):
        return True
    path = pathlib.Path(project) / DEFAULT_START_SCRIPT
    if not path.is_file():
        return False
    return not script_is_placeholder(path)


def _wire_pending(item: dict | None, command: str | None, cwd: str | None, default_state: str | None) -> bool:
    from .cli import normalize_default_state, normalize_optional_str

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
    from .cli import find_matching_unreleased, find_project_range

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
    from .cli import read_listen_port_from_disk

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
    from .cli import range_has_live_process

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


def _allocate_range(registry, project, count, note, start_port, tailnet_mode):
    from .cli import (
        ensure_project,
        fail,
        find_free_range,
        next_range_id,
        non_released_ranges,
        normalize_settings,
        overlaps,
        range_record,
        scaffold_lifecycle_scripts,
        warnings_for_port_range,
    )

    scaffold_lifecycle_scripts(project)
    count = int(count)
    if start_port is not None:
        start = int(start_port)
        end = start + count - 1
        pool = registry["pool"]
        if start < pool["start"] or end > pool["end"]:
            pool["start"] = min(int(pool["start"]), start)
            pool["end"] = max(int(pool["end"]), end)
            registry["pool"] = pool
        for other in non_released_ranges(registry):
            if overlaps(start, end, other["start"], other["end"]):
                fail(
                    "port_in_use",
                    f"ports {start}-{end} overlap existing range {other.get('id')} "
                    f"({other['start']}-{other['end']})",
                )
    else:
        available = find_free_range(registry, count)
        if available is None:
            fail("pool_exhausted")
        start, end = available
    project_entry = ensure_project(registry, project)
    item = range_record(
        next_range_id(project_entry),
        start,
        end,
        note,
        tailnet_mode or "none",
    )
    preset_warnings = warnings_for_port_range(
        registry, start, end, exclude_range_id=item.get("id")
    )
    settings = normalize_settings(registry.get("settings"))
    if preset_warnings and settings.get("require_compat"):
        fail(
            "compat_conflict",
            "Ports collide with a saved environment/preset",
            conflicts=[
                {
                    "reason": "preset_port_overlap",
                    "message": w["message"],
                    "preset": w.get("preset"),
                    "ports": w.get("ports"),
                }
                for w in preset_warnings
            ],
            conflict_count=len(preset_warnings),
            hint="Pick free ports, or: settings set --require-compat off",
        )
    project_entry["ranges"].append(item)
    return item, preset_warnings


def _apply_wire(item, project, command, cwd, default_state):
    from .cli import normalize_default_state, normalize_optional_str, scaffold_lifecycle_scripts

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


def _mark_active(item):
    from .cli import utc_now

    item["state"] = "active"
    item["activated_at"] = utc_now()


def _launch_start(item, project):
    from .cli import (
        fail,
        lifecycle,
        normalize_optional_str,
        resolve_project_relative,
        utc_now,
        validate_start_script,
    )
    import os
    import shlex
    import subprocess

    life = lifecycle(item)
    cwd_override = normalize_optional_str(item.get("cwd")) or project
    command = normalize_optional_str(item.get("command"))
    start_log = resolve_project_relative(project, life["start_log"], "start_log")
    start_log.parent.mkdir(parents=True, exist_ok=True)
    with start_log.open("a", encoding="utf-8") as log:
        if command:
            try:
                process = subprocess.Popen(
                    shlex.split(command),
                    cwd=cwd_override,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                fail("start_command_failed", str(exc))
        else:
            start_script = resolve_project_relative(project, life["start_script"], "start_script")
            validate_start_script(start_script)
            try:
                process = subprocess.Popen(
                    [str(start_script)],
                    cwd=cwd_override,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                fail("start_script_failed", str(exc))
    life["pid"] = process.pid
    try:
        life["pgid"] = os.getpgid(process.pid)
    except ProcessLookupError:
        life["pgid"] = process.pid
    life["started_at"] = utc_now()
    life["stopped_at"] = None
    return {"pid": process.pid, "pgid": life["pgid"]}


def _apply_tailnet(item, mode):
    from .cli import funnel_of_listen_port, run_tailnet, utc_now

    tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    port = item["start"]
    if mode == "funnel" and funnel_of_listen_port(port):
        return "skipped_funnel_listen"
    tailnet["mode"] = mode
    if mode in ("serve", "funnel"):
        run_tailnet(mode, port, off=False)
        tailnet["port"] = port
        tailnet["configured_at"] = utc_now()
    else:
        tailnet["port"] = None
        tailnet["configured_at"] = None
    return mode


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
    from .cli import (
        fail,
        is_remote_range,
        locked_registry,
        maybe_push_history,
        park_item_for_exit,
        probe_tailscale_status,
        project_path,
        range_has_live_process,
        refuse_remote_process,
        release_item,
        require_project_directory,
        resolve_history_env_name,
        ensure_baseline,
    )

    mode = (mode or "").strip().lower()
    if mode not in PATH_MODES:
        fail("invalid_args", f"path mode must be one of {', '.join(PATH_MODES)}")

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

    def mutate(registry):
        nonlocal needs_input
        hist_key = resolve_history_env_name(registry, environment)
        ensure_baseline(registry, hist_key)
        changed: list[str] = []
        item = _select_item(registry, project, range_id, note) if mode != "status" else None
        if item is not None and is_remote_range(registry, item):
            refuse_remote_process(registry, item, action=f"path {mode}")

        result: dict[str, Any] = {"range": item, "project": project, "mode": mode}

        if mode == "status":
            from .cli import enrich_range_for_status, normalize_machines, status_project_from_context
            from argparse import Namespace

            selected = status_project_from_context(Namespace(project=project))
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
                continue

            if phase == "allocate":
                item, warnings = _allocate_range(
                    registry, project, count or 1, note, start, requested or "none"
                )
                changed.append(project)
                detail = {"range_id": item.get("id"), "start": item.get("start"), "end": item.get("end")}
                if warnings:
                    detail["warnings"] = warnings
                ran.append(_phase_entry(phase, detail=detail))
            elif phase == "wire":
                if item is None:
                    fail("not_found", "path wire needs an allocated range")
                applied = _apply_wire(item, project, command, cwd, default_state)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"applied": applied}))
            elif phase == "activate":
                if item is None:
                    fail("not_found", "path activate needs an allocated range")
                _mark_active(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"range_id": item.get("id")}))
            elif phase == "start":
                if item is None:
                    fail("not_found", "path start needs an allocated range")
                if item.get("state") == "released":
                    fail("range_released", "released ranges cannot be started; allocate a fresh range")
                launched = _launch_start(item, project)
                if item.get("state") != "active":
                    _mark_active(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail=launched))
            elif phase == "tailnet":
                if item is None:
                    fail("not_found", "path tailnet needs an allocated range")
                applied = _apply_tailnet(item, requested or "none")
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"mode": applied, "port": item.get("start")}))
            elif phase == "stop":
                if item is None:
                    skipped.append(_phase_entry(phase, skipped=SKIP_STOP_NOT_RUNNING))
                    continue
                ok, detail = park_item_for_exit(item, project, also_release=False)
                if not ok:
                    fail(detail.get("reason") or "stop_failed", detail.get("message"), **{
                        k: v for k, v in detail.items() if k not in ("reason", "message")
                    })
                changed.append(project)
                ran.append(_phase_entry(phase, detail=detail))
            elif phase == "release":
                if item is None:
                    skipped.append(_phase_entry(phase, skipped=SKIP_RELEASE_ALREADY))
                    continue
                if range_has_live_process(item):
                    fail(
                        "process_still_running",
                        "range still has a live pid/pgid; use path --mode stop (not release)",
                    )
                release_item(item)
                changed.append(project)
                ran.append(_phase_entry(phase, detail={"range_id": item.get("id")}))

        if changed:
            maybe_push_history(registry, f"path-{mode}", hist_key)
        rid = range_id or (item.get("id") if isinstance(item, dict) else None)
        if rid:
            from .cli import find_project_range

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
        if needs_input:
            payload = _tailnet_needs_input(ran, skipped, result)
        return payload, sorted(set(changed))

    return locked_registry(mutate)


def cmd_path_from_args(args) -> None:
    from .cli import emit

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
    emit(payload)
    if payload.get("needs_input") or payload.get("status") == "needs_input":
        raise SystemExit(3)
