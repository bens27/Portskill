"""Transport-neutral active-session state helpers."""
from __future__ import annotations

from .domain import utc_now
from .registry_domain import (
    SESSION_HISTORY_CAP,
    normalize_session_preset,
    normalize_session_source,
    normalize_sessions,
)


def begin_active_session(registry, preset, source):
    sessions = normalize_sessions(registry.get("sessions"))
    now = utc_now()
    previous = sessions.get("active")
    if previous and previous.get("started_at"):
        closed = False
        for entry in reversed(sessions["history"]):
            if (
                entry.get("ended_at") is None
                and entry.get("started_at") == previous.get("started_at")
                and entry.get("preset") == previous.get("preset")
            ):
                entry["ended_at"] = now
                closed = True
                break
        if not closed:
            sessions["history"].append({
                "preset": previous.get("preset"),
                "started_at": previous.get("started_at"),
                "ended_at": now,
                "opaque": True,
            })
    preset_name = normalize_session_preset(preset)
    source_name = normalize_session_source(source)
    sessions["active"] = {
        "preset": preset_name,
        "started_at": now,
        "source": source_name,
    }
    sessions["history"].append({
        "preset": preset_name,
        "started_at": now,
        "ended_at": None,
        "opaque": True,
    })
    sessions["history"] = sessions["history"][-SESSION_HISTORY_CAP:]
    registry["sessions"] = sessions
    return sessions["active"]


def end_active_session(registry):
    sessions = normalize_sessions(registry.get("sessions"))
    now = utc_now()
    active = sessions.get("active")
    if active and active.get("started_at"):
        closed = False
        for entry in reversed(sessions["history"]):
            if (
                entry.get("ended_at") is None
                and entry.get("started_at") == active.get("started_at")
                and entry.get("preset") == active.get("preset")
            ):
                entry["ended_at"] = now
                closed = True
                break
        if not closed:
            sessions["history"].append({
                "preset": active.get("preset"),
                "started_at": active.get("started_at"),
                "ended_at": now,
                "opaque": True,
            })
    sessions["active"] = None
    sessions["history"] = sessions["history"][-SESSION_HISTORY_CAP:]
    registry["sessions"] = sessions
    return active
