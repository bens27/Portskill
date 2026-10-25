"""Final dashboard and MCP subprocess-removal contracts."""
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


class CallableDomainSlice8Tests(unittest.TestCase):
    def test_workspace_domain_does_not_emit_or_reach_adapters(self) -> None:
        from portskill import workspace
        from portskill.domain import DomainError

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                with self.assertRaises(DomainError):
                    workspace.workspace_draft_set_result({})
                self.assertEqual(
                    workspace.workspace_draft_revert_result()["status"], "ok"
                )
                self.assertEqual(
                    workspace.workspace_edit_mode_result({"state": "on"})["status"],
                    "ok",
                )
        self.assertEqual(stdout.getvalue(), "")

        package = pathlib.Path(workspace.__file__).parent
        graph = {}
        for path in package.glob("*.py"):
            graph[path.stem] = {
                node.module.split(".", 1)[0]
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module
            }
        visiting = set()
        visited = set()

        def visit(name):
            self.assertNotIn(name, {"cli", "mcp", "server"})
            self.assertNotIn(name, visiting, f"workspace import cycle at {name}")
            if name in visited or name not in graph:
                return
            visiting.add(name)
            for dependency in graph[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        visit("workspace")

    def test_workspace_stage_save_revert_persist_contract(self) -> None:
        from portskill.registry_operations import range_record
        from portskill.workspace import (
            workspace_draft_revert_result,
            workspace_draft_save_result,
            workspace_draft_set_result,
        )

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record("r1", 23200, 23200, None, "none")
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            staged = workspace_draft_set_result({
                "range_id": "r1",
                "command": "python app.py",
                "cwd": str(project),
            })
            saved = workspace_draft_save_result({"range_id": "r1"})
            workspace_draft_set_result({"range_id": "r1", "command": "other"})
            reverted = workspace_draft_revert_result({"range_id": "r1"})
            persisted = json.loads(iso.registry_path.read_text())

        self.assertTrue(staged["modified"])
        self.assertEqual(saved["applied_count"], 1)
        self.assertFalse(reverted["modified"])
        saved_item = persisted["projects"][str(project.resolve())]["ranges"][0]
        self.assertEqual(saved_item["command"], "python app.py")
        self.assertEqual(saved_item["cwd"], str(project))
        self.assertEqual(persisted["workspace_draft"]["ranges"], {})

    def test_cli_workspace_json_and_persistence_contract(self) -> None:
        from portskill.registry_operations import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record("r1", 23210, 23210, None, "none")
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            staged = iso.run_cli([
                "workspace", "draft-set", "--range-id", "r1",
                "--command", "python app.py", "--cwd", str(project),
            ])
            saved = iso.run_cli(["workspace", "draft-save", "--range-id", "r1"])
            reverted = iso.run_cli(["workspace", "draft-revert"])
            persisted = json.loads(iso.registry_path.read_text())

        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertEqual(json.loads(staged.stdout)["range_id"], "r1")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads(saved.stdout)["applied_count"], 1)
        self.assertEqual(reverted.returncode, 0, reverted.stderr)
        self.assertFalse(json.loads(reverted.stdout)["modified"])
        saved_item = persisted["projects"][str(project.resolve())]["ranges"][0]
        self.assertEqual(saved_item["command"], "python app.py")

    def test_dashboard_slice8_actions_fail_then_succeed_without_cli(self) -> None:
        from portskill import server
        from portskill.domain import DomainError

        cases = (
            (
                "portskill.presets.preset_save_result",
                {"action": "new-environment", "name": "empty"},
            ),
            (
                "portskill.workspace.workspace_draft_set_result",
                {"action": "draft-stage", "rangeId": "r1", "command": "run"},
            ),
            (
                "portskill.workspace.workspace_draft_save_result",
                {"action": "draft-save", "rangeId": "r1"},
            ),
            (
                "portskill.workspace.workspace_draft_revert_result",
                {"action": "draft-revert", "rangeId": "r1"},
            ),
        )
        for operation_name, body in cases:
            with self.subTest(action=body["action"]), mock.patch(
                operation_name,
                side_effect=[
                    DomainError("registry_unavailable", "boom", exit_code=1),
                    {"status": "ok"},
                ],
            ):
                failed = server.dispatch_ui_action(body)
                succeeded = server.dispatch_ui_action(body)
            self.assertEqual(failed, (1, {
                "ok": False,
                "message": "boom",
                "result": {
                    "status": "error",
                    "reason": "registry_unavailable",
                    "message": "boom",
                },
            }))
            self.assertEqual(succeeded, (0, {
                "ok": True,
                "result": {"status": "ok"},
            }))

    def test_dashboard_slice8_real_domains_persist_without_cli(self) -> None:
        from portskill import server
        from portskill.registry_operations import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record("r1", 23220, 23220, None, "none")
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            created = server.dispatch_ui_action({
                "action": "new-environment",
                "name": "empty",
                "description": "Empty env",
            })
            staged = server.dispatch_ui_action({
                "action": "draft-stage",
                "rangeId": "r1",
                "command": "python app.py",
                "cwd": str(project),
            })
            saved = server.dispatch_ui_action({
                "action": "draft-save",
                "rangeId": "r1",
            })
            reverted = server.dispatch_ui_action({"action": "draft-revert"})
            persisted = json.loads(iso.registry_path.read_text())

        for code, body in (created, staged, saved, reverted):
            self.assertEqual(code, 0, body)
            self.assertTrue(body["ok"], body)
        self.assertIn("empty", persisted["presets"])
        saved_item = persisted["projects"][str(project.resolve())]["ranges"][0]
        self.assertEqual(saved_item["command"], "python app.py")

    def test_dashboard_option_values_match_legacy_argparse_without_writes(self) -> None:
        from portskill import server

        cases = (
            (
                {"action": "new-environment", "name": "VALUE"},
                "--name",
            ),
            (
                {
                    "action": "new-environment",
                    "name": "valid",
                    "description": "VALUE",
                },
                "--description",
            ),
            (
                {"action": "draft-stage", "rangeId": "VALUE", "command": "run"},
                "--range-id",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "startScript": "VALUE"},
                "--start-script",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "start_script": "VALUE"},
                "--start-script",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "stopScript": "VALUE"},
                "--stop-script",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "stop_script": "VALUE"},
                "--stop-script",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "command": "VALUE"},
                "--command",
            ),
            (
                {"action": "draft-stage", "rangeId": "r1", "cwd": "VALUE"},
                "--cwd",
            ),
            (
                {"action": "draft-save", "rangeId": "VALUE"},
                "--range-id",
            ),
            (
                {"action": "draft-revert", "rangeId": "VALUE"},
                "--range-id",
            ),
        )
        for template, flag in cases:
            for invalid in ("-x", "--", "--opt=val"):
                body = {
                    key: invalid if value == "VALUE" else value
                    for key, value in template.items()
                }
                with self.subTest(action=body["action"], flag=flag, value=invalid), IsolatedConfig() as iso:
                    iso.write_registry()
                    before = iso.registry_path.read_bytes()
                    code, payload = server.dispatch_ui_action(body)
                    after = iso.registry_path.read_bytes()
                    expected_message = f"argument {flag}: expected one argument"
                    self.assertEqual(code, 2)
                    self.assertEqual(payload, {
                        "ok": False,
                        "message": expected_message,
                        "result": {
                            "status": "error",
                            "reason": "invalid_args",
                            "message": expected_message,
                        },
                    })
                    self.assertEqual(after, before)

                    success_body = {
                        key: "accepted" if value == "VALUE" else value
                        for key, value in template.items()
                    }
                    success_code, success = server.dispatch_ui_action(success_body)
                    self.assertEqual(success_code, 0, success)
                    self.assertTrue(success["ok"], success)

    def test_dashboard_negative_and_spaced_option_values_remain_accepted(self) -> None:
        from portskill import server

        templates = (
            {"action": "new-environment", "name": "VALUE"},
            {"action": "new-environment", "name": "valid", "description": "VALUE"},
            {"action": "draft-stage", "rangeId": "VALUE", "command": "run"},
            {"action": "draft-stage", "rangeId": "r1", "startScript": "VALUE"},
            {"action": "draft-stage", "rangeId": "r1", "start_script": "VALUE"},
            {"action": "draft-stage", "rangeId": "r1", "stopScript": "VALUE"},
            {"action": "draft-stage", "rangeId": "r1", "stop_script": "VALUE"},
            {"action": "draft-stage", "rangeId": "r1", "command": "VALUE"},
            {"action": "draft-stage", "rangeId": "r1", "cwd": "VALUE"},
            {"action": "draft-save", "rangeId": "VALUE"},
            {"action": "draft-revert", "rangeId": "VALUE"},
        )
        for template in templates:
            for accepted in ("-1", "-1.5", "-", "value with spaces"):
                body = {
                    key: accepted if value == "VALUE" else value
                    for key, value in template.items()
                }
                with self.subTest(action=body["action"], value=accepted), IsolatedConfig() as iso:
                    iso.write_registry()
                    code, payload = server.dispatch_ui_action(body)
                    self.assertEqual(code, 0, payload)
                    self.assertTrue(payload["ok"], payload)

    def test_dogfood_allocate_uses_domain_and_preserves_failure_policy(self) -> None:
        from portskill import server
        from portskill.domain import DomainError

        stdout = io.StringIO()
        with mock.patch.object(
            server, "_portskill_project_path", return_value="/tmp/portskill"
        ), mock.patch(
            "portskill.registry_mutations.allocate_result",
            side_effect=[
                DomainError("port_in_use", "busy"),
                {"status": "ok", "range": {"start": 23456}},
            ],
        ) as allocate, mock.patch("sys.stdout", stdout):
            failed = server._dogfood_allocate_port(prefer_start=23456)
            succeeded = server._dogfood_allocate_port(prefer_start=23456)

        self.assertIsNone(failed)
        self.assertEqual(succeeded, 23456)
        self.assertIn("dogfood allocate failed (2)", stdout.getvalue())
        for call in allocate.call_args_list:
            options = call.args[0]
            self.assertEqual(options["start"], 23456)
            self.assertEqual(options["tailnet"], "none")

    def test_mcp_has_no_subprocess_fallback_and_unknown_envelope_survives_ping(self) -> None:
        from portskill import mcp

        tree = ast.parse(pathlib.Path(mcp.__file__).read_text())
        call_tool = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_call_tool"
        )
        calls = {
            node.func.id
            for node in ast.walk(call_tool)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("run_cli", calls)

        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "not_a_tool", "arguments": {}},
        }
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        with IsolatedConfig() as iso:
            iso.write_registry()
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
        self.assertEqual(
            responses[0],
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32602, "message": "Unknown tool: not_a_tool"},
            },
        )
        self.assertEqual(
            responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}}
        )


if __name__ == "__main__":
    unittest.main()
