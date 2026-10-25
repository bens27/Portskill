"""Slice 6 contracts: one spec-driven chain validator shared by dry run, runtime and save."""
from __future__ import annotations

import ast
from dataclasses import replace
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.helpers import ROOT, IsolatedConfig

PACKAGE = pathlib.Path(__file__).parents[1] / "portskill"
FORBIDDEN_MODULES = {"cli", "mcp", "server"}
MODULE_SCOPE_ALLOWED = {
    "__future__",
    "dataclasses",
    "typing",
    "command_specs",
    "mcp_catalog",
    "registry",
}


def _imported_modules(node: ast.AST) -> list[str]:
    """Leaf module names an import node can bind (relative or portskill.*)."""
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[-1] if alias.name.startswith("portskill.") else alias.name
                for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level == 0 and not module.startswith("portskill"):
            return [module]
        if module in ("", "portskill"):
            return [alias.name for alias in node.names]
        return [module.split(".")[-1]]
    return []


class Slice6StructureTests(unittest.TestCase):
    def test_shared_module_never_imports_adapters(self) -> None:
        tree = ast.parse((PACKAGE / "command_chains.py").read_text(encoding="utf-8"))
        found = [
            name
            for node in ast.walk(tree)
            for name in _imported_modules(node)
            if name in FORBIDDEN_MODULES
        ]
        self.assertEqual(found, [])

    def test_shared_module_scope_imports_are_neutral(self) -> None:
        tree = ast.parse((PACKAGE / "command_chains.py").read_text(encoding="utf-8"))
        module_scope = {name for node in tree.body for name in _imported_modules(node)}
        self.assertLessEqual(module_scope, MODULE_SCOPE_ALLOWED)

    def test_import_loads_no_owner_or_adapter_module(self) -> None:
        code = (
            "import sys, portskill.command_chains\n"
            "print(sorted(m for m in sys.modules if m.startswith('portskill')))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
            check=True, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(
            done.stdout.strip(),
            str(["portskill", "portskill.command_chains", "portskill.command_specs",
                 "portskill.mcp_catalog", "portskill.registry"]),
        )

    def test_server_imports_no_private_mcp_helpers(self) -> None:
        tree = ast.parse((PACKAGE / "server.py").read_text(encoding="utf-8"))
        private = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "mcp"
            for alias in node.names
            if alias.name.startswith("_")
        ]
        self.assertEqual(private, [])

    def test_callers_delegate_to_shared_module(self) -> None:
        for path, needle in (("server.py", "command_chains.dry_run_chain("),
                             ("server.py", "command_chains.saved_chain_command(body)"),
                             ("mcp.py", "policy=command_chains.RUN_CHAIN_POLICY")):
            with self.subTest(path=path):
                self.assertIn(needle, (PACKAGE / path).read_text(encoding="utf-8"))

    def test_spec_derived_chain_tools_match_tool_defs(self) -> None:
        from portskill import command_chains
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        specs = command_chains.chain_tool_specs()
        self.assertEqual(set(specs), {tool["name"] for tool in TOOL_DEFS})
        by_name = {tool["name"]: tool for tool in TOOL_DEFS}
        for name, spec in specs.items():
            with self.subTest(name=name):
                self.assertEqual(_mcp_tool_definition(spec), by_name[name])

    def test_mcp_compatibility_names_kept(self) -> None:
        from portskill import command_chains, mcp

        self.assertIs(mcp._group_user_command_steps, command_chains.group_chain_steps)


class Slice6PolicyDifferenceTests(unittest.TestCase):
    """Each ChainPolicy field is the only thing separating the two callers."""

    def check(self, policy, step, prefs=None, user_commands=None):
        from portskill import command_chains

        return command_chains.validate_chain_step(
            step, prefs or {}, command_chains.chain_tool_specs(), policy=policy,
            load_user_commands=lambda: user_commands or {},
        )

    def test_policies_differ_only_in_declared_fields(self) -> None:
        from dataclasses import astuple, fields

        from portskill.command_chains import (
            DRY_RUN_CHAIN_POLICY,
            RUN_CHAIN_POLICY,
            SAVE_CHAIN_POLICY,
        )

        self.assertEqual(
            [f.name for f in fields(DRY_RUN_CHAIN_POLICY)][1:9],
            ["resolve_aliases", "normalize_mode", "clear_stop_arguments", "enabled_lookup",
             "tolerate_user_command_read_errors", "require_string_tool", "forbid_nested",
             "check_arguments"],
        )
        expected = {
            DRY_RUN_CHAIN_POLICY: ("dry-run", False, True, True, "exact", True, True, True, True, False),
            RUN_CHAIN_POLICY: ("run", True, False, False, "catalog", False, True, True, False, True),
            SAVE_CHAIN_POLICY: ("save", False, False, True, "none", False, True, False, False, False),
        }
        for policy, flags in expected.items():
            with self.subTest(policy=policy.name):
                self.assertEqual(astuple(policy)[:10], flags)
        # Save's messages are pinned whole: nested/disabled are inert under the
        # save policy, so only this pin catches a change to them.
        self.assertEqual(astuple(SAVE_CHAIN_POLICY)[10:], (
            "each step.tool must be a system TOOL_DEFS name (got {step!r})",
            "nested user commands forbidden",
            "tool disabled in settings.mcp_tools: {tool}",
        ))

    def test_resolve_aliases(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY

        for tool in ("portskill_path", " status"):
            with self.subTest(tool=tool):
                self.assertIsNotNone(self.check(DRY_RUN_CHAIN_POLICY, {"tool": tool}).rejection)
                run = self.check(RUN_CHAIN_POLICY, {"tool": tool})
                self.assertIsNone(run.rejection)
                self.assertEqual(run.tool, tool)
        self.assertEqual(self.check(RUN_CHAIN_POLICY, {"tool": "portskill_path"}).target, "portskill")

    def test_normalize_mode(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY

        for raw, dry_mode in ((" PARALLEL ", "parallel"), ("sideways", "series"), (5, "series"),
                              (None, "series")):
            with self.subTest(mode=raw):
                step = {"tool": "preset_list", "mode": raw}
                self.assertEqual(self.check(DRY_RUN_CHAIN_POLICY, step).mode, dry_mode)
                self.assertEqual(self.check(RUN_CHAIN_POLICY, step).mode, raw or "series")

    def test_clear_stop_arguments_and_check_arguments(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY

        step = {"tool": "stop", "arguments": {"range_id": "r"}}
        dry = self.check(DRY_RUN_CHAIN_POLICY, step)
        self.assertEqual((dry.arguments, dry.argument_errors), ({}, ("missing required: range_id",)))
        run = self.check(RUN_CHAIN_POLICY, step)
        self.assertEqual((run.arguments, run.argument_errors), ({"range_id": "r"}, ()))
        bad = {"tool": "status", "arguments": {"project": 5, "bogus": 1}}
        self.assertEqual(self.check(RUN_CHAIN_POLICY, bad).argument_errors, ())
        self.assertEqual(self.check(DRY_RUN_CHAIN_POLICY, bad).argument_errors,
                         ("unknown args: bogus", "project: expected string"))

    def test_enabled_lookup_portskill_path_false_pass(self) -> None:
        # Named, kept difference (Ben, 2026-10-23): exact lookup misses the
        # alias key; catalog lookup dual-reads it.
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY

        prefs = {"portskill_path": False}
        self.assertIsNone(self.check(DRY_RUN_CHAIN_POLICY, {"tool": "portskill"}, prefs).rejection)
        self.assertEqual(self.check(RUN_CHAIN_POLICY, {"tool": "portskill"}, prefs).rejection["error"],
                         "tool_disabled")

    def test_user_command_read_errors(self) -> None:
        from portskill import command_chains

        def boom():
            raise OSError("unreadable")

        tools = command_chains.chain_tool_specs()
        dry = command_chains.validate_chain_step(
            {"tool": "preset_list"}, {}, tools, policy=command_chains.DRY_RUN_CHAIN_POLICY,
            load_user_commands=boom)
        self.assertIsNone(dry.rejection)
        with self.assertRaises(OSError):
            command_chains.validate_chain_step(
                {"tool": "preset_list"}, {}, tools, policy=command_chains.RUN_CHAIN_POLICY,
                load_user_commands=boom)

    def test_save_enabled_lookup_none_accepts_disabled(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, SAVE_CHAIN_POLICY

        prefs = {"compat_check": False}
        self.assertIsNone(self.check(SAVE_CHAIN_POLICY, {"tool": "compat_check"}, prefs).rejection)
        self.assertIsNotNone(self.check(DRY_RUN_CHAIN_POLICY, {"tool": "compat_check"}, prefs).rejection)

    def test_save_forbid_nested_off(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, SAVE_CHAIN_POLICY

        cmds = {"preset_list": {}}
        self.assertIsNone(self.check(SAVE_CHAIN_POLICY, {"tool": "preset_list"}, None, cmds).rejection)
        self.assertEqual(
            self.check(DRY_RUN_CHAIN_POLICY, {"tool": "preset_list"}, None, cmds).rejection["message"],
            "nested user commands forbidden")

    def test_save_never_reads_user_commands(self) -> None:
        from portskill import command_chains

        def boom():
            raise AssertionError("save must not read user commands")

        check = command_chains.validate_chain_step(
            {"tool": "preset_list"}, {}, command_chains.chain_tool_specs(),
            policy=command_chains.SAVE_CHAIN_POLICY, load_user_commands=boom)
        self.assertIsNone(check.rejection)

    def test_require_string_tool(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY, SAVE_CHAIN_POLICY

        for policy in (DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY, SAVE_CHAIN_POLICY):
            for tool in (["status"], {"status": 1}, 7):
                with self.subTest(policy=policy.name, tool=tool):
                    self.assertIsNotNone(self.check(policy, {"tool": tool}).rejection)

    def test_save_keeps_raw_mode_and_clears_stop(self) -> None:
        from portskill.command_chains import SAVE_CHAIN_POLICY

        check = self.check(SAVE_CHAIN_POLICY, {"tool": "stop", "mode": " PARALLEL ",
                                               "arguments": {"range_id": "r"}})
        self.assertEqual((check.mode, check.arguments, check.argument_errors),
                         (" PARALLEL ", {}, ()))

    def test_rejection_shapes(self) -> None:
        from portskill.command_chains import DRY_RUN_CHAIN_POLICY, RUN_CHAIN_POLICY

        cases = (
            ({"tool": "nope"}, {}, {},
             ("unknown or non-system tool: 'nope'",
              "unknown_or_non_system_tool",
              "Step tool must be an existing system TOOL_DEFS name (got 'nope')")),
            ({"tool": "status"}, {}, {"status": {}},
             ("nested user commands forbidden", "nested_user_command_forbidden",
              "v1: user commands cannot nest other user commands")),
            ({"tool": "status"}, {"status": False}, {},
             ("tool disabled in settings.mcp_tools: status", "tool_disabled",
              "Step tool disabled in settings.mcp_tools: status")),
        )
        for step, prefs, cmds, (dry_msg, code, run_msg) in cases:
            with self.subTest(step=step, prefs=prefs):
                tool = step["tool"]
                self.assertEqual(self.check(DRY_RUN_CHAIN_POLICY, step, prefs, cmds).rejection,
                                 {"ok": False, "tool": tool, "mode": "series", "message": dry_msg})
                self.assertEqual(self.check(RUN_CHAIN_POLICY, step, prefs, cmds).rejection,
                                 {"ok": False, "tool": tool, "mode": "series", "error": code,
                                  "message": run_msg})


class Slice6RealDispatchRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = IsolatedConfig().__enter__()
        self.addCleanup(self.cfg.__exit__, None, None, None)
        proj = self.cfg.root / "proj"
        proj.mkdir()
        self.project = str(proj)

    def test_spec_coercion_mutation_reaches_ui_dry_run(self) -> None:
        from portskill import server
        from portskill.command_specs import STATUS_COMMAND

        self.cfg.write_registry({})
        action = {"action": "mcp-user-command-dry-run",
                  "steps": [{"tool": "status", "arguments": {"project": self.project}}]}
        self.assertEqual(server.dispatch_ui_action(action)[0], 0)
        original = STATUS_COMMAND.params
        mutated = tuple(
            replace(param, mcp=replace(param.mcp, json_type="integer"))
            if param.mcp is not None and param.key == "project"
            else param
            for param in original
        )
        self.assertNotEqual(mutated, original)
        object.__setattr__(STATUS_COMMAND, "params", mutated)
        try:
            code, body = server.dispatch_ui_action(action)
        finally:
            object.__setattr__(STATUS_COMMAND, "params", original)
        self.assertEqual(code, 422)
        self.assertEqual(body["steps"][0]["message"], "project: expected integer")

    def test_run_policy_mutation_reaches_mcp_tools_call(self) -> None:
        from portskill import command_chains, mcp

        self.cfg.write_registry({"settings": {"mcp_user_commands": {"mine": {
            "name": "mine",
            "steps": [{"tool": "portskill_path", "mode": "series",
                       "arguments": {"mode": "status", "project": self.project}}],
        }}}})
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "mine", "arguments": {}}}
        self.assertTrue(mcp.mcp_handle(message)["result"]["structuredContent"]["ok"])
        strict = replace(command_chains.RUN_CHAIN_POLICY, resolve_aliases=False)
        with mock.patch.object(command_chains, "RUN_CHAIN_POLICY", strict):
            body = mcp.mcp_handle(message)["result"]["structuredContent"]
        self.assertFalse(body["ok"])
        self.assertEqual(body["steps"][0]["error"], "unknown_or_non_system_tool")

    def test_dry_run_policy_mutation_closes_false_pass(self) -> None:
        from portskill import command_chains, server

        self.cfg.write_registry({"settings": {"mcp_tools": {"portskill_path": False}}})
        action = {"action": "mcp-user-command-dry-run", "steps": [
            {"tool": "portskill", "arguments": {"mode": "status", "project": self.project}}]}
        self.assertEqual(server.dispatch_ui_action(action)[0], 0)
        catalog = replace(command_chains.DRY_RUN_CHAIN_POLICY,
                          enabled_lookup=command_chains.ENABLED_LOOKUP_CATALOG)
        with mock.patch.object(command_chains, "DRY_RUN_CHAIN_POLICY", catalog):
            code, body = server.dispatch_ui_action(action)
        self.assertEqual(code, 422)
        self.assertEqual(body["steps"][0]["message"], "tool disabled in settings.mcp_tools: portskill")

    def save(self, steps, **extra):
        from portskill import server

        return server.dispatch_ui_action(
            {"action": "mcp-user-command-save", "name": "chain", "steps": steps, **extra})

    def test_save_policy_mutation_reaches_ui_save(self) -> None:
        from portskill import command_chains

        self.cfg.write_registry({"settings": {"mcp_tools": {"compat_check": False}}})
        code, body = self.save([{"tool": "stop", "arguments": {"range_id": "r"}}])
        self.assertEqual(code, 0, body)
        self.assertEqual(body["result"]["settings"]["mcp_user_commands"]["chain"]["steps"][0]["arguments"], {})
        keep = replace(command_chains.SAVE_CHAIN_POLICY, clear_stop_arguments=False)
        with mock.patch.object(command_chains, "SAVE_CHAIN_POLICY", keep):
            code, body = self.save([{"tool": "stop", "arguments": {"range_id": "r"}}])
        self.assertEqual(code, 0, body)
        self.assertEqual(body["result"]["settings"]["mcp_user_commands"]["chain"]["steps"][0]["arguments"],
                         {"range_id": "r"})

        self.assertEqual(self.save([{"tool": "compat_check"}])[0], 0)
        exact = replace(command_chains.SAVE_CHAIN_POLICY,
                        enabled_lookup=command_chains.ENABLED_LOOKUP_EXACT)
        with mock.patch.object(command_chains, "SAVE_CHAIN_POLICY", exact):
            self.assertEqual(self.save([{"tool": "compat_check"}]),
                             (400, {"ok": False, "message": "tool disabled in settings.mcp_tools: compat_check"}))

        worded = replace(command_chains.SAVE_CHAIN_POLICY, unknown_message="nope: {tool}")
        with mock.patch.object(command_chains, "SAVE_CHAIN_POLICY", worded):
            self.assertEqual(self.save([{"tool": "zz"}]), (400, {"ok": False, "message": "nope: zz"}))

    def test_spec_catalog_mutation_reaches_ui_save(self) -> None:
        from portskill import command_chains

        self.cfg.write_registry({})
        self.assertEqual(self.save([{"tool": "preset_list"}])[0], 0)
        specs = command_chains.chain_tool_specs()
        del specs["preset_list"]
        with mock.patch.object(command_chains, "chain_tool_specs", return_value=specs):
            code, body = self.save([{"tool": "preset_list"}])
        self.assertEqual(code, 400)
        self.assertIn("each step.tool must be a system TOOL_DEFS name", body["message"])

    def test_dry_run_reads_registry_settings_not_mcp_seam(self) -> None:
        # Documented seam change: the UI dry run reads registry.read_settings
        # via command_chains, not mcp._load_settings_from_registry.
        from portskill import mcp, server

        self.cfg.write_registry({"settings": {"mcp_tools": {"preset_list": False}}})
        action = {"action": "mcp-user-command-dry-run", "steps": [{"tool": "preset_list"}]}
        with mock.patch.object(mcp, "_load_settings_from_registry", return_value={}):
            code, body = server.dispatch_ui_action(action)
        self.assertEqual(code, 422)
        self.assertEqual(body["steps"][0]["message"], "tool disabled in settings.mcp_tools: preset_list")

    def test_mcp_settings_seam_still_drives_chain_prefs(self) -> None:
        from portskill import mcp

        self.cfg.write_registry({})
        with mock.patch.object(mcp, "_load_settings_from_registry",
                               return_value={"mcp_tools": {"preset_list": False}}):
            body = mcp.call_user_command(
                "c", {"name": "c", "steps": [{"tool": "preset_list"}]})["structuredContent"]
        self.assertEqual(body["steps"][0]["error"], "tool_disabled")


if __name__ == "__main__":
    unittest.main()
