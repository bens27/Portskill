"""Shared domain result and error primitives.

Domain callables return ordinary dictionaries.  Adapters decide how those
results are serialized and how failures map onto their transport.
"""
from __future__ import annotations

import datetime
import os
from typing import Any


def utc_now() -> str:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DomainError(Exception):
    """A transport-neutral domain failure with an intended CLI exit code."""

    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        exit_code: int = 2,
        payload: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message
        self.exit_code = exit_code
        self.payload = dict(payload) if payload is not None else None
        self.extra = {key: value for key, value in extra.items() if value is not None}

    def as_dict(self) -> dict[str, Any]:
        if self.payload is not None:
            return dict(self.payload)
        payload: dict[str, Any] = {"status": "error", "reason": self.reason}
        if self.message is not None:
            payload["message"] = self.message
        payload.update(self.extra)
        return payload


def pool_bounds_from_environment() -> tuple[int, int]:
    """Return validated configured pool bounds without emitting or exiting."""
    try:
        start = int(os.environ.get("PORTSKILL_POOL_START", "20000"))
        end = int(os.environ.get("PORTSKILL_POOL_END", "29999"))
    except ValueError as exc:
        raise DomainError("invalid_config", "pool bounds must be integers") from exc
    if start > end:
        raise DomainError("invalid_config", "pool start must be less than or equal to pool end")
    return start, end


def pid_alive(pid: Any) -> bool:
    """Return whether a process id currently exists without raising."""
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
