"""Stale-range sweep: report by default, release only on explicit opt-in.

Stale means saved state ``active``, local, no tracked process alive, and no
listener on any port of the range (a bind test, see ``drift.range_drift``). A
range whose process died but whose ports are still held is drift, not stale:
it is listed under ``skipped`` and never released, because freeing it would let
the port be reallocated under a live listener. Reserved and released ranges
are never touched. There is no age threshold: a dead process is stale however
recent. A range whose tracked process is alive is never stale and is not
probed here (the scan runs under the registry lock), so sweep does not list
it; ``status`` still reports its ``port_unbound`` hint.

The ``ps`` process-group probe runs before the lock. Under the lock a range
counts as dead only if its pid is dead and, when it has a pgid, its row is
unchanged since that probe (same state, pid, pgid), the probe found the group
dead and ``killpg(pgid, 0)`` still finds no group. A range restarted or edited
in between counts as alive, so it is never released by that sweep.

Release re-checks every candidate under the registry lock and runs no
subprocess there (no stop script: a stale range has no live process; no
Tailscale). A candidate without an applied Serve/Funnel mapping is released in
that first write. One with a mapping is parked ``reserved`` (mapping recorded,
``restore_pending`` set, so it is never handed out again) with a teardown
marker that makes start/restart refuse it (``teardown_in_progress``; the
marker expires after ``lifecycle.TEARDOWN_MARKER_TTL_SECONDS`` so a crash
cannot wedge it). Each ``off`` command then runs with the lock released, after
a short locked check that the range is still parked (re-stamping the marker
when less than half its TTL is left). A second short locked write releases the
range on success only if it is still parked (same token, ``reserved``,
``restore_pending``, no process); otherwise it leaves it as it is and lists it
under ``failed`` (``tailnet_teardown_failed``, or ``changed_during_teardown``
when someone else changed it meanwhile; a range that changed before its
``off`` is not torn down at all).

``range_ids``
narrows a release to the ranges the caller reviewed: a confirmed range that is
no longer stale, or a stale range that was not confirmed, is reported under
``skipped`` instead of being freed, with a ``skip_reason``:

- ``not_confirmed``: stale, but not in ``range_ids``;
- ``no_longer_stale``: confirmed and in scope, but no longer stale;
- ``unknown_range``: confirmed, but no range has that id (``project`` and
  ``start`` are then null);
- ``not_in_scope``: confirmed, but the range belongs to a project outside the
  ``project`` filter.

Every skip row of an existing range carries its ``project`` and ``start``.
Without ``range_ids``, release frees every currently stale range.
"""
from __future__ import annotations

import os
import pathlib
import time
import uuid
from typing import Any

from .domain import DomainError, utc_now
from . import lifecycle
from .registry import CorruptRegistryError, mutate_registry, read_registry
from .drift import range_drift
from .registry_domain import is_remote_range, normalize_registry, project_path
from .registry_operations import (
    ensure_baseline,
    maybe_push_history,
    pool_bounds,
    resolve_history_env_name,
    write_local_project_files,
)


def _row(project: str, item: dict, drift: dict) -> dict[str, Any]:
    life = item.get("lifecycle") or {}
    return {
        "project": project,
        "range_id": item.get("id"),
        "start": item.get("start"),
        "end": item.get("end"),
        "pid": life.get("pid"),
        "active_since": item.get("activated_at"),
        "reasons": list(drift.get("reasons") or []),
        "hint": drift.get("hint"),
    }


def _liveness_key(item: dict) -> tuple:
    """The fields a start or restart changes: a probe is valid only while they match."""
    life = item.get("lifecycle") or {}
    return item.get("state"), life.get("pid"), life.get("pgid")


def _probe_groups(project: str | None) -> dict[tuple, tuple]:
    """Unlocked: run the ``ps`` group probe for in-scope active ranges with a pgid.

    Maps (project, range id) to (liveness key, group alive). A registry that
    cannot be read yields no probes; the locked scan then reports the error.
    """
    start, end = pool_bounds()
    try:
        registry = read_registry(start=start, end=end, normalizer=normalize_registry)
    except (CorruptRegistryError, OSError, UnicodeError, ValueError):
        return {}
    probes: dict[tuple, tuple] = {}
    for proj, entry in (registry.get("projects") or {}).items():
        if (project is not None and proj != project) or not isinstance(entry, dict):
            continue
        for item in entry.get("ranges") or []:
            if not isinstance(item, dict) or item.get("state") != "active":
                continue
            pgid = (item.get("lifecycle") or {}).get("pgid")
            if pgid is None:
                continue
            alive = any(lifecycle.pid_alive(pid) for pid in lifecycle.process_group_pids(pgid))
            probes[(proj, item.get("id"))] = (_liveness_key(item), alive)
    return probes


def _group_gone(pgid: Any) -> bool:
    """Fresh, subprocess-free check that no process is left in the group."""
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        return False  # unsure (no permission, odd value): count it as alive
    return False


def _tracked_alive(proj: str, item: dict, probes: dict) -> bool:
    """Under the lock: range_has_live_process without running ``ps``.

    A group seen dead before the lock counts only while the row is unchanged
    and the group is still gone; restarted, edited or unprobed counts as alive.
    """
    life = item.get("lifecycle") or {}
    if lifecycle.pid_alive(life.get("pid")):
        return True
    pgid = life.get("pgid")
    if pgid is None:
        return False
    probe = probes.get((proj, item.get("id")))
    if probe is None or probe[0] != _liveness_key(item) or probe[1]:
        return True
    return not _group_gone(pgid)


def _scan(registry: dict, project: str | None, probes: dict) -> tuple[list, list]:
    """Return (stale, skipped) as (project, item, row) triples from a fresh probe."""
    stale, skipped = [], []
    for proj, entry in (registry.get("projects") or {}).items():
        if project is not None and proj != project:
            continue
        if not isinstance(entry, dict):
            continue
        for item in entry.get("ranges") or []:
            if not isinstance(item, dict) or item.get("state") != "active":
                continue
            if is_remote_range(registry, item):
                continue
            # No network I/O under the registry lock: live ranges (never
            # releasable) are not probed, the rest get the bind test only.
            drift = range_drift(item, connect=False, live=_tracked_alive(proj, item, probes))
            if not drift:
                continue
            triple = (proj, item, _row(proj, item, drift))
            (stale if drift.get("stale") else skipped).append(triple)
    return stale, skipped


def _find_range(registry: dict, range_id: str) -> tuple[str, dict] | None:
    for proj, entry in (registry.get("projects") or {}).items():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("ranges") or []:
            if isinstance(item, dict) and item.get("id") == range_id:
                return proj, item
    return None


def _confirmed_skip_row(registry: dict, project: str | None, range_id: str) -> dict[str, Any]:
    """Skip row for a confirmed id that is neither stale nor drifted in scope."""
    found = _find_range(registry, range_id)
    if found is None:
        return {
            "project": None,
            "range_id": range_id,
            "start": None,
            "end": None,
            "reasons": [],
            "skip_reason": "unknown_range",
        }
    proj, item = found
    out_of_scope = project is not None and proj != project
    return {**_row(proj, item, {}), "skip_reason": "not_in_scope" if out_of_scope else "no_longer_stale"}


def _apply_confirmation(
    registry: dict, project: str | None, stale: list, skipped: list, confirmed: set, payload: dict
) -> list:
    """Keep only stale ranges the caller confirmed; report the rest as skipped."""
    kept = []
    for proj, item, row in stale:
        if row["range_id"] in confirmed:
            kept.append((proj, item, row))
        else:
            payload["skipped"].append({**row, "skip_reason": "not_confirmed"})
    stale_ids = {row["range_id"] for _, _, row in stale}
    drifted = {row["range_id"]: row for _, _, row in skipped}
    for range_id in sorted(confirmed - stale_ids):
        if range_id in drifted:
            drifted[range_id]["skip_reason"] = "no_longer_stale"
        else:
            payload["skipped"].append(_confirmed_skip_row(registry, project, range_id))
    return kept


def _write_existing_project_files(registry: dict, changed_projects: list) -> None:
    # A stale range often belongs to a deleted checkout: refresh the local
    # .portskill.json only where the project directory still exists.
    write_local_project_files(
        registry, [p for p in changed_projects if pathlib.Path(p).is_dir()]
    )


def _confirmed_ids(raw: Any) -> set[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not all(isinstance(v, str) and v for v in raw):
        raise DomainError("invalid_range_ids", "range_ids must be a list of range id strings")
    return set(raw)


def _park_for_release(item: dict, project: str, row: dict) -> dict[str, Any] | None:
    """Lock 1: park a stale range for release without running any subprocess.

    A stale range has no live tracked process (re-checked under this lock by
    the scan), so no stop script or kill is needed: only the dead pid is cleared.
    Without an applied Serve/Funnel mapping the range is released at once and
    None is returned. With one, the range is parked reserved (never handed out
    again) with the mapping recorded and restore_pending set, which is the
    state a failed teardown leaves, plus a teardown marker that makes
    start/restart refuse it; the returned job runs `off` after the lock.
    """
    life = lifecycle.lifecycle(item)
    life["pid"] = None
    life["pgid"] = None
    if not life.get("stopped_at"):
        life["stopped_at"] = utc_now()
    tailnet = lifecycle._tailnet_record(item)
    mode = tailnet.get("mode")
    if mode not in ("serve", "funnel") or not tailnet.get("configured_at"):
        lifecycle.release_item(item)  # no mapping applied: teardown is a no-op
        return None
    token = uuid.uuid4().hex
    item["state"] = "reserved"
    tailnet[lifecycle.RESTORE_PENDING_KEY] = True
    tailnet[lifecycle.TEARDOWN_MARKER_KEY] = {"token": token, "at": time.time()}
    return {
        "project": project,
        "range_id": item.get("id"),
        "mode": mode,
        "port": tailnet.get("port") or item.get("start"),
        "token": token,
        "row": row,
    }


def _parked_item(registry: dict, job: dict) -> dict | None:
    """The job's range if it is still exactly as lock 1 parked it, else None.

    The token alone is not enough: once the marker has expired a start may
    have re-activated the range (keeping the marker), so the range must also
    still be reserved, restore_pending, with no tracked process.
    """
    entry = (registry.get("projects") or {}).get(job["project"])
    if not isinstance(entry, dict):
        return None
    item = next(
        (r for r in entry.get("ranges") or [] if isinstance(r, dict) and r.get("id") == job["range_id"]),
        None,
    )
    tailnet = item.get("tailnet") if isinstance(item, dict) else None
    if not isinstance(tailnet, dict):
        return None
    marker = tailnet.get(lifecycle.TEARDOWN_MARKER_KEY)
    life = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
    if (
        not isinstance(marker, dict)
        or marker.get("token") != job["token"]
        or item.get("state") != "reserved"
        or tailnet.get(lifecycle.RESTORE_PENDING_KEY) is not True
        or life.get("pid")
        or life.get("pgid")
    ):
        return None
    return item


def _changed_during_teardown(job: dict) -> dict[str, Any]:
    return {
        "reason": "changed_during_teardown",
        "message": (
            f"Range {job['range_id']} changed while sweep was removing its Tailscale "
            f"{job['mode']} mapping for port {job['port']}; sweep left it as it is now. "
            "Run sweep again to review it."
        ),
    }


# Re-stamp a job's marker before its `off` once less than this much of the TTL
# is left, so the marker outlives the command (run_tailnet times out at 20s)
# however many mapped ranges came before it in this sweep.
_RESTAMP_BELOW_SECONDS = lifecycle.TEARDOWN_MARKER_TTL_SECONDS / 2


def _claim_teardown(registry: dict, job: dict):
    """Short lock before one `off`: confirm the range is still parked and keep its marker fresh."""
    item = _parked_item(registry, job)
    if item is None:
        return False, False, []
    marker = item["tailnet"][lifecycle.TEARDOWN_MARKER_KEY]
    at = marker.get("at")
    fresh = lifecycle.teardown_marker(item) is not None
    left = lifecycle.TEARDOWN_MARKER_TTL_SECONDS - (time.time() - at) if fresh else 0
    if left >= _RESTAMP_BELOW_SECONDS:
        return True, False, []  # read-only: no registry write
    marker["at"] = time.time()
    return True, True, [job["project"]]


def _finish_release(registry: dict, job: dict) -> dict[str, Any] | None:
    """Lock 2: release on a successful teardown; else keep the range parked.

    Returns None when released, else the failure fields for the row (with
    ``_changed`` when the registry was edited).
    """
    if job.get("changed"):
        return _changed_during_teardown(job)
    item = _parked_item(registry, job)
    if item is None:
        return _changed_during_teardown(job)
    tailnet = item["tailnet"]
    tailnet.pop(lifecycle.TEARDOWN_MARKER_KEY, None)
    failure = job.get("failure")
    if failure is None:
        tailnet["configured_at"] = None
        lifecycle.release_item(item)  # configured_at cleared: no second `off`
        return None
    # Same parked shape as a failed teardown in park_item_for_exit: reserved,
    # mapping recorded (it may still be live), restore_pending set, never released.
    why = failure.get("message") or failure.get("reason") or "tailscale command failed"
    return {
        "reason": "tailnet_teardown_failed",
        "message": (
            f"Stopped range {job['range_id']} but could not remove the Tailscale "
            f"{job['mode']} mapping for port {job['port']} ({why}). The range stays reserved "
            "with the mapping recorded; fix Tailscale, then stop or release it again."
        ),
        "error": failure,
        "_changed": True,
    }


def sweep_result(options: dict) -> dict[str, Any]:
    raw_project = options.get("project")
    project = project_path(raw_project) if raw_project else None
    release = bool(options.get("release"))
    confirmed = _confirmed_ids(options.get("range_ids"))

    probes = _probe_groups(project)  # `ps` runs here, before the lock

    def mutate(registry: dict):
        stale, skipped = _scan(registry, project, probes)
        payload: dict[str, Any] = {
            "status": "ok",
            "dry_run": not release,
            "stale": [row for _, _, row in stale],
            "skipped": [row for _, _, row in skipped],
            "released": [],
            "failed": [],
        }
        if release and confirmed is not None:
            stale = _apply_confirmation(registry, project, stale, skipped, confirmed, payload)
        if not release or not stale:
            if stale and not release:
                payload["hint"] = (
                    "Run sweep again with release (and range_ids set to the ranges you reviewed) to free them."
                )
            return payload, False, []
        # Candidates were recomputed under the registry lock (no TOCTOU with
        # an earlier report). Lock 1 decides and marks; no subprocess runs here.
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        changed = []
        for proj, item, row in stale:
            job = _park_for_release(item, proj, row)
            if job is None:
                payload["released"].append(row)
            else:
                pending.append(job)
            changed.append(proj)
        maybe_push_history(registry, "sweep", history)
        return payload, True, sorted(set(changed))

    pending: list[dict[str, Any]] = []
    payload = _locked(mutate)
    if not pending:
        return payload
    # Lock released: run the slow `tailscale ... off` commands, then record
    # their outcome in a second short locked write.
    for job in pending:
        if not _locked(lambda registry, job=job: _claim_teardown(registry, job)):
            job["changed"] = True  # changed since lock 1: do not remove its mapping
            continue
        try:
            job["failure"] = lifecycle.run_tailnet_teardown(job["mode"], job["port"])
        except Exception as exc:  # noqa: BLE001 - recorded, the range stays parked
            job["failure"] = {"reason": "tailnet_command_failed", "message": str(exc)}

    def finish(registry: dict):
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        changed = []
        for job in pending:
            outcome = _finish_release(registry, job)
            if outcome is None:
                payload["released"].append(job["row"])
                changed.append(job["project"])
            else:
                if outcome.pop("_changed", False):
                    changed.append(job["project"])
                payload["failed"].append({**job["row"], **outcome})
        if changed:
            maybe_push_history(registry, "sweep", history)
        return None, bool(changed), sorted(set(changed))

    _locked(finish)
    if payload["failed"]:
        payload["warnings"] = [
            {"reason": f["reason"], "message": f["message"]} for f in payload["failed"]
        ]
    return payload


def _locked(mutate) -> Any:
    start, end = pool_bounds()
    try:
        return mutate_registry(
            mutate,
            start=start,
            end=end,
            normalizer=normalize_registry,
            after_write=_write_existing_project_files,
        )
    except CorruptRegistryError as exc:
        raise DomainError("corrupt_registry", str(exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise DomainError("registry_unavailable", str(exc), exit_code=1) from exc
