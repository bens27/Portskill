"""Slice 7: obsolete translators are gone and every surface is covered by specs."""
from __future__ import annotations

import unittest
from unittest import mock

from tests.test_command_specs_slice1 import _cli_choice_paths, _ui_action_names

# UI actions handled in dispatch_ui_action without a command spec, on purpose:
# edit-mode is a CLI-only no-op answer for the UI, and the dry run validates
# chain steps without executing a command.
UI_ACTIONS_WITHOUT_SPEC = frozenset({"edit-mode", "mcp-user-command-dry-run"})

_SAMPLE_BY_KIND = {
    "string": "sample",
    "integer": 1,
    "number": 1,
    "boolean": True,
    "array": ["sample"],
    "object": {},
}


def _required_mcp_arguments(spec) -> dict:
    from portskill.command_specs import MISSING

    arguments = {}
    for param in spec.params:
        if param.mcp is None or not param.mcp.required:
            continue
        name = param.key if param.mcp.name is MISSING else param.mcp.name
        arguments[name] = _SAMPLE_BY_KIND[param.kind]
    return arguments


class Slice7RemovedTranslatorTests(unittest.TestCase):
    def test_cli_translators_are_removed(self) -> None:
        from portskill import cli, mcp, server

        for name in ("tool_argv", "run_cli", "_cli_env", "PACKAGE_ROOT"):
            with self.subTest(module="mcp", name=name):
                self.assertFalse(hasattr(mcp, name))
        self.assertFalse(hasattr(server, "run_cli"))
        self.assertFalse(hasattr(cli, "MCP_USER_COMMAND_TOOL_NAMES"))

    def test_unknown_tool_keeps_legacy_error_envelope(self) -> None:
        import json

        from portskill import mcp

        result = mcp._call_tool("no_such_tool", {})
        self.assertEqual(
            result,
            {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"ok": False, "message": "unknown tool: no_such_tool"}),
                }],
                "isError": True,
            },
        )


class Slice7StructuralCoverageTests(unittest.TestCase):
    def test_every_ui_action_is_a_spec_or_a_documented_exception(self) -> None:
        from portskill.server import UI_COMMAND_SPECS

        self.assertFalse(UI_ACTIONS_WITHOUT_SPEC & set(UI_COMMAND_SPECS))
        self.assertEqual(
            set(UI_COMMAND_SPECS) | UI_ACTIONS_WITHOUT_SPEC,
            set(_ui_action_names()),
        )

    def test_every_cli_path_comes_from_a_spec_exactly_once(self) -> None:
        from portskill import cli
        from portskill.command_specs import COMMAND_SPECS

        expected = set()
        for spec in COMMAND_SPECS:
            if spec.cli is None:
                continue
            path = spec.cli.path
            for depth in range(1, len(path)):
                expected.add("/".join(path[:depth]))
            expected.add("/".join(path))
            for alias in spec.cli.aliases:
                expected.add("/".join(path[:-1] + (alias,)))
            for alternate in spec.cli.alternate_paths:
                expected.add("/".join(alternate.path))

        actual = _cli_choice_paths(cli.parser())
        self.assertEqual(len(actual), len(set(actual)), "duplicate CLI paths")
        self.assertEqual(set(actual), expected)

    def test_mcp_catalog_order_comes_from_specs(self) -> None:
        from portskill.command_specs import COMMAND_SPECS
        from portskill.mcp import TOOL_DEFS

        mcp_specs = sorted(
            (spec for spec in COMMAND_SPECS if spec.mcp is not None),
            key=lambda spec: spec.mcp.catalog_position,
        )
        self.assertEqual(
            [spec.mcp.name for spec in mcp_specs],
            [tool["name"] for tool in TOOL_DEFS],
        )

    def test_every_mcp_tool_routes_in_process_to_its_own_spec(self) -> None:
        from portskill import mcp
        from portskill.command_specs import COMMAND_SPECS

        routed = []
        for spec in COMMAND_SPECS:
            if spec.mcp is None:
                continue
            for name in (spec.mcp.name, *spec.mcp.aliases):
                routed.append((name, spec))
        self.assertEqual(
            {name for name, _ in routed},
            {tool["name"] for tool in mcp.TOOL_DEFS} | {"portskill_path"},
        )

        payload = {"status": "ok", "ok": True, "compatible": True}
        for name, spec in routed:
            arguments = _required_mcp_arguments(spec)
            if name == "ports_import":
                arguments = {"port": 1}
            with self.subTest(tool=name), mock.patch.object(
                mcp, "_tool_enabled", return_value=True
            ), mock.patch.object(
                mcp, "execute_command", return_value=dict(payload)
            ) as execute:
                mcp._call_tool(name, arguments)
            self.assertTrue(execute.called, f"{name} did not reach execute_command")
            self.assertIs(execute.call_args_list[0].args[0], spec)


if __name__ == "__main__":
    unittest.main()
