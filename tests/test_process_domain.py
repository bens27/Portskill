"""Callable start/stop/Path adapter contracts."""
from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class ProcessDomainTests(unittest.TestCase):
    def test_mcp_start_error_then_ping_and_no_subprocess(self) -> None:
        from portskill import mcp

        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "start", "arguments": {"project": ".", "range_id": "missing", "tailnet": "none"},
        }}
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()
        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(mcp, "run_cli", side_effect=AssertionError("subprocess used")), mock.patch.object(
                mcp.sys, "stdin", stdin
            ), mock.patch.object(mcp.sys, "stdout", stdout):
                self.assertEqual(mcp.mcp_stdio_loop(), 0)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_http_start_failure_then_stop_success_is_in_process(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            with mock.patch.object(server, "run_cli", side_effect=AssertionError("subprocess used")):
                failed_code, failed = server.run_cli_action(str(project), "missing", "start")
                allocated_code, allocated = server.dispatch_ui_action({
                    "action": "allocate", "project": str(project), "count": 1, "tailnet": "none",
                })
                range_id = allocated["result"]["range"]["id"]
                success_code, success = server.run_cli_action(str(project), range_id, "stop")
        self.assertEqual(failed_code, 2)
        self.assertFalse(failed["ok"])
        self.assertEqual(allocated_code, 0)
        self.assertEqual(success_code, 0)
        self.assertTrue(success["ok"])

    def test_path_owner_has_no_cli_import(self) -> None:
        import ast
        import pathlib
        import portskill.path

        source = pathlib.Path(portskill.path.__file__).read_text()
        imports = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertNotIn("cli", imports)

    def test_http_bulk_start_stop_routes_are_in_process(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(
                server, "run_cli", side_effect=AssertionError("subprocess used")
            ):
                results = {
                    action: server.dispatch_ui_action({"action": action})
                    for action in ("start-all", "stop-all", "stop-non-default")
                }

        for action, (code, body) in results.items():
            with self.subTest(action=action):
                self.assertEqual(code, 0)
                self.assertTrue(body["ok"])
                self.assertEqual(body["result"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
