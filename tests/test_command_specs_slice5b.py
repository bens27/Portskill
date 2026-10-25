"""CommandSpec migration contracts for Tailscale and port discovery."""
from __future__ import annotations

import ast
from dataclasses import replace
import io
import json
import pathlib
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class Slice5BStructureTests(unittest.TestCase):
    def test_all_seven_specs_are_fixed_catalog_members(self) -> None:
        from portskill.command_specs import (
            COMMAND_SPECS,
            PORTS_DISCOVER_COMMAND,
            PORTS_IMPORT_COMMAND,
            SET_TAILNET_COMMAND,
            TAILSCALE_LOGIN_COMMAND,
            TAILSCALE_LOGOUT_COMMAND,
            TAILSCALE_SERVE_PORTSKILL_COMMAND,
            TAILSCALE_STATUS_COMMAND,
        )

        expected = {
            SET_TAILNET_COMMAND: ("portskill.tailscale_commands", "set_tailnet_result"),
            TAILSCALE_STATUS_COMMAND: ("portskill.tailscale_commands", "tailscale_status_result"),
            TAILSCALE_LOGIN_COMMAND: ("portskill.tailscale_commands", "tailscale_login_result"),
            TAILSCALE_LOGOUT_COMMAND: ("portskill.tailscale_commands", "tailscale_logout_result"),
            TAILSCALE_SERVE_PORTSKILL_COMMAND: (
                "portskill.tailscale_commands",
                "serve_portskill_result",
            ),
            PORTS_DISCOVER_COMMAND: ("portskill.port_discovery", "discover_ports_result"),
            PORTS_IMPORT_COMMAND: ("portskill.port_discovery", "discover_import_result"),
        }
        for spec, handler in expected.items():
            with self.subTest(spec=spec.id):
                self.assertIn(spec, COMMAND_SPECS)
                self.assertEqual((spec.handler.module, spec.handler.symbol), handler)

    def test_adapters_do_not_call_migrated_owners_directly(self) -> None:
        package = pathlib.Path(__file__).parents[1] / "portskill"
        forbidden = {
            "set_tailnet_result",
            "tailscale_status_result",
            "tailscale_login_result",
            "tailscale_logout_result",
            "serve_portskill_result",
            "discover_ports_result",
            "discover_import_result",
        }
        for filename in ("cli.py", "mcp.py", "server.py"):
            tree = ast.parse((package / filename).read_text())
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse(called & forbidden, (filename, called & forbidden))


class Slice5BProjectionTests(unittest.TestCase):
    def test_mcp_specs_occupy_frozen_positions(self) -> None:
        from portskill.command_specs import (
            PORTS_DISCOVER_COMMAND,
            PORTS_IMPORT_COMMAND,
            SET_TAILNET_COMMAND,
            TAILSCALE_LOGIN_COMMAND,
            TAILSCALE_STATUS_COMMAND,
        )
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        for spec in (
            SET_TAILNET_COMMAND,
            TAILSCALE_STATUS_COMMAND,
            TAILSCALE_LOGIN_COMMAND,
            PORTS_DISCOVER_COMMAND,
            PORTS_IMPORT_COMMAND,
        ):
            with self.subTest(spec=spec.id):
                self.assertEqual(TOOL_DEFS[spec.mcp.catalog_position], _mcp_tool_definition(spec))

    def test_cli_parsers_are_generated_from_family_specs(self) -> None:
        from portskill.cli import parser

        tailnet = parser().parse_args(
            ["set-tailnet", "--range-id", "r1", "--mode", "serve"]
        )
        self.assertEqual((tailnet.range_id, tailnet.mode), ("r1", "serve"))
        login = parser().parse_args(["tailscale", "login", "--no-open", "--wait", "0"])
        self.assertTrue(login.no_open)
        self.assertEqual(login.wait, 0)
        serve = parser().parse_args(["tailscale", "serve-portskill", "--state", "off"])
        self.assertEqual(serve.state, "off")
        imported = parser().parse_args(
            ["discover", "import", "--port", "3010", "--tailnet", "none"]
        )
        self.assertEqual((imported.port, imported.tailnet), (3010, "none"))

    def test_mcp_routes_through_specs_after_transport_normalization(self) -> None:
        from portskill import mcp
        from portskill.command_specs import TAILSCALE_LOGIN_COMMAND

        with mock.patch(
            "portskill.mcp.execute_command", return_value={"status": "ok"}
        ) as execute:
            result = mcp._call_tool("tailscale_login", {"no_open": True, "wait": "0"})
        execute.assert_called_once_with(
            TAILSCALE_LOGIN_COMMAND, {"no_open": True, "wait": 0}
        )
        self.assertFalse(result["isError"])


class Slice5BUIBindingTests(unittest.TestCase):
    def test_all_declared_ui_fields_bind_transport_options(self) -> None:
        from portskill import server

        cases = (
            (
                "set-tailnet",
                {"rangeId": "r1", "mode": "none", "project": ""},
                {"range_id": "r1", "mode": "none", "project": None},
            ),
            ("tailscale-status", {"ignored": True}, {}),
            ("tailscale-login", {"wait": "bad"}, {"wait": 0, "no_open": True}),
            ("tailscale-logout", {"ignored": True}, {}),
            ("serve-portskill", {"enabled": False}, {"state": "off"}),
            ("discover-ports", {"ignored": True}, {}),
            (
                "discover-import",
                {
                    "ports": [3001, "3002"],
                    "port": "3003",
                    "all_missing": True,
                    "notes": {"3001": "one"},
                    "note": " fallback ",
                    "tailnet": "funnel",
                    "project": " /tmp/project ",
                },
                {
                    "ports": [3001, "3002"],
                    "port": "3003",
                    "all_missing": True,
                    "notes": {"3001": "one"},
                    "note": "fallback",
                    "tailnet": "none",
                    "project": " /tmp/project ",
                },
            ),
        )
        for action, payload, expected in cases:
            with self.subTest(action=action):
                _spec, options = server._ui_command_options(action, payload)
                self.assertEqual(options, expected)

    def test_ui_coercion_metadata_mutation_drives_real_dispatch(self) -> None:
        from portskill import server
        from portskill.command_specs import TAILSCALE_LOGIN_COMMAND

        original = TAILSCALE_LOGIN_COMMAND.params
        object.__setattr__(
            TAILSCALE_LOGIN_COMMAND,
            "params",
            tuple(
                replace(param, ui=replace(param.ui, coercion="integer_or_one"))
                if param.key == "wait"
                else param
                for param in original
            ),
        )
        try:
            with mock.patch(
                "portskill.tailscale_commands.tailscale_login_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {"action": "tailscale-login", "wait": "invalid"}
                )
        finally:
            object.__setattr__(TAILSCALE_LOGIN_COMMAND, "params", original)
        self.assertEqual(code, 0, response)
        owner.assert_called_once_with({"wait": 1, "no_open": True})

    def test_multi_port_ui_import_keeps_aggregation_and_bound_values(self) -> None:
        from portskill import server

        seen = []

        def imported(options):
            seen.append(options)
            return {"status": "ok", "imported": [{"port": options["port"]}], "skipped": []}

        with mock.patch(
            "portskill.port_discovery.discover_import_result", side_effect=imported
        ):
            code, response = server.dispatch_ui_action({
                "action": "discover-import",
                "ports": [3001, 3002],
                "notes": {"3001": "first"},
                "note": " second ",
                "tailnet": "funnel",
                "project": " /tmp/project ",
            })
        self.assertEqual(code, 0, response)
        self.assertEqual(response["count_imported"], 2)
        self.assertEqual(seen, [
            {"port": 3001, "note": "first", "tailnet": "none", "project": "/tmp/project"},
            {"port": 3002, "note": "second", "tailnet": "none", "project": "/tmp/project"},
        ])


class Slice5BUIDispatchRegressionTests(unittest.TestCase):
    @staticmethod
    def _http_action(body):
        from portskill import server

        raw = json.dumps(body).encode()
        handler = object.__new__(server.Handler)
        handler.path = "/portskill/actions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /portskill/actions HTTP/1.1"
        handler.command = "POST"
        handler.client_address = ("127.0.0.1", 1234)
        handler._refuse_untrusted_host = lambda: False
        handler._refuse_mutating_guard = lambda: False
        handler._require_http_access = lambda **_kwargs: True
        handler.log_message = lambda *_args: None
        handler.do_POST()
        header, payload = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(header.split()[1]), json.loads(payload)

    @classmethod
    def _invoke(cls, transport, body):
        from portskill import server

        if transport == "direct":
            code, payload = server.dispatch_ui_action(body)
            return 200 if code == 0 else code if code in (400, 401) else 422, payload
        return cls._http_action(body)

    @staticmethod
    def _discovery(*ports):
        return {
            "discoveries": [
                {
                    "port": port,
                    "missing": True,
                    "note": f"svc {port}",
                    "project_guess": None,
                }
                for port in ports
            ],
            "missing": [{"port": port} for port in ports],
            "serve": {"ports": {}},
        }

    def test_all_missing_string_false_is_rejected_without_persistence(self) -> None:
        # Typed booleans: the string "false" used to read as true and import
        # every missing port. It is now refused before anything is written.
        from portskill import port_discovery

        for transport in ("direct", "http"):
            with self.subTest(transport=transport), IsolatedConfig() as iso:
                project = iso.root / "project"
                project.mkdir()
                iso.write_registry()
                before = iso.registry_path.read_bytes()
                with mock.patch.object(
                    port_discovery.discover_ports_mod,
                    "build_ports_discovery",
                    return_value=self._discovery(23101, 23102),
                ) as discovery:
                    status, payload = self._invoke(transport, {
                        "action": "discover-import",
                        "all_missing": "false",
                        "tailnet": "none",
                        "project": str(project),
                    })

                self.assertEqual(status, 422, payload)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["result"]["reason"], "invalid_args")
                discovery.assert_not_called()
                self.assertEqual(iso.registry_path.read_bytes(), before)

    def test_all_missing_alias_stays_rejected_without_persistence(self) -> None:
        for transport in ("direct", "http"):
            with self.subTest(transport=transport), IsolatedConfig() as iso:
                iso.write_registry()
                before = iso.registry_path.read_bytes()
                status, payload = self._invoke(transport, {
                    "action": "discover-import",
                    "allMissing": True,
                })

                self.assertEqual(status, 400, payload)
                self.assertEqual(payload["message"], "expected ports[] or port")
                self.assertEqual(iso.registry_path.read_bytes(), before)

    def test_all_missing_preserves_spaced_project_and_does_not_persist(self) -> None:
        from portskill import port_discovery

        for transport in ("direct", "http"):
            with self.subTest(transport=transport), IsolatedConfig() as iso:
                project = iso.root / "project"
                project.mkdir()
                iso.write_registry()
                with mock.patch.object(
                    port_discovery.discover_ports_mod,
                    "build_ports_discovery",
                    return_value=self._discovery(23103),
                ):
                    status, payload = self._invoke(transport, {
                        "action": "discover-import",
                        "all_missing": True,
                        "tailnet": "none",
                        "project": f" {project} ",
                    })

                self.assertEqual(status, 422, payload)
                self.assertFalse(payload["ok"])
                registry = json.loads(iso.registry_path.read_text())
                self.assertEqual(registry["projects"], {})

    def test_explicit_ports_strip_spaced_project_before_execution(self) -> None:
        from portskill import port_discovery

        for transport in ("direct", "http"):
            with self.subTest(transport=transport), IsolatedConfig() as iso:
                project = iso.root / "project"
                project.mkdir()
                iso.write_registry()
                with mock.patch.object(
                    port_discovery.discover_ports_mod,
                    "build_ports_discovery",
                    return_value=self._discovery(23104),
                ):
                    status, payload = self._invoke(transport, {
                        "action": "discover-import",
                        "ports": [23104],
                        "tailnet": "none",
                        "project": f" {project} ",
                    })

                registry = json.loads(iso.registry_path.read_text())
                self.assertEqual(status, 200, payload)
                self.assertEqual(payload["count_imported"], 1)
                self.assertIn(str(project.resolve()), registry["projects"])

    def test_set_tailnet_pending_keeps_raw_project_but_execution_sanitizes(self) -> None:
        from portskill import cli, tailscale_commands

        needs_login = {
            "logged_in": False,
            "chip": "Needs login",
            "state": "needs_login",
            "AuthURL": "https://login.example/review",
        }
        for transport in ("direct", "http"):
            for raw_project in ("", 123):
                with self.subTest(
                    transport=transport, raw_project=raw_project
                ), IsolatedConfig() as iso:
                    project = iso.root / "project"
                    project.mkdir()
                    item = cli.range_record("r1", 23105, 23105, "web", "none")
                    iso.write_registry({
                        "projects": {str(project.resolve()): {"ranges": [item]}},
                    })
                    with mock.patch.object(
                        tailscale_commands,
                        "probe_tailscale_status",
                        return_value=needs_login,
                    ):
                        pending_status, pending = self._invoke(transport, {
                            "action": "set-tailnet",
                            "rangeId": "r1",
                            "mode": "serve",
                            "project": raw_project,
                        })

                    executed_status, executed = self._invoke(transport, {
                        "action": "set-tailnet",
                        "rangeId": "r1",
                        "mode": "none",
                        "project": raw_project,
                    })

                    self.assertEqual(pending_status, 401, pending)
                    self.assertEqual(
                        pending["pendingServe"],
                        {"project": raw_project, "rangeId": "r1"},
                    )
                    self.assertEqual(executed_status, 200, executed)
                    self.assertEqual(executed["result"]["project"], str(project.resolve()))


if __name__ == "__main__":
    unittest.main()
