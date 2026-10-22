"""Status domain and adapter contract tests."""
from __future__ import annotations

import json
import io
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


EMPTY_STATUS_STDOUT = (
    '{"machines":{"local":{"host":"127.0.0.1","id":"local","kind":"local",'
    '"label":"This Mac"}},"pool":{"end":29999,"start":20000},"projects":{},'
    '"ranges":[],"status":"ok"}\n'
)


class StatusDomainTests(unittest.TestCase):
    def test_cli_stdout_bytes_and_exit_code_remain_stable(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            completed = iso.run_cli(["status"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, EMPTY_STATUS_STDOUT)
        self.assertEqual(completed.stderr, "")

    def test_status_result_is_callable_without_cli_emission(self) -> None:
        from portskill.status import status_result

        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch("portskill.cli.probe_tailscale_status", return_value={"Self": None}):
                result = status_result()

        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ranges"], [])
        self.assertEqual(
            json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n",
            EMPTY_STATUS_STDOUT,
        )

    def test_populated_registry_matches_cli_and_domain_results(self) -> None:
        from portskill.status import status_result

        with IsolatedConfig() as iso:
            project = str((iso.root / "project").resolve())
            iso.write_registry({
                "projects": {
                    project: {
                        "ranges": [{
                            "id": "range-1",
                            "start": 21000,
                            "end": 21000,
                            "state": "released",
                            "tailnet": {"mode": "none"},
                        }]
                    }
                }
            })
            with mock.patch("portskill.cli.probe_tailscale_status", return_value={"Self": None}):
                result = status_result(project)
            completed = iso.run_cli(["status", "--project", project])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), result)
        self.assertEqual(result["ranges"][0]["id"], "range-1")
        self.assertEqual(result["ranges"][0]["allocation_state"], "released")

    def test_domain_error_maps_to_cli_json_and_exit_code(self) -> None:
        expected = (
            '{"message":"pool bounds must be integers","reason":"invalid_config",'
            '"status":"error"}\n'
        )
        with IsolatedConfig() as iso:
            iso.write_registry()
            completed = iso.run_cli(
                ["status"],
                extra={"PORTSKILL_POOL_START": "not-an-integer"},
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, expected)
        self.assertEqual(completed.stderr, "")

    def test_mcp_status_does_not_spawn_cli(self) -> None:
        from portskill import mcp

        expected = {"status": "ok", "ranges": []}
        with mock.patch.object(mcp, "run_cli", side_effect=AssertionError("subprocess used")), mock.patch.object(
            mcp, "status_result", return_value=expected
        ) as status:
            result = mcp.call_tool("status", {"project": "/tmp/example"})

        status.assert_called_once_with("/tmp/example")
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], expected)

    def test_mcp_status_coerces_project_like_previous_cli_adapter(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            iso.write_registry()
            result = mcp.call_tool("status", {"project": 42})

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "ok")

    def test_mcp_stdio_contains_status_storage_error_and_handles_ping(self) -> None:
        from portskill import mcp

        status_call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        }
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(status_call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()

        with IsolatedConfig() as iso:
            iso.write_registry()
            iso.registry_path.with_name(iso.registry_path.name + ".lock").mkdir()
            with mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(mcp.sys, "stdout", stdout):
                exit_code = mcp.mcp_stdio_loop()

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(responses), 2)
        status_result_body = responses[0]["result"]
        self.assertTrue(status_result_body["isError"])
        self.assertEqual(status_result_body["structuredContent"]["status"], "error")
        self.assertFalse(status_result_body["structuredContent"]["ok"])
        self.assertIn("message", status_result_body["structuredContent"])
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_mcp_last_resort_contains_unexpected_tool_exception(self) -> None:
        from portskill import mcp

        with mock.patch.object(mcp, "_call_tool", side_effect=RuntimeError("unexpected failure")):
            result = mcp.call_tool("status", {})

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"],
            {"status": "error", "message": "unexpected failure", "ok": False},
        )

    def test_server_service_status_uses_domain_path_in_process(self) -> None:
        from portskill import server

        raw = {"projects": {}}
        services = [{"id": "range-1", "project": "/tmp/example", "state": "active"}]
        with mock.patch.object(server, "service_statuses", return_value=services) as status:
            payload = server.api_service_status_payload(raw)

        status.assert_called_once_with(raw)
        self.assertEqual(payload, {"services": services})


if __name__ == "__main__":
    unittest.main()
