"""Callable preset, environment, and compatibility adapter contracts."""
from __future__ import annotations

import ast
import io
import json
import pathlib
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class CallableDomainSlice5ATests(unittest.TestCase):
    def test_domain_modules_do_not_depend_on_cli_or_emit(self) -> None:
        from portskill import (
            apply_defaults,
            compatibility,
            environment_files,
            environments,
            presets,
            sessions,
        )

        with IsolatedConfig() as iso:
            iso.write_registry()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                report = compatibility.compatibility_result({"include_default_on": True})
                exported = environments.environment_export_result({"name": "workspace"})
                listed = presets.preset_list_result()

        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(report["compatible"])
        self.assertEqual(exported["kind"], "portskill-environment")
        self.assertEqual(listed, {"status": "ok", "presets": [], "count": 0})
        modules = (
            apply_defaults,
            compatibility,
            environment_files,
            environments,
            presets,
            sessions,
        )
        for module in modules:
            imports = {
                node.module
                for node in ast.walk(ast.parse(pathlib.Path(module.__file__).read_text()))
                if isinstance(node, ast.ImportFrom) and node.level == 1
            }
            self.assertNotIn("cli", imports, module.__name__)

        dependency_graph = {
            module.__name__.removeprefix("portskill."): {
                node.module.split(".", 1)[0]
                for node in ast.walk(ast.parse(pathlib.Path(module.__file__).read_text()))
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
            }
            for module in modules
        }
        slice_names = set(dependency_graph)
        dependency_graph = {
            name: dependencies & slice_names
            for name, dependencies in dependency_graph.items()
        }

        visiting = set()
        visited = set()

        def visit(name):
            self.assertNotIn(name, visiting, f"slice 5A import cycle at {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in dependency_graph[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in dependency_graph:
            visit(name)

    def test_mcp_slice5a_tools_are_in_process(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            environment = iso.root / "environment.json"
            environment.write_text(json.dumps({
                "kind": "portskill-environment",
                "name": "empty",
                "services": [],
            }))
            iso.write_registry()
            calls = (
                ("environment_export", {"name": "workspace"}),
                ("environment_import", {"file": str(environment)}),
                ("compat_check", {"include_default_on": True}),
                ("preset_save", {"name": "empty", "project": [str(project)]}),
                ("preset_list", {}),
                ("preset_apply", {"name": "empty"}),
                ("preset_delete", {"name": "empty"}),
            )
            with mock.patch.object(
                mcp, "run_cli", side_effect=AssertionError("subprocess used")
            ):
                results = [mcp.call_tool(name, args) for name, args in calls]

        self.assertTrue(all(not result["isError"] for result in results), results)

    def test_mcp_preset_error_is_contained_then_ping_succeeds(self) -> None:
        from portskill import mcp

        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "preset_apply", "arguments": {"name": "missing"},
        }}
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()
        with IsolatedConfig() as iso:
            iso.write_registry()
            with mock.patch.object(
                mcp, "run_cli", side_effect=AssertionError("subprocess used")
            ), mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(
                mcp.sys, "stdout", stdout
            ):
                self.assertEqual(mcp.mcp_stdio_loop(), 0)

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(
            responses[0]["result"]["structuredContent"]["reason"],
            "preset_not_found",
        )
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_mcp_incompatible_reports_keep_legacy_error_envelope(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            first_project = iso.root / "first"
            second_project = iso.root / "second"
            first_project.mkdir()
            second_project.mkdir()
            iso.write_registry({
                "projects": {
                    str(first_project): {"ranges": [{
                        "id": "first",
                        "start": 21000,
                        "end": 21000,
                        "state": "reserved",
                        "default_state": "on",
                    }]},
                    str(second_project): {"ranges": [{
                        "id": "second",
                        "start": 21000,
                        "end": 21000,
                        "state": "reserved",
                        "default_state": "on",
                    }]},
                },
            })
            conflict = mcp.call_tool("compat_check", {"include_default_on": True})
            missing = mcp.call_tool("compat_check", {"preset": "missing"})

        for result in (conflict, missing):
            self.assertTrue(result["isError"])
            self.assertFalse(result["structuredContent"]["ok"])
            self.assertEqual(
                json.loads(result["content"][0]["text"]),
                result["structuredContent"],
            )
        self.assertEqual(conflict["structuredContent"]["conflict_count"], 1)
        self.assertEqual(missing["structuredContent"]["missing_presets"], ["missing"])

    def test_mcp_compatible_report_shape_is_unchanged(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            iso.write_registry()
            result = mcp.call_tool("compat_check", {"include_default_on": True})

        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["compatible"])
        self.assertNotIn("ok", result["structuredContent"])

    def test_http_slice5a_actions_are_in_process(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            iso.write_registry()
            environment = json.dumps({
                "kind": "portskill-environment", "name": "empty", "services": [],
            })
            actions = (
                {"action": "compat-check"},
                {"action": "export-environment", "name": "workspace"},
                {"action": "import-environment", "content": environment},
                {"action": "preset-save", "name": "empty"},
                {"action": "preset-apply", "name": "empty"},
                {"action": "preset-delete", "name": "empty"},
            )
            with mock.patch.object(
                server, "run_cli", side_effect=AssertionError("subprocess used")
            ):
                results = [server.dispatch_ui_action(action) for action in actions]

        for code, body in results:
            self.assertEqual(code, 0, body)
            self.assertTrue(body["ok"], body)

    def test_http_domain_error_keeps_legacy_envelope(self) -> None:
        from portskill import server
        from portskill.domain import DomainError

        with mock.patch(
            "portskill.environments.environment_export_result",
            side_effect=DomainError("invalid_environment", "bad environment"),
        ):
            code, body = server.dispatch_ui_action({
                "action": "export-environment",
                "name": "broken",
            })

        self.assertEqual(code, 2)
        self.assertEqual(body, {
            "ok": False,
            "message": "bad environment",
            "result": {
                "status": "error",
                "reason": "invalid_environment",
                "message": "bad environment",
            },
        })

    def test_environment_import_preserves_project_path_exceptions(self) -> None:
        from portskill import environments
        from portskill.domain import DomainError

        with mock.patch.object(
            environments,
            "project_path",
            side_effect=DomainError("invalid_project", "bad project"),
        ):
            with self.assertRaisesRegex(DomainError, "bad project"):
                environments.import_services_into_registry(
                    {}, [{"project": "broken", "count": 1}]
                )

    def test_launch_preset_contains_apply_failure_as_warning(self) -> None:
        from portskill import presets
        from portskill.domain import DomainError

        with IsolatedConfig() as iso:
            iso.write_registry({
                "presets": {
                    "empty": {
                        "name": "empty",
                        "description": "",
                        "services": [],
                    },
                },
            })
            with mock.patch.object(
                presets,
                "apply_defaults_result",
                side_effect=DomainError("apply_failed", "start failed"),
            ):
                result = presets.preset_apply_for_launch("empty")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["preset"], "empty")
        self.assertTrue(result["auto_apply"])
        self.assertEqual(result["apply_defaults"], {
            "status": "error",
            "reason": "apply_defaults_failed",
            "message": "start failed",
        })
        self.assertEqual(result["warnings"], ["start failed"])

    def test_apply_defaults_persists_skipped_details_with_a_range(self) -> None:
        from portskill import apply_defaults

        project = "/tmp/portskill-skipped-project"
        item = {
            "id": "range-1",
            "state": "reserved",
            "default_state": "on",
        }
        captured = {}

        def run_locked(mutator):
            payload, changed_projects = mutator({})
            captured["changed_projects"] = changed_projects
            return payload

        with mock.patch.object(
            apply_defaults, "locked_registry", side_effect=run_locked
        ), mock.patch.object(
            apply_defaults, "iter_project_ranges", return_value=[(project, item)]
        ), mock.patch.object(
            apply_defaults, "range_has_live_process", return_value=False
        ), mock.patch.object(
            apply_defaults,
            "start_item_for_apply",
            return_value=(False, {"skipped": True, "range": item}),
        ):
            result = apply_defaults.apply_defaults_result(
                record_session=False,
                enforce_overlap=False,
            )

        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(captured["changed_projects"], [project])

    def test_cli_compat_and_preset_errors_keep_exit_contract(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            missing = iso.run_cli(["preset", "apply", "--name", "missing"])
            compatible = iso.run_cli(["compat", "check", "--include-default-on"])

        self.assertEqual(missing.returncode, 2)
        self.assertEqual(
            missing.stdout,
            '{"message":"preset not found: missing","reason":"preset_not_found",'
            '"status":"error"}\n',
        )
        self.assertEqual(compatible.returncode, 0, compatible.stderr)
        self.assertTrue(json.loads(compatible.stdout)["compatible"])


if __name__ == "__main__":
    unittest.main()
