"""CommandSpec migration contracts for auth, handoff, and held surfaces."""
from __future__ import annotations

import ast
from dataclasses import replace
import pathlib
import unittest
from unittest import mock


class Slice5CStructureTests(unittest.TestCase):
    def test_family_specs_are_fixed_catalog_members(self) -> None:
        from portskill.command_specs import (
            COMMAND_SPECS,
            HANDOFF_FETCH_COMMAND,
            HANDOFF_STATUS_COMMAND,
            HTTP_AUTH_GATE_COMMAND,
            HTTP_AUTH_PASSKEYS_COMMAND,
            MACHINE_COMMAND,
            SCHEDULE_COMMAND,
        )

        for spec in (
            HTTP_AUTH_GATE_COMMAND,
            HTTP_AUTH_PASSKEYS_COMMAND,
            HANDOFF_FETCH_COMMAND,
            HANDOFF_STATUS_COMMAND,
            MACHINE_COMMAND,
            SCHEDULE_COMMAND,
        ):
            self.assertIn(spec, COMMAND_SPECS)

    def test_adapters_do_not_call_migrated_owners_directly(self) -> None:
        package = pathlib.Path(__file__).parents[1] / "portskill"
        forbidden = {
            "http_auth_result",
            "fetch_kit_result",
            "call_handoff_tool",
            "run_package_sh",
            "run_codex_install",
            "bundled_skill_text",
            "preset_save_result",
            "machine_unavailable_result",
            "schedule_result",
        }
        for filename in ("cli.py", "mcp.py", "server.py"):
            tree = ast.parse((package / filename).read_text())
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse(called & forbidden, (filename, called & forbidden))


class Slice5CProjectionTests(unittest.TestCase):
    def test_cli_auth_handoff_schedule_and_machine_are_generated(self) -> None:
        from portskill.cli import parser

        gate = parser().parse_args(["http-auth", "gate", "on"])
        self.assertEqual((gate.http_auth_command, gate.gate_mode), ("gate", "on"))
        fetch = parser().parse_args(["handoff", "fetch", "--ref", "v1"])
        self.assertEqual((fetch.handoff_command, fetch.ref), ("fetch", "v1"))
        busy = parser().parse_args(["busy", "--focus", "dev"])
        self.assertEqual(busy.focus, "dev")
        machine = parser().parse_args(["machine", "add", "host"])
        self.assertEqual(machine.machine_args, ["add", "host"])

    def test_handoff_mcp_catalog_and_dispatch_use_specs(self) -> None:
        from portskill import mcp
        from portskill.command_specs import HANDOFF_LIST_COMMAND
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        self.assertEqual(
            TOOL_DEFS[HANDOFF_LIST_COMMAND.mcp.catalog_position],
            _mcp_tool_definition(HANDOFF_LIST_COMMAND),
        )
        envelope = {
            "content": [{"type": "text", "text": "{}"}],
            "structuredContent": {"ok": True},
            "isError": False,
        }
        with mock.patch.object(mcp, "_load_settings_from_registry", return_value={
            "handoff_enabled": True,
        }), mock.patch.object(mcp, "execute_command", return_value=envelope) as execute:
            response = mcp._call_tool("handoff_list", {"project": "/tmp/p"})
        execute.assert_called_once_with(HANDOFF_LIST_COMMAND, {"project": "/tmp/p"})
        self.assertIs(response, envelope)


class Slice5CUIBindingTests(unittest.TestCase):
    @staticmethod
    def _replace_ui(spec, key, **changes):
        original = spec.params
        object.__setattr__(
            spec,
            "params",
            tuple(
                replace(param, ui=replace(param.ui, **changes))
                if param.key == key
                else param
                for param in original
            ),
        )
        return original

    def test_handoff_fetch_metadata_mutation_drives_dispatch(self) -> None:
        from portskill import server
        from portskill.command_specs import HANDOFF_FETCH_COMMAND

        original = self._replace_ui(
            HANDOFF_FETCH_COMMAND,
            "ref",
            fields=("alternateRef",),
            coercion="environment_name",
        )
        try:
            with mock.patch(
                "portskill.handoff.fetch_kit_result",
                side_effect=lambda options: {"ok": True, "options": options},
            ) as owner:
                code, body = server.dispatch_ui_action({
                    "action": "handoff-fetch",
                    "ref": "guard",
                    "alternateRef": "  v1  ",
                })
        finally:
            object.__setattr__(HANDOFF_FETCH_COMMAND, "params", original)
        self.assertEqual(code, 0, body)
        owner.assert_called_once_with({"ref": "v1"})

    def test_handoff_fetch_preserves_nonempty_ref_whitespace(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.handoff.fetch_kit_result",
            side_effect=lambda options: {"ok": True, "options": options},
        ) as owner:
            code, body = server.dispatch_ui_action({
                "action": "handoff-fetch",
                "ref": "  v1  ",
            })
        self.assertEqual(code, 0, body)
        owner.assert_called_once_with({"ref": "  v1  "})

    def test_handoff_fetch_treats_blank_ref_as_missing(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.handoff.fetch_kit_result",
            side_effect=lambda options: {"ok": True, "options": options},
        ) as owner:
            code, body = server.dispatch_ui_action({
                "action": "handoff-fetch",
                "ref": "   ",
            })
        self.assertEqual(code, 0, body)
        owner.assert_called_once_with({"ref": None})

    def test_new_environment_metadata_drives_real_owner(self) -> None:
        from portskill import server
        from portskill.command_specs import PRESET_SAVE_COMMAND

        original = self._replace_ui(
            PRESET_SAVE_COMMAND,
            "description",
            fields=("alternateDescription",),
            coercion="environment_name",
        )
        try:
            with mock.patch(
                "portskill.presets.preset_save_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, body = server.dispatch_ui_action({
                    "action": "new-environment",
                    "name": "dev",
                    "description": "guard",
                    "alternateDescription": "  declared  ",
                })
        finally:
            object.__setattr__(PRESET_SAVE_COMMAND, "params", original)
        self.assertEqual(code, 0, body)
        owner.assert_called_once_with({
            "name": "dev", "description": "declared", "empty": True
        })

    def test_new_environment_non_string_description_stays_empty(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.presets.preset_save_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as owner:
            code, body = server.dispatch_ui_action({
                "action": "new-environment",
                "name": "dev",
                "description": 123,
            })
        self.assertEqual(code, 0, body)
        owner.assert_called_once_with({
            "name": "dev", "description": "", "empty": True
        })

    def test_held_machine_ui_uses_declared_owner(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.machine_commands.machine_unavailable_result",
            return_value={"ok": False, "message": "held"},
        ) as owner:
            response = server.dispatch_ui_action({"action": "machine-add"})
        owner.assert_called_once_with({})
        self.assertEqual(response, (400, {"ok": False, "message": "held"}))


if __name__ == "__main__":
    unittest.main()
