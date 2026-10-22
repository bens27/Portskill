"""Doctor and settings callable-domain adapter contracts."""
from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


POPULATED_SETTINGS_GET_STDOUT = (
    '{"settings":{"auto_apply_on_launch":false,"auto_apply_preset":null,'
    '"auto_exit_on_shutdown":false,"default_tailnet":"none",'
    '"focused_environment":"work","handoff_enabled":false,'
    '"handoff_kit":null,"handoff_skill":null,"mcp_tools":{},'
    '"mcp_tools_profile":"full","mcp_user_commands":{},'
    '"open_environment_tabs":["work"],"overlap_policy":"allow",'
    '"require_compat":true,"serve_portskill_on_tailscale":false,'
    '"stop_also_release":true},"status":"ok"}\n'
)
PRESET_NOT_FOUND_STDOUT = (
    '{"message":"preset not found: missing","reason":"preset_not_found",'
    '"status":"error"}\n'
)


class CallableDomainSlice2Tests(unittest.TestCase):
    def test_settings_cli_populated_and_error_bytes_remain_stable(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry({
                "settings": {
                    "focused_environment": "work",
                    "open_environment_tabs": ["work"],
                }
            })
            shown = iso.run_cli(["settings", "get"])
            failed = iso.run_cli(["settings", "set", "--auto-apply", "missing"])

        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout, POPULATED_SETTINGS_GET_STDOUT)
        self.assertEqual(shown.stderr, "")
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, PRESET_NOT_FOUND_STDOUT)
        self.assertEqual(failed.stderr, "")

    def test_settings_domain_returns_and_raises_without_emitting(self) -> None:
        from portskill.domain import DomainError
        from portskill.settings import settings_get, settings_set

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                shown = settings_get()
                changed = settings_set({"stop_also_release": False})
                with self.assertRaises(DomainError) as ctx:
                    settings_set({"auto_apply": "missing"})

        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(shown["settings"]["stop_also_release"])
        self.assertFalse(changed["settings"]["stop_also_release"])
        self.assertEqual(ctx.exception.reason, "preset_not_found")
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_doctor_domain_success_and_failure_do_not_emit(self) -> None:
        from portskill.doctor import doctor_result
        from portskill.domain import DomainError

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                healthy = doctor_result(".")
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(healthy["status"], "ok")

        with IsolatedConfig() as iso:
            iso.write_registry_raw("{broken")
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout), self.assertRaises(DomainError) as ctx:
                doctor_result(".")
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(ctx.exception.exit_code, 2)
            self.assertEqual(ctx.exception.as_dict()["reason"], "doctor_failed")

    def test_doctor_cli_invalid_pool_config_bytes_and_exit_remain_stable(self) -> None:
        cases = (
            (
                {"PORTSKILL_POOL_START": "bad"},
                '{"message":"pool bounds must be integers","reason":"invalid_config",'
                '"status":"error"}\n',
            ),
            (
                {"PORTSKILL_POOL_START": "30000", "PORTSKILL_POOL_END": "20000"},
                '{"message":"pool start must be less than or equal to pool end",'
                '"reason":"invalid_config","status":"error"}\n',
            ),
        )
        with IsolatedConfig() as iso:
            iso.write_registry()
            for extra, expected in cases:
                with self.subTest(extra=extra):
                    completed = iso.run_cli(["doctor"], extra=extra)
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, expected)
                    self.assertEqual(completed.stderr, "")

    def test_mcp_doctor_and_settings_never_spawn_cli(self) -> None:
        from portskill import mcp

        with mock.patch.object(
            mcp, "run_cli", side_effect=AssertionError("subprocess used")
        ), mock.patch(
            "portskill.doctor.doctor_result",
            return_value={"status": "ok", "checks": []},
        ) as doctor, mock.patch(
            "portskill.settings.settings_get",
            return_value={"status": "ok", "settings": {}},
        ) as get_settings, mock.patch(
            "portskill.settings.settings_set",
            return_value={"status": "ok", "settings": {"stop_also_release": False}},
        ) as set_settings:
            doctor_call = mcp.call_tool("doctor", {"project": "/tmp/project"})
            get_call = mcp.call_tool("settings_get", {})
            set_call = mcp.call_tool(
                "settings_set", {"stop_also_release": False}
            )

        doctor.assert_called_once_with("/tmp/project")
        get_settings.assert_called_once_with()
        set_settings.assert_called_once_with({"stop_also_release": False})
        self.assertFalse(doctor_call["isError"])
        self.assertFalse(get_call["isError"])
        self.assertFalse(set_call["isError"])

    def test_mcp_stdio_contains_settings_storage_error_then_pings(self) -> None:
        from portskill import mcp

        settings_call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "settings_set",
                "arguments": {"stop_also_release": False},
            },
        }
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(settings_call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()

        with IsolatedConfig() as iso:
            iso.write_registry()
            iso.registry_path.with_name(iso.registry_path.name + ".lock").mkdir()
            with mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(
                mcp.sys, "stdout", stdout
            ):
                exit_code = mcp.mcp_stdio_loop()

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(
            responses[0]["result"]["structuredContent"]["reason"],
            "settings_unavailable",
        )
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_mcp_stdio_contains_doctor_invalid_config_then_pings(self) -> None:
        from portskill import mcp

        doctor_call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {}},
        }
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(doctor_call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()

        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.dict(
                "os.environ", {"PORTSKILL_POOL_START": "bad"}
            ), mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(
                mcp.sys, "stdout", stdout
            ):
                exit_code = mcp.mcp_stdio_loop()

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        error = responses[0]["result"]
        self.assertTrue(error["isError"])
        self.assertEqual(
            error["structuredContent"],
            {
                "status": "error",
                "reason": "invalid_config",
                "message": "pool bounds must be integers",
                "ok": False,
            },
        )
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_server_settings_save_uses_domain_path_in_process(self) -> None:
        from portskill import server

        expected = {"status": "ok", "settings": {"stop_also_release": False}}
        with mock.patch.object(
            server, "run_cli", side_effect=AssertionError("subprocess used")
        ), mock.patch(
            "portskill.settings.settings_set", return_value=expected
        ) as set_settings:
            code, body = server.dispatch_ui_action(
                {"action": "settings-save", "stopAlsoRelease": False}
            )

        self.assertEqual(code, 0)
        self.assertEqual(body, {"ok": True, "result": expected})
        set_settings.assert_called_once_with(
            {
                "auto_apply_preset": "none",
                "stop_also_release": False,
                "require_compat": True,
            }
        )

    def test_server_mcp_doctor_uses_domain_path_in_process(self) -> None:
        from portskill import mcp, server

        expected = {"status": "ok", "checks": []}
        message = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {}},
        }
        with mock.patch.object(
            mcp, "run_cli", side_effect=AssertionError("subprocess used")
        ), mock.patch(
            "portskill.doctor.doctor_result", return_value=expected
        ) as doctor:
            response = server.mcp_handle(message)

        doctor.assert_called_once_with(".")
        self.assertEqual(response["result"]["structuredContent"], expected)
        self.assertFalse(response["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
