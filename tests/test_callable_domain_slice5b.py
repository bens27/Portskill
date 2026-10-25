"""Callable apply, deactivate, defaults, and history adapter contracts."""
from __future__ import annotations

import ast
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, ROOT


class CallableDomainSlice5BTests(unittest.TestCase):
    def test_domains_do_not_emit_or_reach_transport_adapters(self) -> None:
        from portskill import apply_defaults, deactivate, defaults, history

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                self.assertEqual(
                    apply_defaults.apply_defaults_result(
                        record_session=False,
                        enforce_overlap=False,
                    )["status"],
                    "ok",
                )
                self.assertEqual(
                    deactivate.deactivate_result({"clear_session": False})["status"],
                    "ok",
                )
                self.assertEqual(
                    defaults.set_defaults_from_current_result()["status"],
                    "ok",
                )
                self.assertEqual(history.history_list_result()["status"], "ok")
        self.assertEqual(stdout.getvalue(), "")

        package = pathlib.Path(apply_defaults.__file__).parent
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

        for owner in ("apply_defaults", "deactivate", "defaults", "history"):
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

    def test_mcp_slice5b_tools_are_in_process(self) -> None:
        from portskill import history, mcp
        from portskill.registry_operations import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record(
                "r1", 22100, 22100, None, "none", default_state="off"
            )
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            history.history_clear_result()
            calls = (
                ("apply_defaults", {"project": str(project)}),
                ("deactivate", {}),
                ("exit_house", {}),
                ("history_list", {}),
                ("history_restore", {"index": 0}),
                ("history_reset", {}),
                ("set_default", {
                    "project": str(project),
                    "range_id": "r1",
                    "state": "on",
                }),
            )
            results = [mcp.call_tool(name, arguments) for name, arguments in calls]

        self.assertTrue(all(not result["isError"] for result in results), results)

    def test_real_mcp_stdio_errors_each_preserve_following_ping(self) -> None:
        tools = (
            ("set_default", {"range_id": "missing", "state": "on"}),
            ("apply_defaults", {}),
            ("deactivate", {}),
            ("exit_house", {}),
            ("history_list", {}),
            ("history_restore", {"index": 0}),
            ("history_reset", {}),
        )
        with IsolatedConfig() as iso:
            iso.write_registry_raw("{broken")
            for name, arguments in tools:
                with self.subTest(name=name):
                    call = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
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
                    self.assertEqual(len(responses), 2, proc.stdout)
                    self.assertTrue(responses[0]["result"]["isError"])
                    self.assertEqual(
                        responses[1],
                        {"jsonrpc": "2.0", "id": 2, "result": {}},
                    )

    def test_http_slice5b_actions_fail_then_succeed_without_cli(self) -> None:
        from portskill import server

        cases = (
            ("portskill.history.history_restore_result", {"action": "history-restore", "index": 0}),
            ("portskill.history.history_reset_result", {"action": "history-reset"}),
            ("portskill.history.history_clear_result", {"action": "history-clear"}),
            ("portskill.defaults.set_default_result", {
                "action": "set-default",
                "rangeId": "r1",
                "state": "on",
            }),
            ("portskill.apply_defaults.apply_defaults_result", {"action": "apply-defaults"}),
            ("portskill.deactivate.deactivate_result", {"action": "deactivate"}),
            ("portskill.deactivate.deactivate_result", {"action": "exit-house"}),
            ("portskill.defaults.set_defaults_from_current_result", {
                "action": "set-defaults-from-current",
            }),
            ("portskill.defaults.set_defaults_from_current_result", {"action": "capture-defaults"}),
        )
        for operation_path, body in cases:
            with self.subTest(action=body["action"]), mock.patch(
                operation_path,
                side_effect=[RuntimeError("boom"), {"status": "ok"}],
            ):
                failed_code, failed = server.dispatch_ui_action(body)
                success_code, success = server.dispatch_ui_action(body)

            self.assertEqual(failed_code, 1)
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["message"], "boom")
            self.assertEqual(success_code, 0)
            self.assertTrue(success["ok"])
            self.assertEqual(success["result"], {"status": "ok"})

    def test_http_slice5b_real_domains_recover_after_failures(self) -> None:
        from portskill import server
        from portskill.history import history_clear_result
        from portskill.registry_operations import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record(
                "r1", 22120, 22120, None, "none", default_state="off"
            )
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            failed = server.dispatch_ui_action({
                "action": "set-default",
                "rangeId": "missing",
                "state": "on",
            })
            succeeded = server.dispatch_ui_action({
                "action": "set-default",
                "rangeId": "r1",
                "state": "on",
            })
            self.assertFalse(failed[1]["ok"])
            self.assertTrue(succeeded[1]["ok"])

        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.dict(os.environ, {"PORTSKILL_POOL_START": "bad"}):
                failed = server.dispatch_ui_action({"action": "apply-defaults"})
            succeeded = server.dispatch_ui_action({"action": "apply-defaults"})
            self.assertFalse(failed[1]["ok"])
            self.assertTrue(succeeded[1]["ok"])

        with IsolatedConfig() as iso:
            iso.write_registry()
            failed = server.dispatch_ui_action({
                "action": "deactivate",
                "preset": "missing",
            })
            succeeded = server.dispatch_ui_action({"action": "deactivate"})
            self.assertFalse(failed[1]["ok"])
            self.assertTrue(succeeded[1]["ok"])

        for action in ("history-restore", "history-reset"):
            with self.subTest(action=action), IsolatedConfig() as iso:
                iso.write_registry()
                body = {"action": action}
                if action == "history-restore":
                    body["index"] = 0
                failed = server.dispatch_ui_action(body)
                history_clear_result()
                succeeded = server.dispatch_ui_action(body)
                self.assertFalse(failed[1]["ok"])
                self.assertTrue(succeeded[1]["ok"])

        for action in ("history-clear", "set-defaults-from-current"):
            with self.subTest(action=action), IsolatedConfig() as iso:
                iso.write_registry_raw("{broken")
                failed = server.dispatch_ui_action({"action": action})
                iso.write_registry()
                succeeded = server.dispatch_ui_action({"action": action})
                self.assertFalse(failed[1]["ok"])
                self.assertTrue(succeeded[1]["ok"])

    def test_cli_slice5b_error_and_history_list_contracts(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            cases = (
                (
                    ["set-default", "--range-id", "missing", "--state", "on"],
                    2,
                    '{"message":"range id not found: missing","reason":"not_found",'
                    '"status":"error"}\n',
                    None,
                ),
                (
                    ["deactivate", "--preset", "missing"],
                    2,
                    '{"message":"preset not found: missing","reason":"preset_not_found",'
                    '"status":"error"}\n',
                    None,
                ),
                (
                    ["history", "restore", "--index", "0"],
                    2,
                    '{"message":"no history for environment __all__",'
                    '"reason":"history_empty","status":"error"}\n',
                    None,
                ),
                (
                    ["history", "reset"],
                    2,
                    '{"message":"no history for environment __all__",'
                    '"reason":"history_empty","status":"error"}\n',
                    None,
                ),
                (
                    ["apply-defaults"],
                    2,
                    '{"message":"pool bounds must be integers",'
                    '"reason":"invalid_config","status":"error"}\n',
                    {"PORTSKILL_POOL_START": "bad"},
                ),
                (
                    ["history", "list"],
                    0,
                    '{"baseline":false,"baselineLabel":"Original","cursor":0,'
                    '"environment":"__all__","modified":false,"status":"ok",'
                    '"step_count":0,"steps":[]}\n',
                    None,
                ),
            )
            for argv, returncode, stdout, extra in cases:
                with self.subTest(argv=argv):
                    proc = iso.run_cli(argv, extra=extra)
                    self.assertEqual(proc.returncode, returncode, proc.stderr)
                    self.assertEqual(proc.stdout, stdout)
                    self.assertEqual(proc.stderr, "")

    def test_history_restore_and_reset_persist_original_snapshot(self) -> None:
        from portskill.defaults import set_default_result
        from portskill.history import (
            history_clear_result,
            history_list_result,
            history_reset_result,
            history_restore_result,
        )
        from portskill.registry_operations import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            item = range_record(
                "r1", 22110, 22110, None, "none", default_state="off"
            )
            iso.write_registry({
                "projects": {str(project.resolve()): {"ranges": [item]}},
            })
            history_clear_result()
            set_default_result({"range_id": "r1", "state": "on"})
            listed = history_list_result()
            restored = history_restore_result({"index": 0})
            set_default_result({"range_id": "r1", "state": "on"})
            reset = history_reset_result()
            persisted = json.loads(iso.registry_path.read_text())

        self.assertEqual(listed["step_count"], 1)
        self.assertTrue(listed["modified"])
        self.assertEqual(restored["index"], 0)
        self.assertEqual(reset["index"], 0)
        saved = persisted["projects"][str(project.resolve())]["ranges"][0]
        self.assertEqual(saved["default_state"], "off")

    def test_deactivate_continues_after_item_failure_in_both_modes(self) -> None:
        from portskill import deactivate

        project = "/tmp/portskill-deactivate-project"
        for also_release in (False, True):
            first = {"id": "r1", "state": "active", "default_state": "on"}
            second = {"id": "r2", "state": "active", "default_state": "on"}
            captured = {}

            def run_locked(mutator):
                payload, changed_projects = mutator({})
                captured["changed_projects"] = changed_projects
                return payload

            with self.subTest(also_release=also_release), mock.patch.object(
                deactivate, "locked_registry", side_effect=run_locked
            ), mock.patch.object(
                deactivate,
                "iter_project_ranges",
                return_value=[(project, first), (project, second)],
            ), mock.patch.object(
                deactivate, "range_has_live_process", return_value=True
            ), mock.patch.object(
                deactivate,
                "park_item_for_exit",
                side_effect=[
                    (False, {"reason": "failed", "range": first}),
                    (True, {"action": "stopped", "range": second}),
                ],
            ) as park:
                result = deactivate.deactivate_result({
                    "also_release": also_release,
                    "clear_session": False,
                })

            self.assertEqual(result["error_count"], 1)
            self.assertEqual(result["stopped_count"], 1)
            self.assertEqual(captured["changed_projects"], [project])
            self.assertEqual(
                [call.kwargs["also_release"] for call in park.call_args_list],
                [also_release, also_release],
            )


if __name__ == "__main__":
    unittest.main()
