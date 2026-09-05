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
        for need in (
            "version",
            "listen",
            "ui_reachability",
            "mcp_reachability",
            "registry",
            "bind_host",
            "handoff_kit",
        ):
            self.assertIn(need, names)
        self.assertTrue(payload.get("loopback"))
        self.assertIsNone(payload.get("message"))
        hk = payload.get("handoff_kit") or {}
        self.assertTrue(hk.get("present"), hk)
        self.assertEqual(hk.get("source"), "vendored")
        self.assertFalse(hk.get("configured"))
        self.assertIn("vendor/session-handoff-kit", hk.get("path") or "")
        handoff_check = next(c for c in payload["checks"] if c.get("name") == "handoff_kit")
        self.assertTrue(handoff_check.get("ok"))
        self.assertIn("configured=no", handoff_check.get("detail") or "")

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

    def test_non_loopback_host_fails_closed_without_override(self) -> None:
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
        self.assertEqual(code, 2)
        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("reason"), "doctor_failed")
        self.assertFalse(payload.get("loopback"))
        self.assertFalse(payload.get("allow_non_loopback"))
        self.assertEqual(payload.get("bind_host"), "0.0.0.0")
        msg = payload.get("message") or ""
        self.assertIn("not loopback", msg)
        self.assertIn("0.0.0.0", msg)
        bind_check = next(
            c for c in payload["checks"] if c.get("name") == "bind_host"
        )
        self.assertFalse(bind_check.get("ok"))
        self.assertTrue(bind_check.get("warning"))
        self.assertIn("not loopback", bind_check.get("detail") or "")
        self.assertIn("--allow-non-loopback", bind_check.get("detail") or "")

    def test_non_loopback_with_override_warns_and_exits_zero(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_listen({
                "version": 1,
                "listening": False,
                "host": "0.0.0.0",
                "port": 20002,
                "ui_url": "http://0.0.0.0:20002/",
                "mcp_url": "http://0.0.0.0:20002/mcp",
                "allow_non_loopback": True,
            })
            code, payload = _doctor({"PORT_REGISTRY_PATH": str(iso.registry_path)})
        self.assertEqual(code, 0)
        self.assertEqual(payload.get("status"), "ok")
        self.assertFalse(payload.get("loopback"))
        self.assertTrue(payload.get("allow_non_loopback"))
        bind_check = next(
            c for c in payload["checks"] if c.get("name") == "bind_host"
        )
        self.assertTrue(bind_check.get("ok"))
        self.assertTrue(bind_check.get("warning"))
        self.assertIn("--allow-non-loopback", bind_check.get("detail") or "")

    def test_invalid_kit_override_fails_closed(self) -> None:
        with IsolatedConfig() as iso:
            bad = iso.root / "not-a-kit"
            bad.mkdir()
            code, payload = _doctor({
                "PORT_REGISTRY_PATH": str(iso.registry_path),
                "PORTSKILL_HANDOFF_KIT": str(bad),
            })
        self.assertEqual(code, 2)
        self.assertEqual(payload.get("status"), "error")
        hk = payload.get("handoff_kit") or {}
        self.assertFalse(hk.get("present"))
        self.assertTrue(hk.get("configured"))
        self.assertEqual(hk.get("source"), "override")
        handoff_check = next(c for c in payload["checks"] if c.get("name") == "handoff_kit")
        self.assertFalse(handoff_check.get("ok"))

    def test_enabled_reports_configured(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"handoff_enabled": True}})
            code, payload = _doctor({"PORT_REGISTRY_PATH": str(iso.registry_path)})
        self.assertEqual(code, 0)
        hk = payload.get("handoff_kit") or {}
        self.assertTrue(hk.get("present"))
        self.assertTrue(hk.get("configured"))
        self.assertTrue(hk.get("enabled"))


if __name__ == "__main__":
    unittest.main()
