"""Stale-range sweep: report by default, release only on explicit opt-in.

Stale means saved state ``active``, local, no tracked process alive, and no
listener on any port of the range (a bind test, see ``drift.range_drift``). A
range whose process died but whose ports are still held is drift, not stale:
it is listed under ``skipped`` and never released, because freeing it would let
the port be reallocated under a live listener. Reserved and released ranges
are never touched. There is no age threshold: a dead process is stale however
recent.

Release re-checks every candidate under the registry lock. ``range_ids``
narrows a release to the ranges the caller reviewed: a confirmed range that is
no longer stale, or a stale range that was not confirmed, is reported under
``skipped`` (``skip_reason`` ``no_longer_stale`` / ``not_confirmed``) instead
of being freed. Without ``range_ids``, release frees every currently stale
range.
"""
from __future__ import annotations

import pathlib
from typing import Any

from .domain import DomainError
from .lifecycle import park_item_for_exit
from .registry import CorruptRegistryError, mutate_registry
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


def _scan(registry: dict, project: str | None) -> tuple[list, list]:
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
            # Bind test only: no connects while the registry lock is held.
            drift = range_drift(item)
            if not drift:
                continue
            triple = (proj, item, _row(proj, item, drift))
            (stale if drift.get("stale") else skipped).append(triple)
    return stale, skipped


def _apply_confirmation(stale: list, skipped: list, confirmed: set, payload: dict) -> list:
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
            payload["skipped"].append({"range_id": range_id, "skip_reason": "no_longer_stale"})
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


def sweep_result(options: dict) -> dict[str, Any]:
    raw_project = options.get("project")
    project = project_path(raw_project) if raw_project else None
    release = bool(options.get("release"))
    confirmed = _confirmed_ids(options.get("range_ids"))

    def mutate(registry: dict):
        stale, skipped = _scan(registry, project)
        payload: dict[str, Any] = {
            "status": "ok",
            "dry_run": not release,
            "stale": [row for _, _, row in stale],
            "skipped": [row for _, _, row in skipped],
            "released": [],
            "failed": [],
        }
        if release and confirmed is not None:
            stale = _apply_confirmation(stale, skipped, confirmed, payload)
        if not release or not stale:
            if stale and not release:
                payload["hint"] = (
                    "Run sweep again with release (and range_ids set to the ranges you reviewed) to free them."
                )
            return payload, False, []
        # Candidates were recomputed under the registry lock (no TOCTOU with
        # an earlier report); each goes through the normal exit teardown.
        history = resolve_history_env_name(registry, options.get("environment"))
        ensure_baseline(registry, history)
        changed = []
        for proj, item, row in stale:
            ok, detail = park_item_for_exit(item, proj, also_release=True)
            if ok:
                payload["released"].append(row)
            else:
                payload["failed"].append({**row, "reason": detail.get("reason"), "message": detail.get("message")})
            changed.append(proj)
        maybe_push_history(registry, "sweep", history)
        if payload["failed"]:
            payload["warnings"] = [
                {"reason": f["reason"], "message": f["message"]} for f in payload["failed"]
            ]
        return payload, True, sorted(set(changed))

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
