"""Slice 2 late-binding runtime and settings/registry projections."""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import ROOT


class CommandRuntimeTests(unittest.TestCase):
    def test_catalog_and_runtime_import_no_domain_handlers(self) -> None:
        modules = (
            "portskill.settings",
            "portskill.registry_mutations",
            "portskill.status",
            "portskill.doctor",
        )
        code = (
            "import json,sys; import portskill.command_runtime; "
            f"names={modules!r}; "
            "print(json.dumps({name:(name in sys.modules) for name in names},"
            "sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout), {name: False for name in modules}
        )

    def test_shared_modules_import_no_transport_adapters(self) -> None:
        import portskill.command_runtime as command_runtime
        import portskill.command_specs as command_specs

        for module in (command_specs, command_runtime):
            with self.subTest(module=module.__name__):
                tree = ast.parse(pathlib.Path(module.__file__).read_text())
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imports.add(("." * node.level) + (node.module or ""))
                self.assertTrue(
                    imports.isdisjoint(
                        {".cli", ".mcp", ".server", "portskill.cli", "portskill.mcp", "portskill.server"}
                    )
                )

    def test_runtime_uses_named_binders_and_owner_modules(self) -> None:
        from portskill.command_runtime import execute_command
        from portskill.command_specs import (
            ALLOCATE_COMMAND,
            DOCTOR_COMMAND,
            SETTINGS_GET_COMMAND,
        )

        with mock.patch(
            "portskill.registry_mutations.allocate_result",
            return_value={"status": "ok"},
        ) as allocate, mock.patch(
            "portskill.doctor.doctor_result", return_value={"status": "ok"}
        ) as doctor, mock.patch(
            "portskill.settings.settings_get", return_value={"status": "ok"}
        ) as settings_get:
            self.assertEqual(
                execute_command(ALLOCATE_COMMAND, {"count": 2}),
                {"status": "ok"},
            )
            self.assertEqual(
                execute_command(DOCTOR_COMMAND, {"project": "/tmp/app"}),
                {"status": "ok"},
            )
            self.assertEqual(
                execute_command(SETTINGS_GET_COMMAND, {"ignored": True}),
                {"status": "ok"},
            )

        allocate.assert_called_once_with({"count": 2})
        doctor.assert_called_once_with("/tmp/app")
        settings_get.assert_called_once_with()

    def test_runtime_rejects_forged_specs_and_handler_refs(self) -> None:
        from portskill.command_runtime import execute_command, resolve_handler
        from portskill.command_specs import ALLOCATE_COMMAND, CommandSpec, HandlerRef

        with self.assertRaisesRegex(ValueError, "not declared"):
            resolve_handler(HandlerRef("os", "system"))
        forged = CommandSpec(
            id=ALLOCATE_COMMAND.id,
            handler=ALLOCATE_COMMAND.handler,
            params=ALLOCATE_COMMAND.params,
            cli=ALLOCATE_COMMAND.cli,
            mcp=ALLOCATE_COMMAND.mcp,
            ui=ALLOCATE_COMMAND.ui,
        )
        self.assertEqual(forged, ALLOCATE_COMMAND)
        with self.assertRaisesRegex(ValueError, "not declared"):
            execute_command(forged, {"count": 1})


class Slice2ProjectionTests(unittest.TestCase):
    def test_migrated_specs_occupy_original_mcp_positions(self) -> None:
        from portskill.command_specs import COMMAND_SPECS
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        migrated = {
            "allocate",
            "activate",
            "release",
            "settings_get",
            "settings_set",
        }
        for spec in COMMAND_SPECS:
            if spec.id not in migrated:
                continue
            with self.subTest(spec=spec.id):
                self.assertEqual(
                    TOOL_DEFS[spec.mcp.catalog_position], _mcp_tool_definition(spec)
                )

    def test_ui_actions_are_indexed_to_catalog_specs(self) -> None:
        from portskill.command_specs import COMMAND_SPECS
        from portskill.server import UI_COMMAND_SPECS

        expected = {
            action: spec
            for spec in COMMAND_SPECS
            if spec.ui is not None
            for action in spec.ui.actions
        }
        self.assertEqual(UI_COMMAND_SPECS, expected)
        self.assertIs(UI_COMMAND_SPECS["allocate"], expected["allocate"])
        self.assertIs(UI_COMMAND_SPECS["release"], expected["release"])
        self.assertIs(
            UI_COMMAND_SPECS["settings-save"], expected["settings-save"]
        )

    def test_real_ui_path_consumes_declared_binder_aliases_and_coercions(self) -> None:
        from portskill import server

        declared_binder = mock.Mock(wraps=server._bind_ui_declared_params)
        with mock.patch.dict(
            server._UI_PAYLOAD_BINDERS,
            {"allocate_body": declared_binder},
        ), mock.patch(
            "portskill.registry_mutations.allocate_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as allocate:
            code, response = server.dispatch_ui_action({
                "action": "allocate",
                "count": "not-an-integer",
                "project": 42,
                "tailnet": "",
                "note": "bound",
                "machine": "",
                "machineId": "remote-machine",
                "host": "dev.example",
                "scheme": "https",
            })

        expected = {
            "count": 1,
            "note": "bound",
            "project": "42",
            "tailnet": "none",
            "machine": "remote-machine",
            "host": "dev.example",
            "scheme": "https",
        }
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["options"], expected)
        declared_binder.assert_called_once()
        allocate.assert_called_once_with(expected)

    def test_real_ui_path_preserves_falsy_final_alias(self) -> None:
        from portskill import server

        with mock.patch(
            "portskill.registry_mutations.allocate_result",
            side_effect=lambda options: {"status": "ok", "options": options},
        ) as allocate:
            code, response = server.dispatch_ui_action({
                "action": "allocate",
                "machine_id": "",
            })

        expected = {
            "count": 1,
            "note": None,
            "project": ".",
            "tailnet": "none",
            "machine": "",
            "host": None,
            "scheme": None,
        }
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["options"], expected)
        allocate.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
