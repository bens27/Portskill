"""Callable Session Handoff and HTTP-auth adapter contracts."""
from __future__ import annotations

import ast
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, ROOT


class CallableDomainSlice7ATests(unittest.TestCase):
    def test_domain_owners_do_not_emit_or_reach_adapters(self) -> None:
        from portskill import auth, handoff, settings
        from portskill.domain import DomainError

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                shown = auth.http_auth_result({"command": "gate", "gate_mode": "show"})
                configured = settings.settings_set({
                    "handoff_enabled": True,
                    "handoff_kit": "/tmp/kit",
                    "handoff_skill": "none",
                })
                with self.assertRaises(DomainError):
                    handoff.fetch_kit_result({"ref": "../bad"})
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(shown["status"], "ok")
        self.assertTrue(configured["settings"]["handoff_enabled"])

        with IsolatedConfig(), mock.patch.object(
            auth, "atomic_write_json", side_effect=OSError("read only")
        ), self.assertRaises(DomainError) as raised:
            auth.http_auth_result({"command": "show"})
        self.assertEqual(raised.exception.reason, "auth_unavailable")
        self.assertEqual(raised.exception.exit_code, 1)

        package = pathlib.Path(auth.__file__).parent
        graph = {}
        for path in package.glob("*.py"):
            graph[path.stem] = {
                node.module.split(".", 1)[0]
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module
            }
        forbidden = {"cli", "mcp", "server"}
        for owner in ("auth", "handoff", "settings"):
            visiting = set()
            visited = set()

            def visit(name):
                self.assertNotIn(name, forbidden, f"{owner} reaches adapter {name}")
                self.assertNotIn(name, visiting, f"import cycle from {owner} at {name}")
                if name in visited or name not in graph:
                    return
                visiting.add(name)
                for dependency in graph[name]:
                    visit(dependency)
                visiting.remove(name)
                visited.add(name)

            visit(owner)

    def test_cli_handoff_and_auth_error_contracts(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            cases = (
                (
                    ["handoff", "fetch", "--ref", "../bad"],
                    '{"message":"invalid kit ref: \'../bad\'","reason":"bad_ref",'
                    '"status":"error"}\n',
                ),
                (
                    ["http-auth", "passkeys", "delete"],
                    '{"message":"http-auth passkeys delete requires --id",'
                    '"reason":"invalid_args","status":"error"}\n',
                ),
            )
            for argv, expected in cases:
                with self.subTest(argv=argv):
                    proc = iso.run_cli(argv)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertEqual(proc.stdout, expected)
                    self.assertEqual(proc.stderr, "")

            gate = iso.run_cli(["http-auth", "gate", "show"])
            settings = iso.run_cli([
                "settings", "set",
                "--handoff-enabled", "on",
                "--handoff-kit", "/tmp/kit",
                "--handoff-skill", "none",
            ])
        self.assertEqual(gate.returncode, 0, gate.stderr)
        self.assertFalse(json.loads(gate.stdout)["passkey_gate"])
        self.assertEqual(settings.returncode, 0, settings.stderr)
        saved = json.loads(settings.stdout)["settings"]
        self.assertTrue(saved["handoff_enabled"])
        self.assertEqual(saved["handoff_kit"], "/tmp/kit")
        self.assertIsNone(saved["handoff_skill"])

    def test_handoff_dashboard_actions_never_spawn_cli(self) -> None:
        from portskill import server
        from portskill.domain import DomainError

        settings_cases = (
            {"action": "handoff-set-enabled", "enabled": True},
            {"action": "handoff-set-kit", "path": "/tmp/kit"},
            {"action": "handoff-skill-clear"},
        )
        for body in settings_cases:
            with self.subTest(action=body["action"]), mock.patch(
                "portskill.settings.settings_set",
                side_effect=[
                    DomainError("settings_unavailable", "boom", exit_code=1),
                    {"status": "ok", "settings": {}},
                ],
            ):
                failed = server.dispatch_ui_action(body)
                succeeded = server.dispatch_ui_action(body)
            self.assertEqual(failed, (1, {
                "ok": False,
                "message": "boom",
                "result": {
                    "status": "error",
                    "reason": "settings_unavailable",
                    "message": "boom",
                },
            }))
            self.assertEqual(succeeded[0], 0)
            self.assertTrue(succeeded[1]["ok"])

        with mock.patch(
            "portskill.handoff.fetch_kit_result",
            side_effect=[
                DomainError("fetch_failed", "boom"),
                {"ok": True, "ref": "v1"},
            ],
        ):
            failed = server.dispatch_ui_action({"action": "handoff-fetch", "ref": "v1"})
            succeeded = server.dispatch_ui_action({"action": "handoff-fetch", "ref": "v1"})
        self.assertEqual(failed[0], 2)
        self.assertFalse(failed[1]["ok"])
        self.assertEqual(succeeded[0], 0)

        with mock.patch.object(
            server,
            "persist_custom_skill",
            return_value={"ok": True, "path": "/tmp/custom-skill.md"},
        ), mock.patch(
            "portskill.settings.settings_set",
            side_effect=[
                DomainError("settings_unavailable", "boom", exit_code=1),
                {"status": "ok", "settings": {}},
            ],
        ):
            body = {"action": "handoff-skill-upload", "content": "# Skill\n"}
            failed = server.dispatch_ui_action(body)
            succeeded = server.dispatch_ui_action(body)
        self.assertEqual(failed[0], 1)
        self.assertEqual(succeeded[0], 0)
        self.assertEqual(succeeded[1]["handoff_skill"], "/tmp/custom-skill.md")

    def test_handoff_dashboard_argument_validation_is_unchanged(self) -> None:
        from portskill.server import dispatch_ui_action

        self.assertEqual(
            dispatch_ui_action({"action": "handoff-set-enabled"}),
            (400, {"ok": False, "message": "expected enabled bool"}),
        )
        self.assertEqual(
            dispatch_ui_action({"action": "handoff-set-kit", "path": 42}),
            (400, {"ok": False, "message": "expected path"}),
        )
        self.assertEqual(
            dispatch_ui_action({"action": "handoff-skill-upload", "content": None}),
            (400, {"ok": False, "message": "expected skill file content"}),
        )

    def test_real_mcp_handoff_settings_error_preserves_ping(self) -> None:
        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "settings_set",
                "arguments": {
                    "handoff_enabled": True,
                    "handoff_kit": "/tmp/kit",
                    "handoff_skill": "none",
                },
            },
        }
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        with IsolatedConfig() as iso:
            iso.write_registry_raw("{broken")
            proc = subprocess.run(
                [sys.executable, "-m", "portskill", "--mcp-stdio"],
                cwd=str(ROOT),
                env=iso.subprocess_env(),
                input=json.dumps(call) + "\n" + json.dumps(ping) + "\n",
                capture_output=True,
                text=True,
                timeout=30,
            )
        responses = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(
            responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}}
        )

    def test_mcp_handoff_settings_are_in_process(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            iso.write_registry()
            result = mcp.call_tool("settings_set", {
                "handoff_enabled": True,
                "handoff_kit": "/tmp/kit",
                "handoff_skill": "none",
            })
        self.assertFalse(result["isError"], result)
        settings = result["structuredContent"]["settings"]
        self.assertTrue(settings["handoff_enabled"])
        self.assertEqual(settings["handoff_kit"], "/tmp/kit")
        self.assertIsNone(settings["handoff_skill"])

    def test_http_auth_endpoints_contain_failure_then_recover(self) -> None:
        from portskill import auth, server
        from tests.test_http_auth import _http_json, _start_handler

        with IsolatedConfig() as iso:
            iso.write_registry()
            token = auth.ensure_http_auth_token()[0]["token"]
            httpd, port = _start_handler()
            try:
                cases = (
                    (
                        "/api/http-auth/gate",
                        {"enabled": False},
                        None,
                        "gate",
                    ),
                    (
                        "/api/http-auth/regenerate",
                        {},
                        token,
                        "regenerate",
                    ),
                    (
                        "/api/webauthn/credentials/delete",
                        {"id": "missing"},
                        None,
                        "passkeys",
                    ),
                )
                for path, body, bearer, command in cases:
                    real = auth.http_auth_result
                    failed_once = {"value": False}

                    def operation(options):
                        self.assertEqual(options["command"], command)
                        if not failed_once["value"]:
                            failed_once["value"] = True
                            raise RuntimeError("boom")
                        return real(options)

                    with self.subTest(path=path), mock.patch(
                        "portskill.auth.http_auth_result", side_effect=operation
                    ):
                        failed = _http_json(
                            f"http://127.0.0.1:{port}{path}",
                            method="POST",
                            token=bearer,
                            body=body,
                        )
                        succeeded = _http_json(
                            f"http://127.0.0.1:{port}{path}",
                            method="POST",
                            token=bearer,
                            body=body,
                        )
                    self.assertEqual(failed, (422, {"ok": False, "message": "boom"}))
                    self.assertEqual(succeeded[0], 200, succeeded[1])
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
