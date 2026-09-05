"""Shared stdlib helpers for isolated Portskill tests (no user-home writes)."""
from __future__ import annotations

import json
import os
import pathlib
import socket
import tempfile
from typing import Any


class IsolatedConfig:
    """Temp registry + listen dir. Sets PORT_REGISTRY_PATH; restores env on close."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="portskill-test-")
        self.root = pathlib.Path(self._tmp.name)
        self.registry_path = self.root / "registry.json"
        self.listen_path = self.root / "listen.json"
        self._prev: dict[str, str | None] = {}

    def __enter__(self) -> IsolatedConfig:
        for key in ("PORT_REGISTRY_PATH", "PORTSKILL_LISTEN_PATH", "PORTSKILL_HANDOFF_KIT"):
            self._prev[key] = os.environ.get(key)
        os.environ["PORT_REGISTRY_PATH"] = str(self.registry_path)
        os.environ.pop("PORTSKILL_LISTEN_PATH", None)
        os.environ.pop("PORTSKILL_HANDOFF_KIT", None)
        return self

    def __exit__(self, *exc: object) -> None:
        for key, val in self._prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def write_registry(self, data: dict[str, Any] | None = None) -> pathlib.Path:
        settings = {
            "mcp_tools": {},
            "mcp_user_commands": {},
            "require_compat": True,
        }
        payload = {
            "version": 1,
            "pool": {"start": 20000, "end": 29999},
            "projects": {},
            "presets": {},
            "settings": settings,
        }
        if data:
            incoming_settings = data.get("settings")
            payload.update(data)
            if isinstance(incoming_settings, dict):
                merged = dict(settings)
                merged.update(incoming_settings)
                payload["settings"] = merged
        self.registry_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return self.registry_path

    def write_listen(self, data: dict[str, Any]) -> pathlib.Path:
        self.listen_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return self.listen_path


def free_loopback_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def hold_loopback_port(port: int | None = None) -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    if port is None:
        sock.bind(("127.0.0.1", 0))
    else:
        sock.bind(("127.0.0.1", int(port)))
    sock.listen(1)
    return sock, int(sock.getsockname()[1])
