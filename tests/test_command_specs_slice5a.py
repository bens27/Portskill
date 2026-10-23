"""CommandSpec migration contracts for history and workspace families."""
from __future__ import annotations

import ast
from dataclasses import replace
import pathlib
import unittest
from unittest import mock


class Slice5AStructureTests(unittest.TestCase):
    def test_all_eight_specs_are_fixed_catalog_members(self) -> None:
        from portskill.command_specs import (
            COMMAND_SPECS,
            HISTORY_CLEAR_COMMAND,
            HISTORY_LIST_COMMAND,
            HISTORY_RESET_COMMAND,
            HISTORY_RESTORE_COMMAND,
            WORKSPACE_DRAFT_REVERT_COMMAND,
            WORKSPACE_DRAFT_SAVE_COMMAND,
            WORKSPACE_DRAFT_SET_COMMAND,
            WORKSPACE_EDIT_MODE_COMMAND,
        )

        expected = {
            HISTORY_LIST_COMMAND: ("portskill.history", "history_list_result"),
            HISTORY_RESTORE_COMMAND: ("portskill.history", "history_restore_result"),
            HISTORY_RESET_COMMAND: ("portskill.history", "history_reset_result"),
            HISTORY_CLEAR_COMMAND: ("portskill.history", "history_clear_result"),
            WORKSPACE_DRAFT_SET_COMMAND: (
                "portskill.workspace",
                "workspace_draft_set_result",
            ),
            WORKSPACE_DRAFT_SAVE_COMMAND: (
                "portskill.workspace",
                "workspace_draft_save_result",
            ),
            WORKSPACE_DRAFT_REVERT_COMMAND: (
                "portskill.workspace",
                "workspace_draft_revert_result",
            ),
            WORKSPACE_EDIT_MODE_COMMAND: (
                "portskill.workspace",
                "workspace_edit_mode_result",
            ),
        }
        for spec, handler in expected.items():
            with self.subTest(spec=spec.id):
                self.assertIn(spec, COMMAND_SPECS)
                self.assertEqual((spec.handler.module, spec.handler.symbol), handler)

    def test_adapters_do_not_call_migrated_owners_directly(self) -> None:
        package = pathlib.Path(__file__).parents[1] / "portskill"
        forbidden = {
            "history_list_result",
            "history_restore_result",
            "history_reset_result",
            "history_clear_result",
            "workspace_draft_set_result",
            "workspace_draft_save_result",
            "workspace_draft_revert_result",
            "workspace_edit_mode_result",
        }
        for filename in ("cli.py", "mcp.py", "server.py"):
            tree = ast.parse((package / filename).read_text())
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse(called & forbidden, (filename, called & forbidden))


class Slice5AProjectionTests(unittest.TestCase):
    def test_history_mcp_specs_occupy_frozen_positions(self) -> None:
        from portskill.command_specs import (
            HISTORY_LIST_COMMAND,
            HISTORY_RESET_COMMAND,
            HISTORY_RESTORE_COMMAND,
        )
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        for spec in (
            HISTORY_LIST_COMMAND,
            HISTORY_RESTORE_COMMAND,
            HISTORY_RESET_COMMAND,
        ):
            with self.subTest(spec=spec.id):
                self.assertEqual(
                    TOOL_DEFS[spec.mcp.catalog_position],
                    _mcp_tool_definition(spec),
                )

    def test_cli_parsers_are_generated_from_family_specs(self) -> None:
        from portskill.cli import parser

        restored = parser().parse_args(
            ["history", "restore", "--index", "2", "--environment", " dev "]
        )
        self.assertEqual(restored.history_command, "restore")
        self.assertEqual(restored.index, 2)
        self.assertEqual(restored.environment, " dev ")

        staged = parser().parse_args(
            [
                "workspace",
                "draft-set",
                "--range-id",
                "r1",
                "--start-script",
                "start.sh",
            ]
        )
        self.assertEqual(staged.workspace_command, "draft-set")
        self.assertEqual(staged.range_id, "r1")
        self.assertEqual(staged.start_script, "start.sh")

        edit = parser().parse_args(["workspace", "edit-mode"])
        self.assertEqual(edit.state, "on")

    def test_history_mcp_dispatch_uses_specs(self) -> None:
        from portskill import mcp
        from portskill.command_specs import (
            HISTORY_LIST_COMMAND,
            HISTORY_RESET_COMMAND,
            HISTORY_RESTORE_COMMAND,
        )

        cases = (
            ("history_list", {}, HISTORY_LIST_COMMAND),
            ("history_restore", {"index": 0}, HISTORY_RESTORE_COMMAND),
            ("history_reset", {}, HISTORY_RESET_COMMAND),
        )
        for name, arguments, spec in cases:
            with self.subTest(name=name), mock.patch(
                "portskill.mcp.execute_command", return_value={"status": "ok"}
            ) as execute:
                response = mcp._call_tool(name, arguments)
            execute.assert_called_once_with(spec, arguments)
            self.assertFalse(response["isError"])


class Slice5AUIBindingTests(unittest.TestCase):
    @staticmethod
    def _replace_ui_fields(spec, key, fields):
        original = spec.params
        object.__setattr__(
            spec,
            "params",
            tuple(
                replace(param, ui=replace(param.ui, fields=fields))
                if param.key == key
                else param
                for param in original
            ),
        )
        return original

    @staticmethod
    def _replace_ui_coercion(spec, key, coercion):
        original = spec.params
        object.__setattr__(
            spec,
            "params",
            tuple(
                replace(param, ui=replace(param.ui, coercion=coercion))
                if param.key == key
                else param
                for param in original
            ),
        )
        return original

    def test_history_metadata_mutation_drives_real_dispatch(self) -> None:
        from portskill import server
        from portskill.command_specs import (
            HISTORY_CLEAR_COMMAND,
            HISTORY_RESTORE_COMMAND,
        )

        original_index = self._replace_ui_fields(
            HISTORY_RESTORE_COMMAND, "index", ("alternateIndex",)
        )
        original_environment = self._replace_ui_fields(
            HISTORY_RESTORE_COMMAND, "environment", ("alternateEnvironment",)
        )
        try:
            with mock.patch(
                "portskill.history.history_restore_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {
                        "action": "history-restore",
                        "index": "guard",
                        "alternateIndex": "3",
                        "environment": "guard",
                        "alternateEnvironment": "  declared  ",
                    }
                )
        finally:
            object.__setattr__(HISTORY_RESTORE_COMMAND, "params", original_index)
        self.assertEqual(code, 0, response)
        owner.assert_called_once_with({"index": 3, "environment": "declared"})

        original = self._replace_ui_fields(
            HISTORY_CLEAR_COMMAND, "environment", ("alternateEnvironment",)
        )
        try:
            with mock.patch(
                "portskill.history.history_clear_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {
                        "action": "history-clear",
                        "environment": "guard",
                        "alternateEnvironment": "  declared  ",
                    }
                )
        finally:
            object.__setattr__(HISTORY_CLEAR_COMMAND, "params", original)
        self.assertEqual(code, 0, response)
        owner.assert_called_once_with({"environment": "declared"})

    def test_workspace_metadata_mutation_drives_real_dispatch(self) -> None:
        from portskill import server
        from portskill.command_specs import WORKSPACE_DRAFT_SET_COMMAND

        original_range = self._replace_ui_fields(
            WORKSPACE_DRAFT_SET_COMMAND, "range_id", ("alternateRange",)
        )
        original_command = self._replace_ui_fields(
            WORKSPACE_DRAFT_SET_COMMAND, "command", ("alternateCommand",)
        )
        try:
            with mock.patch(
                "portskill.workspace.workspace_draft_set_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {
                        "action": "draft-stage",
                        "rangeId": "guard",
                        "alternateRange": " declared ",
                        "command": "guard",
                        "alternateCommand": "run",
                    }
                )
        finally:
            object.__setattr__(WORKSPACE_DRAFT_SET_COMMAND, "params", original_range)
        self.assertEqual(code, 0, response)
        owner.assert_called_once_with({"range_id": "declared", "command": "run"})

    def test_workspace_coercion_mutation_drives_real_dispatch(self) -> None:
        from portskill import server
        from portskill.command_specs import WORKSPACE_DRAFT_SET_COMMAND

        original = self._replace_ui_coercion(
            WORKSPACE_DRAFT_SET_COMMAND, "command", "environment_name"
        )
        try:
            with mock.patch(
                "portskill.workspace.workspace_draft_set_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {
                        "action": "draft-stage",
                        "rangeId": "r1",
                        "command": "  run  ",
                    }
                )
        finally:
            object.__setattr__(WORKSPACE_DRAFT_SET_COMMAND, "params", original)
        self.assertEqual(code, 0, response)
        owner.assert_called_once_with({"range_id": "r1", "command": "run"})

    def test_workspace_aliases_keep_first_present_string(self) -> None:
        from portskill import server

        cases = (
            ({"startScript": "", "start_script": "fallback"}, ""),
            ({"start_script": ""}, ""),
            ({"stopScript": "", "stop_script": "fallback"}, ""),
            ({"stop_script": ""}, ""),
        )
        for aliases, expected in cases:
            with self.subTest(aliases=aliases), mock.patch(
                "portskill.workspace.workspace_draft_set_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action(
                    {"action": "draft-stage", "rangeId": "r1", **aliases}
                )
            self.assertEqual(code, 0, response)
            key = "start_script" if "start_script" in aliases or "startScript" in aliases else "stop_script"
            self.assertEqual(owner.call_args.args[0][key], expected)

    def test_edit_mode_ui_remains_intentional_ignored_legacy_action(self) -> None:
        from portskill import server

        self.assertNotIn("edit-mode", server.UI_COMMAND_SPECS)
        self.assertEqual(
            server.dispatch_ui_action({"action": "edit-mode", "state": "off"}),
            (
                200,
                {
                    "ok": True,
                    "ignored": True,
                    "message": "edit-mode is CLI-only; use per-card Edit",
                },
            ),
        )

    def test_workspace_capture_defaults_keeps_existing_command_spec(self) -> None:
        from portskill import cli
        from portskill.command_specs import SET_DEFAULTS_FROM_CURRENT_COMMAND

        args = cli.parser().parse_args(
            ["workspace", "capture-defaults", "--environment", "dev"]
        )
        with mock.patch.object(
            cli, "execute_command", return_value={"status": "ok"}
        ) as execute, mock.patch.object(cli, "emit"):
            args.func(args)
        execute.assert_called_once_with(SET_DEFAULTS_FROM_CURRENT_COMMAND, vars(args))


if __name__ == "__main__":
    unittest.main()
