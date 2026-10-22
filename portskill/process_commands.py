"""Callable start and stop orchestration shared by all adapters."""
from __future__ import annotations

import errno
import shlex
import socket
from types import SimpleNamespace

from .domain import DomainError, utc_now
from .lifecycle import (
    activate_item, lifecycle, needs_tailnet, park_item_for_exit, pid_alive,
    range_has_live_process, refuse_remote_process, resolve_stop_also_release,
)
from .registry_domain import is_remote_range, normalize_default_state, normalize_range_record, project_path
from .registry_operations import (
    ensure_baseline, find_project_range, locked_registry, maybe_push_history,
    non_released_ranges, overlaps, require_project_directory, resolve_history_env_name,
)
from .service import launch_service

def _also_release_override_from_args(args):
    raw = getattr(args, "also_release", None)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in ("on", "true", "1", "yes"):
        return True
    if token in ("off", "false", "0", "no"):
        return False
    return None


def run_start_all(also_defaults_only=False):
    """Start all non-released local ranges (or only default_state=on)."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, None)
        ensure_baseline(registry, hist_key)
        started = []
        skipped = []
        errors = []
        changed = set()
        for project, item in list(iter_project_ranges(registry, None)):
            if item.get("state") == "released":
                continue
            if also_defaults_only and normalize_default_state(item.get("default_state")) != "on":
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "not_default_on",
                })
                continue
            ok, detail = start_item_for_apply(item, project, registry=registry)
            detail = dict(detail)
            detail["project"] = project
            detail["range_id"] = item.get("id")
            if ok:
                started.append(detail)
                changed.add(project)
            elif detail.get("skipped"):
                skipped.append(detail)
            else:
                errors.append(detail)
                if "range" in detail:
                    changed.add(project)
        maybe_push_history(registry, "start-all" if not also_defaults_only else "start-defaults", hist_key)
        return {
            "status": "ok",
            "started": started,
            "skipped": skipped,
            "errors": errors,
            "started_count": len(started),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "mode": "defaults" if also_defaults_only else "all",
        }, sorted(changed)

    return locked_registry(mutate)


def run_stop_bulk(mode="all", also_release=None):
    """Stop running services. mode: all | non-default. Release follows settings.stop_also_release."""

    def mutate(registry):
        hist_key = resolve_history_env_name(registry, None)
        ensure_baseline(registry, hist_key)
        stopped = []
        skipped = []
        errors = []
        changed = set()
        also = resolve_stop_also_release(registry, override=also_release)
        for project, item in list(iter_project_ranges(registry, None)):
            if mode == "non-default" and normalize_default_state(item.get("default_state")) == "on":
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "default_on",
                })
                continue
            live = range_has_live_process(item)
            is_active = item.get("state") == "active"
            if not live and not is_active:
                skipped.append({
                    "project": project,
                    "range_id": item.get("id"),
                    "reason": "not_running",
                })
                continue
            ok, detail = park_item_for_exit(item, project, also_release=also)
            detail = dict(detail)
            detail["project"] = project
            detail["range_id"] = item.get("id")
            if ok:
                stopped.append(detail)
                changed.add(project)
            else:
                errors.append(detail)
                if "range" in detail:
                    changed.add(project)
        label = "stop-all" if mode == "all" else "stop-non-default"
        maybe_push_history(registry, label, hist_key)
        return {
            "status": "ok",
            "stopped": stopped,
            "skipped": skipped,
            "errors": errors,
            "stopped_count": len(stopped),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "mode": mode,
            "also_release": also,
        }, sorted(changed)

    return locked_registry(mutate)


def reclaim_range(registry, item):
    """Re-reserve the same ports under the registry lock, preserving service wiring."""
    import socket

    for other in non_released_ranges(registry):
        if overlaps(item["start"], item["end"], other["start"], other["end"]):
            raise DomainError("port_in_use", f"These ports are now reserved by {other.get('note') or other.get('id')}. "
                 "Stop and release that service, then retry Reclaim ports & start.")
    for port in range(item["start"], item["end"] + 1):
        for host in ("127.0.0.1", "::1"):
            try:
                connection = socket.create_connection((host, port), timeout=0.15)
            except OSError:
                continue
            connection.close()
            raise DomainError("port_in_use", f"Port {port} is in use. Stop the service using it, then retry Reclaim ports & start.")
        for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if family == socket.AF_INET6:
                        probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    probe.bind((host, port))
            except OSError as exc:
                import errno
                if family == socket.AF_INET6 and exc.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                    continue
                raise DomainError("port_in_use", f"Port {port} is in use. Stop the service using it, then retry Reclaim ports & start.")
    item["state"] = "reserved"
    item["reserved_at"] = utc_now()
    item["released_at"] = None
    life = lifecycle(item)
    life["pid"] = None
    life["pgid"] = None


def iter_project_ranges(registry, project_filter=None):
    """Yield (project, item) for non-released ranges, optionally filtered by project path(s)."""
    filters = None
    if project_filter:
        filters = {project_path(p) for p in project_filter}
    for project, entry in registry["projects"].items():
        if filters is not None and project not in filters:
            continue
        for item in entry.get("ranges", []):
            if item.get("state") == "released":
                continue
            yield project, normalize_range_record(item)


def start_item_for_apply(item, project, registry=None):
    """Start a range for apply-defaults. Returns (ok: bool, detail: dict). Does not emit/exit."""
    if item.get("state") == "released":
        return False, {"reason": "range_released", "range_id": item.get("id")}
    if registry is not None and is_remote_range(registry, item):
        return False, {
            "reason": "remote_machine",
            "range_id": item.get("id"),
            "machine_id": item.get("machine_id") or item.get("machine") or item.get("host_id"),
            "skipped": True,
            "message": "Remote ranges are links only; not started from this Mac",
        }
    life = lifecycle(item)
    if pid_alive(life.get("pid")) or range_has_live_process(item):
        # Already live — ensure active with recorded/none tailnet; no new process
        try:
            mode = (item.get("tailnet") or {}).get("mode") or "none"
            activate_item(item, project, item.get("id"), mode)
        except DomainError:
            return False, {"reason": "activate_failed", "range_id": item.get("id")}
        return True, {"action": "already_live", "range": item}
    if life.get("pid") is not None:
        life["pid"] = None
        life["pgid"] = None
    mode = (item.get("tailnet") or {}).get("mode") or "none"
    outcome = launch_service(item, project)
    if not outcome.ok:
        detail = outcome.error_detail(item.get("id"))
        if detail.get("reason") == "start_script_placeholder":
            detail["skipped"] = True
        return False, detail
    try:
        activate_item(item, project, item.get("id"), mode)
    except DomainError:
        return False, {"reason": "activate_failed", "range_id": item.get("id"), "range": item}
    return True, {"action": "started", "range": item}


def start_result(options: dict) -> dict:
    if bool(options.get("all")):
        return run_start_all(also_defaults_only=False)
    range_id = options.get("range_id")
    if not range_id:
        raise DomainError("invalid_args", "start requires --range-id or --all")
    project = project_path(options.get("project") or ".")
    require_project_directory(project)

    def mutate(registry):
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        item = find_project_range(registry, project, range_id)
        if item is None:
            raise DomainError("not_found", f"range id not found for project: {range_id}")
        if is_remote_range(registry, item):
            refuse_remote_process(registry, item, action="start")
        if item.get("state") == "released":
            if not options.get("reclaim"):
                raise DomainError("range_released", "Stop released this service's ports. Reclaim them to start again.", hint=f"start --project {shlex.quote(project)} --range-id {shlex.quote(range_id)} --reclaim")
            reclaim_range(registry, item)
        life = lifecycle(item)
        if pid_alive(life.get("pid")):
            activate_item(item, project, range_id, options.get("tailnet"))
            maybe_push_history(registry, "start", history)
            return {"status": "ok", "range": item}, [project]
        if life.get("pid") is not None:
            life["pid"] = None
            life["pgid"] = None
        recorded_mode = item.get("tailnet", {}).get("mode")
        if options.get("tailnet") is None and recorded_mode is None:
            raise DomainError("needs_input", exit_code=3, payload=needs_tailnet(project, range_id))
        outcome = launch_service(item, project)
        if not outcome.ok:
            detail = outcome.error_detail()
            raise DomainError(detail.pop("reason"), detail.pop("message", None), **detail)
        activate_item(item, project, range_id, options.get("tailnet"))
        maybe_push_history(registry, "start", history)
        return {"status": "ok", "range": item}, [project]
    return locked_registry(mutate)


def stop_result(options: dict) -> dict:
    args = SimpleNamespace(**options)
    override = _also_release_override_from_args(args)
    if bool(options.get("all")):
        return run_stop_bulk(mode="all", also_release=override)
    if bool(options.get("non_default")):
        return run_stop_bulk(mode="non-default", also_release=override)
    range_id = options.get("range_id")
    if not range_id:
        raise DomainError("invalid_args", "stop requires --range-id, --all, or --non-default")
    project = project_path(options.get("project") or ".")
    require_project_directory(project)
    def mutate(registry):
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        item = find_project_range(registry, project, range_id)
        if item is None:
            raise DomainError("not_found", f"range id not found for project: {range_id}")
        if item.get("state") == "released":
            return {"status": "ok", "range": item, "also_release": resolve_stop_also_release(registry, override=override), "action": "already_released"}, []
        also = resolve_stop_also_release(registry, override=override)
        ok, detail = park_item_for_exit(item, project, also_release=also)
        if not ok:
            raise DomainError(detail.get("reason") or "stop_failed", detail.get("message") or "stop failed", **{k:v for k,v in detail.items() if k not in ("reason","message")})
        maybe_push_history(registry, "stop", history)
        payload={"status":"ok","range":item,"also_release":also}
        if detail.get("action"): payload["action"]=detail["action"]
        return payload,[project]
    return locked_registry(mutate)
