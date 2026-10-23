"""Callable Tailscale, set-tailnet, and port-discovery adapter contracts."""
from __future__ import annotations

import ast
import io
import json
import pathlib
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig

LOGGED_IN = {"logged_in": True, "chip": "Connected", "state": "connected", "BackendState": "Running"}
BINARY_MISSING = {
    "logged_in": False,
    "chip": "Binary missing",
    "state": "binary_missing",
    "reason": "tailscale_bin_missing",
    "message": "Tailscale binary not found",
}


def _discovery(*ports):
    return {
        "discoveries": [{"port": port, "missing": True, "note": f"svc {port}"} for port in ports],
        "missing": [{"port": port} for port in ports],
        "serve": {"ports": {}},
    }


class CallableDomainSlice6Tests(unittest.TestCase):
    def test_domain_modules_do_not_depend_on_cli_and_are_acyclic(self) -> None:
        from portskill import port_discovery, tailscale_commands

        modules = (port_discovery, tailscale_commands)
        graph = {}
        for module in modules:
            imports = {
                node.module.split(".", 1)[0]
                for node in ast.walk(ast.parse(pathlib.Path(module.__file__).read_text()))
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
            }
            self.assertFalse(imports & {"cli", "mcp", "server"}, module.__name__)
            graph[module.__name__.removeprefix("portskill.")] = imports
        names = set(graph)
        for name, deps in graph.items():
            for dep in deps & names:
                self.assertNotIn(name, graph[dep], f"slice 6 import cycle {name} <-> {dep}")

    def test_domain_results_do_not_print(self) -> None:
        from portskill import port_discovery, tailscale_commands

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch.object(
                tailscale_commands, "probe_tailscale_status", return_value=dict(LOGGED_IN)
            ), mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery", return_value=_discovery()
            ), mock.patch("sys.stdout", stdout):
                status = tailscale_commands.tailscale_status_result()
                discovered = port_discovery.discover_ports_result()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(discovered["missing"], [])

    def test_tailscale_status_binary_missing_is_exit_2_payload(self) -> None:
        from portskill import tailscale_commands
        from portskill.domain import DomainError

        with mock.patch.object(
            tailscale_commands, "probe_tailscale_status", return_value=dict(BINARY_MISSING)
        ):
            with self.assertRaises(DomainError) as ctx:
                tailscale_commands.tailscale_status_result()
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertEqual(ctx.exception.as_dict()["state"], "binary_missing")

    def test_mcp_tools_are_in_process_and_error_then_ping(self) -> None:
        from portskill import mcp, port_discovery, tailscale_commands

        calls = [
            {"name": "tailscale_status", "arguments": {}},
            {"name": "set_tailnet", "arguments": {"range_id": "missing", "mode": "none"}},
            {"name": "ports_import", "arguments": {}},
            {"name": "ports_discover", "arguments": {}},
        ]
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": call})
            for i, call in enumerate(calls, start=1)
        ]
        lines.append(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}))
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(
                mcp, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(
                tailscale_commands, "probe_tailscale_status", return_value=dict(BINARY_MISSING)
            ), mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery", return_value=_discovery()
            ), mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(mcp.sys, "stdout", stdout):
                self.assertEqual(mcp.mcp_stdio_loop(), 0)

        responses = {r["id"]: r for r in map(json.loads, stdout.getvalue().splitlines())}
        self.assertTrue(responses[1]["result"]["isError"])
        self.assertEqual(responses[1]["result"]["structuredContent"]["state"], "binary_missing")
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[2]["result"]["structuredContent"]["reason"], "not_found")
        self.assertTrue(responses[3]["result"]["isError"])
        self.assertFalse(responses[4]["result"]["isError"])
        self.assertEqual(responses[99], {"jsonrpc": "2.0", "id": 99, "result": {}})

    def test_mcp_tailscale_login_needs_input_maps_to_error_envelope(self) -> None:
        from portskill import mcp, tailscale_commands

        needs_login = {
            "state": "needs_login",
            "logged_in": False,
            "AuthURL": "https://login.example/x",
        }
        with mock.patch.object(
            mcp, "run_cli", side_effect=AssertionError("subprocess used")
        ), mock.patch.object(
            tailscale_commands, "probe_tailscale_status", return_value=needs_login
        ) as probe, mock.patch.object(
            tailscale_commands,
            "obtain_tailscale_auth_url",
            return_value=("https://login.example/x", []),
        ):
            result = mcp.call_tool("tailscale_login", {"no_open": True, "wait": 0})
        self.assertEqual(probe.call_count, 2)
        self.assertTrue(result["isError"])
        body = result["structuredContent"]
        self.assertFalse(body["ok"])
        self.assertTrue(body["needs_input"])
        self.assertEqual(body["auth_url"], "https://login.example/x")

    def test_http_actions_are_in_process_failure_then_success(self) -> None:
        from portskill import port_discovery, server, tailscale_commands

        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(
                server, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery", return_value=_discovery()
            ):
                with mock.patch.object(
                    tailscale_commands, "probe_tailscale_status", return_value=dict(BINARY_MISSING)
                ):
                    missing = server.dispatch_ui_action({"action": "serve-portskill", "enabled": True})
                    chip = server.probe_tailscale_for_ui()
                with mock.patch.object(
                    tailscale_commands, "probe_tailscale_status", return_value=dict(LOGGED_IN)
                ):
                    status = server.dispatch_ui_action({"action": "tailscale-status"})
                    not_found = server.dispatch_ui_action(
                        {"action": "set-tailnet", "rangeId": "missing", "mode": "none"}
                    )
                discovered = server.dispatch_ui_action({"action": "discover-ports"})

        self.assertEqual(missing[0], 400)
        self.assertEqual(missing[1]["reason"], "tailscale_bin_missing")
        self.assertEqual(chip["state"], "binary_missing")
        self.assertEqual(status[0], 0)
        self.assertTrue(status[1]["ok"])
        self.assertEqual(not_found[0], 2)
        self.assertEqual(not_found[1]["result"]["reason"], "not_found")
        self.assertEqual(discovered[0], 0)
        self.assertTrue(discovered[1]["ok"])

    def test_http_discover_import_imports_and_continues_past_errors(self) -> None:
        from portskill import port_discovery, server

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            with mock.patch.object(
                server, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery",
                return_value=_discovery(21060, 21061),
            ):
                first = server.dispatch_ui_action({
                    "action": "discover-import", "ports": [21060], "tailnet": "none",
                    "project": str(project),
                })
                again = server.dispatch_ui_action({
                    "action": "discover-import", "ports": [21060, 21061], "tailnet": "none",
                    "project": str(project),
                })

        self.assertEqual(first[0], 0, first)
        self.assertEqual(first[1]["count_imported"], 1)
        # 21060 now overlaps the claimed range; 21061 still imports.
        self.assertEqual(again[0], 0, again)
        self.assertEqual(again[1]["status"], "partial")
        self.assertEqual([e["port"] for e in again[1]["errors"]], [21060])
        self.assertEqual(again[1]["errors"][0]["payload"]["reason"], "port_in_use")
        self.assertEqual(again[1]["count_imported"], 1)

    def test_mcp_argument_errors_keep_legacy_envelope_then_ping(self) -> None:
        from portskill import mcp

        cases = [
            ("ports_import", {}, "ports_import requires ports[], port, or all_missing"),
            ("ports_import", {"port": "bad"}, "invalid literal for int() with base 10: 'bad'"),
            ("tailscale_login", {"wait": "bad"}, "invalid literal for int() with base 10: 'bad'"),
            ("set_tailnet", {}, "'range_id'"),
            ("set_tailnet", {"range_id": "r1"}, "'mode'"),
        ]
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}})
            for i, (name, arguments, _message) in enumerate(cases, start=1)
        ]
        lines.append(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}))
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout = io.StringIO()
        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(
                mcp, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(mcp.sys, "stdout", stdout):
                self.assertEqual(mcp.mcp_stdio_loop(), 0)

        responses = {r["id"]: r for r in map(json.loads, stdout.getvalue().splitlines())}
        for i, (_name, _arguments, message) in enumerate(cases, start=1):
            self.assertEqual(
                responses[i]["result"],
                {
                    "content": [{"type": "text", "text": json.dumps({"ok": False, "message": message})}],
                    "isError": True,
                },
            )
        self.assertEqual(responses[99], {"jsonrpc": "2.0", "id": 99, "result": {}})

    def test_all_missing_local_failures_keep_legacy_import_failed_entry(self) -> None:
        from portskill import cli, port_discovery

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            existing = cli.range_record("r1", 24567, 24567, "busy", "none")
            iso.write_registry({"projects": {str(project.resolve()): {"ranges": [existing]}}})
            # Stale snapshot: 24567 still reported missing although r1 now claims it.
            with mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery",
                return_value=_discovery(24567, 24568),
            ):
                payload = port_discovery.discover_import_result(
                    {"all_missing": True, "tailnet": "none", "project": str(project)}
                )

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["errors"], [{"port": 24567, "reason": "import_failed"}])
        self.assertEqual(payload["count_imported"], 1)

    def test_http_discover_import_keeps_cli_json_diagnostic(self) -> None:
        from portskill import cli, port_discovery, server

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            existing = cli.range_record("r1", 24567, 24567, "busy", "none")
            iso.write_registry({"projects": {str(project.resolve()): {"ranges": [existing]}}})
            with mock.patch.object(
                server, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(
                port_discovery.discover_ports_mod, "build_ports_discovery",
                return_value=_discovery(24567, 24568),
            ):
                single = server.dispatch_ui_action({
                    "action": "discover-import", "port": 24567, "tailnet": "none", "project": str(project),
                })
                partial = server.dispatch_ui_action({
                    "action": "discover-import", "ports": [24567, 24568], "tailnet": "none",
                    "project": str(project),
                })

        expected = (
            '{"message":"ports 24567-24567 overlap existing range r1 (24567-24567)",'
            '"reason":"port_in_use","status":"error"}'
        )
        self.assertEqual(single[0], 422)
        self.assertEqual(single[1]["errors"][0]["stdout"], expected[:300])
        self.assertEqual(partial[0], 0)
        self.assertEqual(partial[1]["errors"][0]["stdout"], expected[:300])
        self.assertEqual(partial[1]["count_imported"], 1)

    def test_server_startup_no_longer_catches_system_exit(self) -> None:
        from portskill import server

        source = pathlib.Path(server.__file__).read_text()
        self.assertNotIn("(Exception, SystemExit)", source)


if __name__ == "__main__":
    unittest.main()
