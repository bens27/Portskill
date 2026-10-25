"""Slice 3 command families and adapter-consumption regressions."""
from __future__ import annotations

import unittest
from unittest import mock


class Slice3RuntimeTests(unittest.TestCase):
    def test_path_binder_preserves_keyword_coercions(self) -> None:
        from portskill.command_runtime import execute_command
        from portskill.command_specs import PATH_COMMAND

        with mock.patch(
            "portskill.path.run_path", return_value={"status": "ok"}
        ) as run_path:
            self.assertEqual(
                execute_command(PATH_COMMAND, {"mode": "status", "count": 0}),
                {"status": "ok"},
            )

        run_path.assert_called_once_with(
            mode="status",
            project=".",
            count=1,
            range_id=None,
            note=None,
            tailnet=None,
            command=None,
            cwd=None,
            default_state=None,
            start=None,
            environment=None,
        )

    def test_path_binder_converts_needs_input_to_exit_three(self) -> None:
        from portskill.command_runtime import execute_command
        from portskill.command_specs import PATH_COMMAND
        from portskill.domain import DomainError

        payload = {"status": "needs_input", "needs_input": True, "input": "login"}
        with mock.patch("portskill.path.run_path", return_value=payload):
            with self.assertRaises(DomainError) as raised:
                execute_command(PATH_COMMAND, {"mode": "start"})

        self.assertEqual(raised.exception.exit_code, 3)
        self.assertEqual(raised.exception.as_dict(), payload)

    def test_apply_defaults_binder_preserves_named_call(self) -> None:
        from portskill.command_runtime import execute_command
        from portskill.command_specs import APPLY_DEFAULTS_COMMAND

        with mock.patch(
            "portskill.apply_defaults.apply_defaults_result",
            return_value={"status": "ok"},
        ) as apply_defaults:
            execute_command(
                APPLY_DEFAULTS_COMMAND,
                {"project": "/tmp/app", "also_stop_off": 1},
            )

        apply_defaults.assert_called_once_with(
            project_filter=["/tmp/app"],
            also_stop_off=True,
            session_preset=None,
            session_source="apply_defaults",
            record_session=True,
            enforce_overlap=True,
        )


class Slice3ProjectionTests(unittest.TestCase):
    def test_migrated_mcp_specs_occupy_frozen_positions(self) -> None:
        from portskill.command_specs import (
            APPLY_DEFAULTS_COMMAND,
            DEACTIVATE_COMMAND,
            EXIT_HOUSE_COMMAND,
            PATH_COMMAND,
            SET_DEFAULT_COMMAND,
            START_COMMAND,
            STOP_COMMAND,
        )
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        specs = (
            START_COMMAND,
            STOP_COMMAND,
            PATH_COMMAND,
            SET_DEFAULT_COMMAND,
            APPLY_DEFAULTS_COMMAND,
            DEACTIVATE_COMMAND,
            EXIT_HOUSE_COMMAND,
        )
        for spec in specs:
            with self.subTest(spec=spec.id):
                self.assertEqual(
                    TOOL_DEFS[spec.mcp.catalog_position],
                    _mcp_tool_definition(spec),
                )

    def test_bulk_flags_remain_cli_only_and_lean_policy_is_generated(self) -> None:
        from portskill.command_specs import START_COMMAND, STOP_COMMAND
        from portskill.mcp_catalog import LEAN_MCP_TOOLS_ENABLED, TOOL_DEFS

        start_schema = TOOL_DEFS[START_COMMAND.mcp.catalog_position]["inputSchema"]
        stop_schema = TOOL_DEFS[STOP_COMMAND.mcp.catalog_position]["inputSchema"]
        self.assertNotIn("all", start_schema["properties"])
        self.assertNotIn("reclaim", start_schema["properties"])
        self.assertNotIn("all", stop_schema["properties"])
        self.assertNotIn("non_default", stop_schema["properties"])
        self.assertIn("stop", LEAN_MCP_TOOLS_ENABLED)
        self.assertIn("portskill", LEAN_MCP_TOOLS_ENABLED)

    def test_ui_reclaim_consumes_declared_binder_and_fixed_override(self) -> None:
        from portskill import server

        declared = mock.Mock(wraps=server._bind_ui_reclaim_start)
        with mock.patch.dict(
            server._UI_PAYLOAD_BINDERS, {"reclaim_start": declared}
        ), mock.patch(
            "portskill.process_commands.start_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as start:
            code, response = server.dispatch_ui_action({
                "action": "reclaim-start",
                "project": "/tmp/app",
                "rangeId": "r1",
                "reclaim": False,
            })

        self.assertEqual(code, 0)
        expected = {"range_id": "r1", "project": "/tmp/app", "reclaim": True}
        self.assertEqual(response["result"]["options"], expected)
        declared.assert_called_once()
        start.assert_called_once_with(expected)

    def test_ui_set_default_consumes_declared_fields_and_coercions(self) -> None:
        from dataclasses import replace

        from portskill import server
        from portskill.command_specs import SET_DEFAULT_COMMAND

        replacements = {
            "range_id": ("alternateRange",),
            "state": ("alternateState",),
            "project": ("alternateProject",),
        }
        params = tuple(
            replace(param, ui=replace(param.ui, fields=replacements[param.key]))
            if param.key in replacements
            else param
            for param in SET_DEFAULT_COMMAND.params
        )
        original_params = SET_DEFAULT_COMMAND.params
        object.__setattr__(SET_DEFAULT_COMMAND, "params", params)
        try:
            with mock.patch(
                "portskill.defaults.set_default_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as set_default:
                code, response = server.dispatch_ui_action({
                    "action": "set-default",
                    "rangeId": "guard-value",
                    "state": "on",
                    "project": "/tmp/ignored",
                    "alternateRange": "declared-range",
                    "alternateState": "off",
                    "alternateProject": "",
                })
        finally:
            object.__setattr__(SET_DEFAULT_COMMAND, "params", original_params)

        self.assertEqual(code, 0)
        expected = {
            "range_id": "declared-range",
            "state": "off",
            "project": None,
        }
        self.assertEqual(response["result"]["options"], expected)
        set_default.assert_called_once_with(expected)

    def test_ui_bulk_and_falsy_final_aliases_use_declared_bindings(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.process_commands.stop_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as stop:
            code, response = server.dispatch_ui_action({"action": "stop-non-default"})
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["options"], {"non_default": True})
        stop.assert_called_once_with({"non_default": True})

        with mock.patch(
            "portskill.deactivate.deactivate_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as deactivate:
            code, response = server.dispatch_ui_action({
                "action": "exit-house",
                "preset": "  daily  ",
                "alsoRelease": False,
                "also_release": False,
            })
        self.assertEqual(code, 0)
        expected = {"preset": "daily", "also_release": False}
        self.assertEqual(response["result"]["options"], expected)
        deactivate.assert_called_once_with(expected)

    def test_mcp_dispatch_consumes_specs_including_hidden_path_alias(self) -> None:
        from portskill import mcp
        from portskill.command_specs import PATH_COMMAND, START_COMMAND

        for name, expected_spec in (("start", START_COMMAND), ("portskill_path", PATH_COMMAND)):
            with self.subTest(name=name), mock.patch(
                "portskill.mcp.execute_command", return_value={"status": "ok"}
            ) as execute:
                response = mcp._call_tool(name, {})
                self.assertFalse(response["isError"])
                execute.assert_called_once_with(expected_spec, {})


if __name__ == "__main__":
    unittest.main()
