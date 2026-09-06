"""Shared stdlib helpers for isolated Portskill tests (no user-home writes)."""
from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME_LISTEN = pathlib.Path.home() / ".config" / "port-registry" / "listen.json"

_ISOLATE_KEYS = (
    "PORT_REGISTRY_PATH",
    "PORTSKILL_LISTEN_PATH",
    "PORTSKILL_HTTP_AUTH_PATH",
    "PORTSKILL_HTTP_PASSKEY_PATH",
    "PORTSKILL_HTTP_SESSIONS_PATH",
    "HOME",
    "XDG_CONFIG_HOME",
    "PORTSKILL_HANDOFF_KIT",
)


class IsolatedConfig:
    """Temp registry + listen dir. Pins env so tests never touch user home.

    Sets PORT_REGISTRY_PATH, PORTSKILL_LISTEN_PATH, HOME, and XDG_CONFIG_HOME.
    Clears PORTSKILL_HANDOFF_KIT (pass extra= to restore an override for one call).
    Restores the previous values on close. Subprocess helpers copy this env so
    a leaked PORTSKILL_LISTEN_PATH / leftover home listen.json cannot flake CI.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="portskill-test-")
        self.root = pathlib.Path(self._tmp.name)
        self.registry_path = self.root / "registry.json"
        self.listen_path = self.root / "listen.json"
        self.home_dir = self.root / "home"
        self.xdg_config = self.home_dir / ".config"
        # Snapshot the real home listen path before we rewrite HOME.
        self.real_home_listen = HOME_LISTEN
        self._prev: dict[str, str | None] = {}

    def __enter__(self) -> IsolatedConfig:
        for key in _ISOLATE_KEYS:
            self._prev[key] = os.environ.get(key)
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.xdg_config.mkdir(parents=True, exist_ok=True)
        os.environ["PORT_REGISTRY_PATH"] = str(self.registry_path)
        os.environ["PORTSKILL_LISTEN_PATH"] = str(self.listen_path)
        os.environ["HOME"] = str(self.home_dir)
        os.environ["XDG_CONFIG_HOME"] = str(self.xdg_config)
        os.environ.pop("PORTSKILL_HANDOFF_KIT", None)
        os.environ.pop("PORTSKILL_HTTP_AUTH_PATH", None)
        os.environ.pop("PORTSKILL_HTTP_PASSKEY_PATH", None)
        os.environ.pop("PORTSKILL_HTTP_SESSIONS_PATH", None)
        return self

    def __exit__(self, *exc: object) -> None:
        for key, val in self._prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def subprocess_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Env for CLI/script subprocesses: isolated paths + PYTHONPATH."""
        env = os.environ.copy()
        env["PORT_REGISTRY_PATH"] = str(self.registry_path)
        env["PORTSKILL_LISTEN_PATH"] = str(self.listen_path)
        env["HOME"] = str(self.home_dir)
        env["XDG_CONFIG_HOME"] = str(self.xdg_config)
        env["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        env.pop("PORTSKILL_HANDOFF_KIT", None)
        env.pop("PORTSKILL_HTTP_AUTH_PATH", None)
        env.pop("PORTSKILL_HTTP_PASSKEY_PATH", None)
        env.pop("PORTSKILL_HTTP_SESSIONS_PATH", None)
        if extra:
            env.update(extra)
        return env

    def run_cli(
        self,
        argv: list[str],
        timeout: int = 60,
        extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "port_registry_app.cli", *argv],
            cwd=str(ROOT),
            env=self.subprocess_env(extra),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def run_doctor_sh(self, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT / "scripts/doctor.sh")],
            cwd=str(ROOT),
            env=self.subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def write_registry(self, data: dict[str, Any] | None = None) -> pathlib.Path:
        settings = {
            "mcp_tools": {},
            "mcp_tools_profile": "full",
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

    def write_listen_raw(self, text: str) -> pathlib.Path:
        self.listen_path.write_text(text, encoding="utf-8")
        return self.listen_path

    def write_registry_raw(self, text: str) -> pathlib.Path:
        self.registry_path.write_text(text, encoding="utf-8")
        return self.registry_path


def parse_cli_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if not proc.stdout or not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def free_loopback_port(retries: int = 16) -> int:
    """Ephemeral port that is still bindable after release.

    Bind-then-close has a TOCTOU gap on Linux CI; retry until a probe bind works.
    """
    last_err: OSError | None = None
    for _ in range(retries):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        except OSError as exc:
            last_err = exc
            continue
        finally:
            sock.close()
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            probe.bind(("127.0.0.1", port))
            return port
        except OSError as exc:
            last_err = exc
        finally:
            probe.close()
    raise OSError(f"no free loopback port after {retries} tries: {last_err}")


def hold_loopback_port(port: int | None = None) -> tuple[socket.socket, int]:
    """Listen exclusively on a loopback port (second bind must fail)."""
    last_err: OSError | None = None
    for _ in range(8):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            if port is None:
                sock.bind(("127.0.0.1", 0))
            else:
                sock.bind(("127.0.0.1", int(port)))
            sock.listen(1)
            bound = int(sock.getsockname()[1])
        except OSError as exc:
            sock.close()
            last_err = exc
            if port is not None:
                break
            continue
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", bound))
        except OSError:
            probe.close()
            return sock, bound
        probe.close()
        sock.close()
        last_err = OSError(f"port {bound} was not exclusive after listen")
        if port is not None:
            break
    raise OSError(f"could not exclusively hold a loopback port: {last_err}")


@contextmanager
def active_listen(payload: dict[str, Any]) -> Iterator[None]:
    """Swap server._ACTIVE_LISTEN and always restore (avoids leaked bind banners)."""
    import port_registry_app.server as srv

    prev = srv._ACTIVE_LISTEN
    srv._ACTIVE_LISTEN = payload
    try:
        yield
    finally:
        srv._ACTIVE_LISTEN = prev
