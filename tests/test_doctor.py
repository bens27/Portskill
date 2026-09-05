"""(b) doctor exit code / ok status offline + loopback warning."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

from tests.helpers import IsolatedConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _doctor(env: dict) -> tuple[int, dict]:
    merged = os.environ.copy()
    merged.update(env)
    merged["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + merged["PYTHONPATH"] if merged.get("PYTHONPATH") else ""
    )
    proc = subprocess.run(
        [sys.executable, "-m", "port_registry_app.cli", "doctor"],
        cwd=str(ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, payload


class DoctorOfflineTests(unittest.TestCase):
    def test_offline_ok_exit_zero(self) -> None:
        with IsolatedConfig() as iso:
            code, payload = _doctor({"PORT_REGISTRY_PATH": str(iso.registry_path)})
        self.assertEqual(code, 0)
        self.assertEqual(payload.get("status"), "ok")
        self.assertIsNone(payload.get("reason"))
        names = {c.get("name") for c in payload.get("checks") or [] if isinstance(c, dict)}
        for need in ("version", "listen", "ui_reachability", "mcp_reachability", "registry", "bind_host"):
            self.assertIn(need, names)
        self.assertTrue(payload.get("loopback"))
        self.assertIsNone(payload.get("message"))

    def test_scripts_doctor_sh_exit_zero(self) -> None:
        with IsolatedConfig() as iso:
            env = os.environ.copy()
            env["PORT_REGISTRY_PATH"] = str(iso.registry_path)
            proc = subprocess.run(
                ["bash", str(ROOT / "scripts/doctor.sh")],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("status"), "ok")

    def test_non_loopback_host_warns_without_failing(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_listen({
                "version": 1,
                "listening": False,
                "host": "0.0.0.0",
                "port": 20001,
                "ui_url": "http://0.0.0.0:20001/",
                "mcp_url": "http://0.0.0.0:20001/mcp",
            })
            code, payload = _doctor({"PORT_REGISTRY_PATH": str(iso.registry_path)})
        self.assertEqual(code, 0)
        self.assertEqual(payload.get("status"), "ok")
        self.assertFalse(payload.get("loopback"))
        self.assertEqual(payload.get("bind_host"), "0.0.0.0")
        msg = payload.get("message") or ""
        self.assertIn("not loopback", msg)
        self.assertIn("0.0.0.0", msg)
        bind_check = next(
            c for c in payload["checks"] if c.get("name") == "bind_host"
        )
        self.assertTrue(bind_check.get("ok"))
        self.assertTrue(bind_check.get("warning"))
        self.assertIn("not loopback", bind_check.get("detail") or "")


if __name__ == "__main__":
    unittest.main()
