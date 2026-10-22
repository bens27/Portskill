"""Callable settings/user-command adapter contracts (slice 7B)."""
from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig

UPSERT_OK = {"action": "mcp-user-command-save", "name": "deploy", "steps": [{"tool": "status"}]}

# (HTTP body, legacy argv the server used to pass to run_cli)
CASES = [
    ({"action": "mcp-tool-set", "name": "status", "enabled": False},
     ["settings", "set", "--mcp-tool", "status=off"]),
    ({"action": "mcp-tool-set", "name": "no-such-tool", "enabled": True},
     ["settings", "set", "--mcp-tool", "no-such-tool=on"]),
    ({"action": "mcp-user-command-delete", "name": "missing"},
     ["settings", "set", "--mcp-user-command-delete", "missing"]),
    ({"action": "open-environment-tab", "name": "dev"},
     ["settings", "set", "--open-tab", "dev"]),
    ({"action": "focus-environment", "name": "dev"},
     ["settings", "set", "--open-tab", "dev"]),
    ({"action": "close-environment-tab", "name": "dev"},
     ["settings", "set", "--close-tab", "dev"]),
    ({"action": "close-environment-tab", "name": "absent"},
     ["settings", "set", "--close-tab", "absent"]),
]


class CallableDomainSlice7BTests(unittest.TestCase):
    def test_settings_owner_does_not_depend_on_adapters(self) -> None:
        from portskill import settings

        tree = ast.parse(pathlib.Path(settings.__file__).read_text())
        imports = {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        }
        self.assertFalse(imports & {"cli", "mcp", "server"})

    def test_server_has_no_settings_run_cli_left_outside_7a(self) -> None:
        from portskill import server

        source = pathlib.Path(server.__file__).read_text()
        legacy_argv = re.compile(
            r'\[\s*"settings",\s*"set",\s*"--(mcp-tool|mcp-user-command|open-tab|close-tab)'
        )
        self.assertIsNone(legacy_argv.search(source))

    def _http(self, body):
        from portskill import server

        with mock.patch.object(server, "run_cli", side_effect=AssertionError("subprocess used")):
            return server.dispatch_ui_action(body)

    def _legacy(self, argv):
        from portskill import mcp, server

        return server._cli_result(*mcp.run_cli(argv))

    def _settings(self, iso):
        return json.loads(iso.registry_path.read_text()).get("settings")

    def test_http_matches_legacy_cli_results_and_registry(self) -> None:
        for body, argv in CASES:
            with self.subTest(body=body):
                with IsolatedConfig() as iso:
                    iso.write_registry()
                    legacy = self._legacy(argv)
                    legacy_settings = self._settings(iso)
                with IsolatedConfig() as iso:
                    iso.write_registry()
                    new = self._http(body)
                    new_settings = self._settings(iso)
                self.assertEqual(new, legacy)
                self.assertEqual(new_settings, legacy_settings)

    def test_user_command_save_matches_legacy_then_delete(self) -> None:
        argv = [
            "settings", "set",
            "--mcp-user-command-upsert",
            json.dumps({"name": "deploy", "description": "",
                        "steps": [{"tool": "status", "arguments": {}, "mode": "series"}]}),
            "--mcp-tool", "deploy=on",
        ]
        with IsolatedConfig() as iso:
            iso.write_registry()
            legacy = self._legacy(argv)
            legacy_settings = self._settings(iso)
            legacy_delete = self._legacy(["settings", "set", "--mcp-user-command-delete", "deploy"])
            legacy_after = self._settings(iso)
        with IsolatedConfig() as iso:
            iso.write_registry()
            new = self._http(dict(UPSERT_OK))
            new_settings = self._settings(iso)
            deleted = self._http({"action": "mcp-user-command-delete", "name": "deploy"})
            after = self._settings(iso)
        self.assertEqual(new, legacy)
        self.assertEqual(new_settings, legacy_settings)
        self.assertIn("deploy", new_settings["mcp_user_commands"])
        self.assertEqual(deleted, legacy_delete)
        self.assertEqual(after, legacy_after)
        self.assertNotIn("deploy", after.get("mcp_user_commands") or {})

    def test_http_error_keeps_cli_json_diagnostic_then_next_request_works(self) -> None:
        argv = ["settings", "set", "--mcp-tool", "=off"]
        with IsolatedConfig() as iso:
            iso.write_registry()
            legacy = self._legacy(argv)
        with IsolatedConfig() as iso:
            iso.write_registry()
            bad = self._http({"action": "mcp-tool-set", "name": " =", "enabled": False})
            ok = self._http({"action": "open-environment-tab", "name": "dev"})
        self.assertNotEqual(legacy[0], 0)
        self.assertEqual(bad, legacy)
        self.assertEqual(ok[0], 0)

    def test_option_like_values_keep_argparse_rejection_and_no_write(self) -> None:
        """Values argparse read as options must 422 before any write; '-1' etc. stay accepted."""
        families = [
            ("mcp-user-command-delete", "--mcp-user-command-delete"),
            ("focus-environment", "--open-tab"),
            ("open-environment-tab", "--open-tab"),
            ("close-environment-tab", "--close-tab"),
        ]
        values = ["--switch", "--", "--open-tab=other", "-x", "-1", "-1.5", "-", "dev"]
        bodies = [({"action": a, "name": v}, ["settings", "set", f, v]) for a, f in families for v in values]
        for name in ["-bad", "-1", "--", "--opt=val"]:
            for enabled in (True, False):
                state = "on" if enabled else "off"
                bodies.append((
                    {"action": "mcp-tool-set", "name": name, "enabled": enabled},
                    ["settings", "set", "--mcp-tool", f"{name}={state}"],
                ))
        for body, argv in bodies:
            with self.subTest(body=body):
                with IsolatedConfig() as iso:
                    iso.write_registry()
                    legacy = self._legacy(argv)
                    legacy_bytes = iso.registry_path.read_bytes()
                with IsolatedConfig() as iso:
                    iso.write_registry()
                    before = iso.registry_path.read_bytes()
                    new = self._http(body)
                    new_bytes = iso.registry_path.read_bytes()
                self.assertEqual(new, legacy)
                if legacy[0] != 0:
                    self.assertEqual(new_bytes, before)
                self.assertEqual(json.loads(new_bytes), json.loads(legacy_bytes))

    def test_option_like_user_command_name_rejected_before_write(self) -> None:
        body = {**UPSERT_OK, "name": "-deploy"}
        with IsolatedConfig() as iso:
            iso.write_registry()
            before = iso.registry_path.read_bytes()
            code, payload = self._http(body)
            after = iso.registry_path.read_bytes()
        self.assertEqual(code, 2)  # dispatch exit code; the HTTP layer answers 422
        self.assertEqual(payload["result"]["message"], "argument --mcp-tool: expected one argument")
        self.assertEqual(after, before)
        for value in ("--switch", "-1"):
            with IsolatedConfig() as iso:
                iso.write_registry()
                legacy = self._legacy(["settings", "set", "--open-tab", value])
            self.assertEqual(legacy[0] == 0, value == "-1")

if __name__ == "__main__":
    unittest.main()
