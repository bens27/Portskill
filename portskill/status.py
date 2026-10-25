"""Callable status domain shared by CLI, MCP, and HTTP adapters."""
from __future__ import annotations

import json
import pathlib
import socket
from typing import Any

from .domain import DomainError, pid_alive, pool_bounds_from_environment
from .drift import drift_summary, range_drift
from .registry import CorruptRegistryError, mutate_registry
from .registry_domain import (
    default_machines,
    get_machine,
    is_remote_range,
    normalize_machines,
    normalize_range_machine_fields,
    normalize_registry,
    resolve_range_host,
    resolve_range_url,
)
from .tailscale import probe_tailscale_status


def project_from_context(project: str | None, *, cwd: pathlib.Path | None = None) -> str | None:
    if project is not None:
        return str(pathlib.Path(str(project)).expanduser().resolve())
    current = cwd or pathlib.Path.cwd()
    local_path = current / ".portskill.json"
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            local_project = data.get("project_path")
            if local_project:
                return str(pathlib.Path(local_project).expanduser().resolve())
        except (OSError, json.JSONDecodeError):
            return str(current.expanduser().resolve())
    return None


def observed_range_status(registry: dict, item: dict) -> dict[str, Any]:
    """Keep saved allocation state separate from a current local TCP observation."""
    saved = item.get("state") or "reserved"
    result: dict[str, Any] = {"allocation_state": saved, "state": saved, "reachable": None, "drift": None}
    if saved == "released":
        return result
    if is_remote_range(registry, item):
        result["state"] = "unknown"
        return result
    port = item.get("start")
    reachable = False
    if isinstance(port, int) and 0 < port < 65536:
        for host in ("127.0.0.1", "::1"):
            try:
                with socket.create_connection((host, port), timeout=0.15):
                    reachable = True
                    break
            except OSError:
                pass
    result["reachable"] = reachable
    pid = (item.get("lifecycle") or {}).get("pid")
    result["process_alive"] = pid_alive(pid) if pid else None
    result["state"] = "active" if reachable and result["process_alive"] is not False else "inactive"
    # Drift uses a bind test over the whole range; ``reachable`` stays a display probe.
    result["drift"] = range_drift(item)
    return result


def enrich_range(registry: dict, item: Any, *, tailscale_self: dict | None = None) -> Any:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    normalize_range_machine_fields(out)
    machine_id = out.get("machine_id") or "local"
    machine = get_machine(registry, machine_id) or default_machines()["local"]
    out["machine"] = {
        "id": machine.get("id") or machine_id,
        "label": machine.get("label") or machine_id,
        "host": machine.get("host"),
        "kind": machine.get("kind") or "local",
    }
    out["resolved_host"] = resolve_range_host(registry, out, tailscale_self=tailscale_self)
    url = resolve_range_url(registry, out, tailscale_self=tailscale_self)
    if url:
        out["url"] = url
    out.update(observed_range_status(registry, item))
    return out


def parse_find_query(find: Any) -> dict[str, Any] | None:
    """Normalize a registry search query; ``None`` when no search was asked for.

    Text is a case-insensitive substring matched against the range note and
    its names (project directory name and range id). An ASCII-digit query is
    also a port when it is 1..65535 (kind ``port``): it then matches every range
    whose ``start..end`` contains it, as well as the note and project name. A
    digit query does not match range ids, which are timestamps and would match
    almost every range. Out-of-range digits are a text search only.
    """
    if find is None:
        return None
    if not isinstance(find, str) or not find.strip():
        raise DomainError("invalid_args", "find needs a non-empty note, name or port")
    text = find.strip()
    query: dict[str, Any] = {"text": text, "kind": "text", "needle": text.casefold(), "port": None}
    # isdigit() alone accepts "²", "٣" or "８" (int() fails or reads them as ASCII).
    if text.isascii() and text.isdigit():
        query["digits"] = True
        # Too long to be a port; also keeps int() under its digit-length limit.
        significant = text.lstrip("0")
        if len(significant) <= 5:
            port = int(significant or "0")
            if 0 < port < 65536:
                query["kind"] = "port"
                query["port"] = port
    return query


def range_matches(project_path: str, item: Any, query: dict[str, Any]) -> list[str]:
    """Return which fields of a saved range match ``query`` (empty: no match)."""
    if not isinstance(item, dict):
        return []
    needle = query["needle"]
    matched = []
    note = item.get("note")
    if isinstance(note, str) and needle in note.casefold():
        matched.append("note")
    names = [pathlib.PurePath(str(project_path)).name]
    if not query.get("digits"):
        names.append(str(item.get("id") or ""))
    if any(needle in name.casefold() for name in names if name):
        matched.append("name")
    port = query["port"]
    if port is not None:
        start, end = item.get("start"), item.get("end")
        if isinstance(start, int):
            if not isinstance(end, int) or end < start:
                end = start
            if start <= port <= end:
                matched.append("port")
    return matched


def status_result(
    project: str | None = None,
    *,
    all_projects: bool = False,
    find: str | None = None,
) -> dict[str, Any]:
    """Return status as an ordinary dict without emitting output or exiting.

    Scope: an explicit ``project``; otherwise the project of the current
    directory's ``.portskill.json``; otherwise every project. ``all_projects``
    and ``find`` both ignore the directory context, so they cover the whole
    registry unless ``project`` is given. Never writes the registry.
    """
    if all_projects and project is not None:
        raise DomainError("invalid_args", "all and project are mutually exclusive")
    query = parse_find_query(find)
    try:
        if project is None and (all_projects or query is not None):
            selected_project = None
        else:
            selected_project = project_from_context(project)
    except (OSError, TypeError, ValueError) as exc:
        raise DomainError("invalid_project", str(exc)) from exc
    start, end = pool_bounds_from_environment()

    def read(registry: dict) -> tuple[dict[str, Any], bool, list]:
        try:
            tailscale_self = (probe_tailscale_status() or {}).get("Self")
        except Exception:
            tailscale_self = None
        if selected_project is None:
            ranges = []
            for project_entry in registry["projects"].values():
                ranges.extend(project_entry.get("ranges", []))
            projects = registry["projects"]
        else:
            ranges = registry["projects"].get(selected_project, {}).get("ranges", [])
            projects = (
                {selected_project: {"ranges": ranges}}
                if selected_project in registry["projects"]
                else {}
            )
        if query is not None:
            return _search_payload(registry, projects, query, tailscale_self), False, []
        enriched = [enrich_range(registry, item, tailscale_self=tailscale_self) for item in ranges]
        projects_out = {}
        for project_path, entry in (projects or {}).items():
            if not isinstance(entry, dict):
                projects_out[project_path] = entry
                continue
            project_ranges = [
                enrich_range(registry, item, tailscale_self=tailscale_self)
                for item in (entry.get("ranges") or [])
            ]
            projects_out[project_path] = {**entry, "ranges": project_ranges}
        payload = {
            "status": "ok",
            "ranges": enriched,
            "pool": registry["pool"],
            "projects": projects_out,
            "machines": normalize_machines(registry.get("machines")),
        }
        summary = drift_summary(enriched)
        if summary["count"]:
            # Only when something drifted (each range always carries "drift").
            payload["drift"] = summary
        return payload, False, []

    try:
        return mutate_registry(
            read,
            start=start,
            end=end,
            normalizer=normalize_registry,
        )
    except CorruptRegistryError as exc:
        raise DomainError("corrupt_registry", str(exc)) from exc
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise DomainError("status_unavailable", str(exc)) from exc


def _search_payload(
    registry: dict, projects: dict, query: dict[str, Any], tailscale_self: dict | None
) -> dict[str, Any]:
    """Status payload holding only matching ranges.

    Matching reads saved fields only; the existing status probe (connect for
    live ranges, drift via ``range_drift``) runs on the matches alone.
    """
    enriched = []
    projects_out = {}
    for project_path, entry in (projects or {}).items():
        if not isinstance(entry, dict):
            continue
        project_ranges = []
        for item in entry.get("ranges") or []:
            matched = range_matches(project_path, item, query)
            if not matched:
                continue
            out = enrich_range(registry, item, tailscale_self=tailscale_self)
            out["project"] = project_path
            out["matched"] = matched
            project_ranges.append(out)
        if project_ranges:
            projects_out[project_path] = {**entry, "ranges": project_ranges}
            enriched.extend(project_ranges)
    payload = {
        "status": "ok",
        "query": {"text": query["text"], "kind": query["kind"]},
        "matches": len(enriched),
        "ranges": enriched,
        "pool": registry["pool"],
        "projects": projects_out,
        "machines": normalize_machines(registry.get("machines")),
    }
    summary = drift_summary(enriched)
    if summary["count"]:
        payload["drift"] = summary
    return payload


def service_statuses(registry: dict) -> list[dict[str, Any]]:
    services = []
    for project, entry in registry.get("projects", {}).items():
        for item in entry.get("ranges", []):
            services.append({"id": item.get("id"), "project": project, **observed_range_status(registry, item)})
    return services
