"""Doctor exit contract: 0 healthy offline / informational warnings; 2 fail-closed; script parity."""
from __future__ import annotations

import json
import unittest
from argparse import Namespace
from io import StringIO
from unittest.mock import patch

from port_registry_app import cli
from port_registry_app.cli import DOCTOR_FAIL_CLOSED_EXIT, DOCTOR_HARD_CHECKS
from tests.helpers import IsolatedConfig, free_loopback_port, parse_cli_json


def _doctor(iso: IsolatedConfig, extra: dict[str, str] | None = None) -> tuple[int, dict]:
    proc = iso.run_cli(["doctor"], extra=extra)
    return proc.returncode, parse_cli_json(proc)


def _assert_hard_names(payload: dict) -> None:
    names = {c.get("name") for c in payload.get("checks") or [] if isinstance(c, dict)}
    for need in DOCTOR_HARD_CHECKS:
        if need not in names:
            raise AssertionError(f"doctor missing hard check {need!r}")


class DoctorContractTests(unittest.TestCase):
    def test_offline_ok_exit_zero(self) -> None:
        with IsolatedConfig() as iso:
            code, payload = _doctor(iso)
        self.assertEqual(code, 0)
        self.assertEqual(payload.get("status"), "ok")
        self.assertIsNone(payload.get("reason"))
        _assert_hard_names(payload)
        names = {c.get("name") for c in payload.get("checks") or [] if isinstance(c, dict)}
        self.assertIn("version", names)
        self.assertIn("bind_host", names)
        self.assertIn("handoff_kit", names)
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

    def test_offline_idempotent_twice(self) -> None:
        with IsolatedConfig() as iso:
            first_code, first = _doctor(iso)
            second_code, second = _doctor(iso)
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(first.get("status"), second.get("status"))
            self.assertEqual(first.get("reason"), second.get("reason"))
            self.assertFalse(iso.registry_path.exists())
            self.assertFalse(iso.listen_path.exists())

    def test_scripts_doctor_sh_matches_cli_exit_zero(self) -> None:
        with IsolatedConfig() as iso:
            cli_proc = iso.run_cli(["doctor"])
            sh_proc = iso.run_doctor_sh()
        self.assertEqual(cli_proc.returncode, 0, cli_proc.stderr or cli_proc.stdout)
        self.assertEqual(sh_proc.returncode, cli_proc.returncode)
        cli_payload = parse_cli_json(cli_proc)
        sh_payload = parse_cli_json(sh_proc)
        self.assertEqual(cli_payload.get("status"), "ok")
        self.assertEqual(sh_payload.get("status"), cli_payload.get("status"))
        self.assertEqual(sh_payload.get("reason"), cli_payload.get("reason"))

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
            code, payload = _doctor(iso)
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
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
        self.assertIn("bind_host", DOCTOR_HARD_CHECKS)

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
            code, payload = _doctor(iso)
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
            code, payload = _doctor(iso, extra={"PORTSKILL_HANDOFF_KIT": str(bad)})
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
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
            code, payload = _doctor(iso)
        self.assertEqual(code, 0)
        hk = payload.get("handoff_kit") or {}
        self.assertTrue(hk.get("present"))
        self.assertTrue(hk.get("configured"))
        self.assertTrue(hk.get("enabled"))

    def test_corrupt_registry_fail_closed_no_wipe(self) -> None:
        raw = "{not-json"
        with IsolatedConfig() as iso:
            iso.write_registry_raw(raw)
            code, payload = _doctor(iso)
            self.assertEqual(iso.registry_path.read_text(encoding="utf-8"), raw)
            code2, payload2 = _doctor(iso)
            self.assertEqual(iso.registry_path.read_text(encoding="utf-8"), raw)
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
        self.assertEqual(code2, DOCTOR_FAIL_CLOSED_EXIT)
        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("reason"), "doctor_failed")
        self.assertEqual(payload2.get("status"), payload.get("status"))
        registry = next(c for c in payload["checks"] if c.get("name") == "registry")
        self.assertFalse(registry.get("ok"))
        self.assertIn("corrupt_registry", registry.get("detail") or "")

    def test_registry_not_object_fail_closed(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry_raw("[]\n")
            code, payload = _doctor(iso)
            self.assertEqual(iso.registry_path.read_text(encoding="utf-8"), "[]\n")
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
        registry = next(c for c in payload["checks"] if c.get("name") == "registry")
        self.assertFalse(registry.get("ok"))

    def test_corrupt_listen_fail_closed_no_wipe(self) -> None:
        raw = "not-json-listen"
        with IsolatedConfig() as iso:
            iso.write_listen_raw(raw)
            code, payload = _doctor(iso)
            self.assertEqual(iso.listen_path.read_text(encoding="utf-8"), raw)
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
        self.assertEqual(payload.get("status"), "error")
        listen = next(c for c in payload["checks"] if c.get("name") == "listen")
        self.assertFalse(listen.get("ok"))
        self.assertIn("corrupt listen.json", listen.get("detail") or "")

    def test_scripts_doctor_sh_matches_cli_fail_closed(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry_raw("{broken")
            cli_proc = iso.run_cli(["doctor"])
            sh_proc = iso.run_doctor_sh()
            leftover = iso.registry_path.read_text(encoding="utf-8")
        self.assertEqual(cli_proc.returncode, DOCTOR_FAIL_CLOSED_EXIT)
        self.assertEqual(sh_proc.returncode, cli_proc.returncode)
        self.assertEqual(leftover, "{broken")
        self.assertEqual(parse_cli_json(sh_proc).get("reason"), "doctor_failed")

    def test_listening_missing_urls_fail_closed(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_listen({
                "version": 1,
                "listening": True,
                "host": "127.0.0.1",
                "port": 9,
                "ui_url": "",
                "mcp_url": "",
            })
            code, payload = _doctor(iso)
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
        names = {
            c.get("name"): c
            for c in payload.get("checks") or []
            if isinstance(c, dict)
        }
        self.assertFalse(names["ui_reachability"].get("ok"))
        self.assertFalse(names["mcp_reachability"].get("ok"))
        self.assertIn("URL missing", names["ui_reachability"].get("detail") or "")

    def test_listening_unreachable_fail_closed(self) -> None:
        port = free_loopback_port()
        with IsolatedConfig() as iso:
            iso.write_listen({
                "version": 1,
                "listening": True,
                "host": "127.0.0.1",
                "port": port,
                "ui_url": f"http://127.0.0.1:{port}/",
                "mcp_url": f"http://127.0.0.1:{port}/mcp",
            })
            code, payload = _doctor(iso)
        self.assertEqual(code, DOCTOR_FAIL_CLOSED_EXIT)
        names = {
            c.get("name"): c
            for c in payload.get("checks") or []
            if isinstance(c, dict)
        }
        self.assertFalse(names["ui_reachability"].get("ok"))
        self.assertFalse(names["mcp_reachability"].get("ok"))

    def test_skill_files_missing_fail_closed_inprocess(self) -> None:
        with IsolatedConfig() as iso:
            empty = iso.root / "missing-skill"
            empty.mkdir()
            buf = StringIO()
            with patch.object(cli, "skill_dir", return_value=empty), patch(
                "sys.stdout", buf
            ):
                with self.assertRaises(SystemExit) as ctx:
                    cli.cmd_doctor(Namespace(project="."))
            self.assertEqual(ctx.exception.code, DOCTOR_FAIL_CLOSED_EXIT)
            payload = json.loads(buf.getvalue())
        self.assertEqual(payload.get("reason"), "doctor_failed")
        skill = next(c for c in payload["checks"] if c.get("name") == "skill_files")
        self.assertFalse(skill.get("ok"))

    def test_informational_checks_do_not_fail_closed(self) -> None:
        self.assertIn("bind_host", DOCTOR_HARD_CHECKS)
        self.assertIn("handoff_kit", DOCTOR_HARD_CHECKS)
        self.assertNotIn("tailscale_bin", DOCTOR_HARD_CHECKS)
        self.assertNotIn("tailscale_auth", DOCTOR_HARD_CHECKS)
        self.assertNotIn("start_script", DOCTOR_HARD_CHECKS)
        self.assertNotIn("default_state", DOCTOR_HARD_CHECKS)
        self.assertNotIn("version", DOCTOR_HARD_CHECKS)


if __name__ == "__main__":
    unittest.main()
