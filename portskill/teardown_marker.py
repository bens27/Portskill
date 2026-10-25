"""Sweep teardown marker: a short-lived guard against re-applying a mapping.

A sweep release parks a range whose Serve/Funnel mapping it is about to remove
outside the registry lock, and sets ``tailnet[TEARDOWN_MARKER_KEY]`` to
``{"token": str, "at": epoch seconds}``. While the marker is fresh, start,
restart and activate refuse the range: they would re-apply a mapping the
pending ``off`` is about to remove. It expires after
``TEARDOWN_MARKER_TTL_SECONDS`` so a crashed sweep cannot wedge the range.
Leaf module (imports only ``domain``) so lifecycle and service can share it.
"""
from __future__ import annotations

import time
from typing import Any

from .domain import DomainError

TEARDOWN_MARKER_KEY = "sweep_teardown"
TEARDOWN_MARKER_TTL_SECONDS = 120


def teardown_marker(item: Any) -> dict | None:
    """Return the fresh teardown marker on item, or None (absent, malformed or expired)."""
    tailnet = item.get("tailnet") if isinstance(item, dict) else None
    marker = tailnet.get(TEARDOWN_MARKER_KEY) if isinstance(tailnet, dict) else None
    if not isinstance(marker, dict):
        return None
    at = marker.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    # A stamp more than a TTL in the future (clock stepped back, or a bad
    # write) would otherwise block the range for as long: treat it as expired.
    if abs(time.time() - at) > TEARDOWN_MARKER_TTL_SECONDS:
        return None
    return marker


def teardown_in_progress_message(item: dict) -> str:
    return (
        f"Range {item.get('id')} is being released by sweep (its Tailscale mapping is being "
        "removed). Try again in a moment."
    )


def refuse_teardown_in_progress(item: dict) -> None:
    if teardown_marker(item) is not None:
        raise DomainError(
            "teardown_in_progress", teardown_in_progress_message(item), range_id=item.get("id")
        )
