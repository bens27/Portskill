"""Slice 1 command-spec declarations and c5ea284 parity oracle."""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, ROOT


FIXTURE = pathlib.Path(__file__).with_name("fixtures") / "command_surface_c5ea284.json"


def _digest(text: str) -> dict:
    payload = text.encode("utf-8")
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _scalar(value):
    if value is argparse.SUPPRESS:
        return "<SUPPRESS>"
    if callable(value):
        return f"{value.__module__}.{value.__name__}"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scalar(item) for key, item in value.items()}
    return repr(value)


def _parser_inventory(parser) -> dict:
    arguments = []
    subcommands = []
    for action in parser._actions:
        action_row = {
            "action": action.__class__.__name__,
            "option_strings": list(action.option_strings),
            "dest": action.dest,
            "required": bool(getattr(action, "required", False)),
            "nargs": _scalar(action.nargs),
            "default": _scalar(action.default),
            "const": _scalar(getattr(action, "const", None)),
            "type": _scalar(action.type),
            "metavar": _scalar(action.metavar),
            "help": _scalar(action.help),
        }
        if isinstance(action, argparse._SubParsersAction):
            action_row["choices"] = list(action.choices)
            action_row["choice_entries"] = [
                {
                    "dest": _scalar(choice.dest),
                    "help": _scalar(choice.help),
                    "metavar": _scalar(choice.metavar),
                }
                for choice in action._choices_actions
            ]
            arguments.append(action_row)
            subcommands = [
                {"name": name, "parser": _parser_inventory(child)}
                for name, child in action.choices.items()
            ]
            continue
        action_row["choices"] = _scalar(
            list(action.choices) if action.choices is not None else None
        )
        arguments.append(action_row)
    return {
        "prog": parser.prog,
        "description": _scalar(parser.description),
        "epilog": _scalar(parser.epilog),
        "defaults": {key: _scalar(value) for key, value in parser._defaults.items()},
        "arguments": arguments,
        "subcommands": subcommands,
    }


def _cli_choice_paths(parser, prefix=()) -> list[str]:
    paths = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            path = prefix + (name,)
            paths.append("/".join(path))
            paths.extend(_cli_choice_paths(child, path))
    return paths


def _ui_action_names() -> list[str]:
    import portskill.server as server

    tree = ast.parse(pathlib.Path(server.__file__).read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_ui_action"
    )
    actions = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "action":
            continue
        comparator = node.comparators[0]
        if (
            isinstance(node.ops[0], ast.Eq)
            and isinstance(comparator, ast.Constant)
            and isinstance(comparator.value, str)
        ):
            actions.add(comparator.value)
        elif isinstance(node.ops[0], ast.In) and isinstance(
            comparator, (ast.Tuple, ast.List)
        ):
            actions.update(
                item.value
                for item in comparator.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return sorted(actions)


def _cli_case(argv: list[str]) -> tuple[int, str, str]:
    from portskill import cli

    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}), contextlib.redirect_stdout(
        stdout
    ), contextlib.redirect_stderr(stderr):
        try:
            exit_code = int(cli.main(argv) or 0)
        except SystemExit as exc:
            exit_code = int(exc.code)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _parsed_namespace(argv: list[str]) -> dict:
    from portskill import cli

    namespace = vars(cli.parser().parse_args(argv))
    namespace.pop("func", None)
    return _scalar(namespace)


def _run_oracle_cli_case(expected: dict) -> dict:
    from portskill import cli

    with IsolatedConfig() as iso:
        iso.write_registry()
        project = iso.root / "project"
        project.mkdir()
        argv = [str(project) if token == "$PROJECT" else token for token in expected["argv"]]
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {
            "COLUMNS": "80",
            "LINES": "24",
            **(expected.get("extra_env") or {}),
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, environment))
            if expected.get("mock") == "doctor_success":
                stack.enter_context(mock.patch(
                    "portskill.doctor.doctor_result",
                    return_value={
                        "status": "ok",
                        "checks": [{
                            "name": "fixed",
                            "ok": True,
                            "detail": "controlled",
                        }],
                    },
                ))
            if expected.get("fixed_clock"):
                stack.enter_context(mock.patch(
                    "portskill.sessions.utc_now",
                    return_value="2026-01-02T03:04:05+00:00",
                ))
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    exit_code = int(cli.main(argv) or 0)
                except SystemExit as exc:
                    exit_code = int(exc.code)
        result = {
            "exit": exit_code,
            "stdout": _digest(stdout.getvalue()),
            "stderr": _digest(stderr.getvalue()),
        }
        if "registry" in expected:
            result["registry"] = _digest(iso.registry_path.read_text())
        return result


class CommandSurfaceOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.oracle = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_mcp_tools_list_exact_bytes_match_c5ea284(self) -> None:
        from portskill import mcp

        for name, expected in self.oracle["mcp_tools_list"].items():
            settings = dict(expected["settings"])
            reference = settings.pop("mcp_tools_ref", None)
            if reference:
                settings["mcp_tools"] = dict(self.oracle[reference])
            with self.subTest(name=name), IsolatedConfig() as iso:
                iso.write_registry({"settings": settings})
                response = mcp.mcp_handle({
                    "jsonrpc": "2.0",
                    "id": expected["id"],
                    "method": "tools/list",
                })
            wire = json.dumps(response, separators=(",", ":")) + "\n"
            self.assertEqual(_digest(wire), expected["wire"])
            self.assertEqual(
                [tool["name"] for tool in response["result"]["tools"]],
                expected["tool_names"],
            )

    def test_complete_cli_tree_inventory_matches_c5ea284(self) -> None:
        from portskill import cli

        with mock.patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}):
            inventory = _parser_inventory(cli.parser())
        wire = json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n"
        expected = self.oracle["cli"]["tree_inventory"]
        self.assertEqual(_digest(wire), expected["wire"])
        self.assertEqual(
            [item["name"] for item in inventory["subcommands"]],
            expected["top_level_commands"],
        )

    def test_surface_inventory_matches_c5ea284(self) -> None:
        from portskill import cli
        from portskill.mcp_catalog import TOOL_DEFS

        expected = self.oracle["surface_inventory"]
        self.assertEqual(_cli_choice_paths(cli.parser()), expected["cli_choice_paths"])
        self.assertEqual(
            [tool["name"] for tool in TOOL_DEFS], expected["mcp_tool_names"]
        )
        self.assertEqual(_ui_action_names(), expected["ui_actions"])

    def test_all_cli_byte_and_persistence_cases_match_c5ea284(self) -> None:
        cases = self.oracle["cli"]["cases"]
        self.assertEqual(len(cases), 28)
        for name, expected in cases.items():
            with self.subTest(name=name):
                actual = _run_oracle_cli_case(expected)
            comparable = {
                key: expected[key]
                for key in ("exit", "stdout", "stderr", "registry")
                if key in expected
            }
            self.assertEqual(actual, comparable)

    def test_parsed_absent_false_zero_empty_repeated_and_encoded_values(self) -> None:
        for name, expected in self.oracle["cli"]["parse_cases"].items():
            with self.subTest(name=name):
                actual = _parsed_namespace(self.oracle["cli"]["parse_argv"][name])
            self.assertEqual(actual, expected)

    def test_subparser_metadata_mutations_change_inventory_contract(self) -> None:
        from portskill import cli

        expected = self.oracle["cli"]["tree_inventory"]["wire"]

        def inventory_digest(root):
            wire = json.dumps(
                _parser_inventory(root), sort_keys=True, separators=(",", ":")
            ) + "\n"
            return _digest(wire)

        required_parser = cli.parser()
        settings_parser = next(
            action.choices["settings"]
            for action in required_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        settings_subparsers = next(
            action
            for action in settings_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertTrue(settings_subparsers.required)
        settings_subparsers.required = False
        self.assertNotEqual(inventory_digest(required_parser), expected)

        dest_parser = cli.parser()
        settings_parser = next(
            action.choices["settings"]
            for action in dest_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        settings_subparsers = next(
            action
            for action in settings_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        settings_subparsers.dest = "changed_settings_command"
        self.assertNotEqual(inventory_digest(dest_parser), expected)

        help_parser = cli.parser()
        root_subparsers = next(
            action
            for action in help_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertTrue(root_subparsers._choices_actions)
        root_subparsers._choices_actions[0].help = "changed help"
        self.assertNotEqual(inventory_digest(help_parser), expected)


class CommandSpecStructureTests(unittest.TestCase):
    def test_specs_are_unique_and_surface_positions_are_stable(self) -> None:
        from portskill.command_specs import COMMAND_SPECS
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        ids = [spec.id for spec in COMMAND_SPECS]
        cli_paths = [spec.cli.path for spec in COMMAND_SPECS if spec.cli]
        mcp_names = [spec.mcp.name for spec in COMMAND_SPECS if spec.mcp]
        ui_actions = [
            action
            for spec in COMMAND_SPECS
            if spec.ui
            for action in spec.ui.actions
        ]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(cli_paths), len(set(cli_paths)))
        self.assertEqual(len(mcp_names), len(set(mcp_names)))
        self.assertEqual(len(ui_actions), len(set(ui_actions)))
        for spec in COMMAND_SPECS:
            if spec.mcp is None:
                continue
            self.assertEqual(
                TOOL_DEFS[spec.mcp.catalog_position], _mcp_tool_definition(spec)
            )
            self.assertEqual(len({param.key for param in spec.params}), len(spec.params))

    def test_cli_pilot_uses_generated_status_and_doctor_bindings(self) -> None:
        from portskill import cli

        status = cli.parser().parse_args(["status"])
        doctor = cli.parser().parse_args(["doctor"])
        self.assertIs(status.func, cli.cmd_status)
        self.assertIsNone(status.project)
        self.assertIs(doctor.func, cli.cmd_doctor)
        self.assertEqual(doctor.project, ".")

    def test_every_fixed_handler_reference_resolves(self) -> None:
        from portskill.command_specs import COMMAND_SPECS

        for spec in COMMAND_SPECS:
            with self.subTest(spec=spec.id):
                module = importlib.import_module(spec.handler.module)
                handler = getattr(module, spec.handler.symbol)
                self.assertTrue(callable(handler))

    def test_command_specs_has_no_runtime_or_adapter_imports(self) -> None:
        import portskill.command_specs as command_specs

        tree = ast.parse(pathlib.Path(command_specs.__file__).read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(("." * node.level) + (node.module or ""))
        self.assertEqual(
            set(imports), {"__future__", "dataclasses", "typing"}
        )

    def test_catalog_enumeration_does_not_import_domain_handlers(self) -> None:
        code = (
            "import json,sys; import portskill.mcp_catalog; "
            "print(json.dumps({name:(name in sys.modules) for name in "
            "('portskill.status','portskill.doctor')},sort_keys=True))"
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
            json.loads(completed.stdout),
            {"portskill.doctor": False, "portskill.status": False},
        )


if __name__ == "__main__":
    unittest.main()
