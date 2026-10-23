"""Read-only session schedule projection."""
from __future__ import annotations

from .registry import normalize_settings
from .registry_domain import normalize_session_preset, normalize_sessions
from .registry_operations import mutate_registry_meta


def build_schedule_windows(registry: dict, focus=None) -> dict:
    """Return opaque busy windows without exposing service details."""
    sessions = normalize_sessions(registry.get("sessions"))
    focus_name = normalize_session_preset(focus)
    windows = []
    active = sessions.get("active")
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
    if active and active.get("started_at"):
        if focus_name is None or active.get("preset") == focus_name:
            found_open = any(
                window.get("started_at") == active.get("started_at")
                and window.get("preset") == active.get("preset")
                and window.get("ended_at") is None
                for window in windows
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
    windows.sort(key=lambda window: window.get("started_at") or "")
    return {
        "status": "ok",
        "overlap_policy": normalize_settings(registry.get("settings")).get(
            "overlap_policy"
        ),
        "active": active,
        "focus": focus_name,
        "windows": windows,
        "count": len(windows),
    }


def schedule_result(options: dict | None = None) -> dict:
    options = options or {}

    def read(registry):
        return build_schedule_windows(registry, focus=options.get("focus")), False, []

    return mutate_registry_meta(read)
