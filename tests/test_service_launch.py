"""Regression coverage for the shared service-launch path."""
from __future__ import annotations

import json
import pathlib
import shlex
import sys
import time
import unittest

from tests.helpers import IsolatedConfig, parse_cli_json


def _wait_for_json(path: pathlib.Path, timeout: float = 2.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _configured_range(
    project: pathlib.Path,
    cwd: pathlib.Path,
    marker: pathlib.Path,
    wrong_marker: pathlib.Path,
    *,
    range_id: str,
    port: int,
    default_state: str,
) -> dict[str, object]:
    lifecycle_dir = project / ".portskill"
    lifecycle_dir.mkdir()
    start_script = lifecycle_dir / "start.sh"
    start_script.write_text(
        f"#!/bin/sh\nprintf wrong > {shlex.quote(str(wrong_marker))}\n",
        encoding="utf-8",
    )
    start_script.chmod(0o755)
    command_code = (
        "import json, os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text("
        "json.dumps({'port': os.environ.get('PORT'), 'cwd': os.getcwd()}), "
        "encoding='utf-8')"
    )
    command = shlex.join([sys.executable, "-c", command_code, str(marker)])
    return {
        "id": range_id,
        "start": port,
        "end": port,
        "state": "reserved",
        "note": "configured-service",
        "default_state": default_state,
        "command": command,
        "cwd": str(cwd),
        "tailnet": {"mode": "none", "port": None, "configured_at": None},
        "lifecycle": {
            "start_script": ".portskill/start.sh",
            "stop_script": ".portskill/stop.sh",
            "start_log": ".portskill/start.log",
            "stop_log": ".portskill/stop.log",
            "pid": None,
            "pgid": None,
            "started_at": None,
            "stopped_at": None,
            "last_exit_code": None,
        },
    }


class ServiceLaunchTests(unittest.TestCase):
    def test_apply_defaults_uses_configured_command_cwd_and_port(self) -> None:
        with IsolatedConfig() as iso:
            project = iso.root / "project"
            cwd = iso.root / "configured-cwd"
            marker = iso.root / "command.json"
            wrong_marker = iso.root / "script-ran"
            project.mkdir()
            cwd.mkdir()
            item = _configured_range(
                project,
                cwd,
                marker,
                wrong_marker,
                range_id="r1",
                port=23101,
                default_state="on",
            )
            iso.write_registry({"projects": {str(project.resolve()): {"ranges": [item]}}})

            proc = iso.run_cli(["apply-defaults"])

            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertEqual(parse_cli_json(proc)["error_count"], 0)
            self.assertEqual(
                _wait_for_json(marker),
                {"port": "23101", "cwd": str(cwd.resolve())},
            )
            self.assertFalse(wrong_marker.exists())

    def test_preset_apply_uses_configured_command_cwd_and_port(self) -> None:
        with IsolatedConfig() as iso:
            project = iso.root / "project"
            cwd = iso.root / "configured-cwd"
            marker = iso.root / "command.json"
            wrong_marker = iso.root / "script-ran"
            project.mkdir()
            cwd.mkdir()
            item = _configured_range(
                project,
                cwd,
                marker,
                wrong_marker,
                range_id="r1",
                port=23102,
                default_state="off",
            )
            preset_service = {
                "project": str(project.resolve()),
                "range_id": "r1",
                "note": "configured-service",
                "count": 1,
                "tailnet": "none",
                "default_state": "on",
            }
            iso.write_registry(
                {
                    "projects": {str(project.resolve()): {"ranges": [item]}},
                    "presets": {
                        "work": {
                            "name": "work",
                            "description": "",
                            "services": [preset_service],
                        }
                    },
                }
            )

            proc = iso.run_cli(["preset", "apply", "--name", "work"])

            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            payload = parse_cli_json(proc)
            self.assertEqual(payload["apply_defaults"]["error_count"], 0)
            self.assertEqual(payload["apply_defaults"]["started_count"], 1, payload)
            self.assertEqual(
                _wait_for_json(marker),
                {"port": "23102", "cwd": str(cwd.resolve())},
            )
            self.assertFalse(wrong_marker.exists())


if __name__ == "__main__":
    unittest.main()
