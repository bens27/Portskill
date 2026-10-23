"""Characterization of user-command chain validation (dry run vs runtime).

Written against e358543 BEFORE the slice 6 extraction and kept green through
it. Every test drives a public entry point that exists on both sides of the
move: server.dry_run_user_command_steps, server.dispatch_ui_action,
mcp._run_one_chain_step, mcp.call_user_command and mcp.mcp_handle. Tools run
for real against an isolated config; nothing below the owners is mocked.

The dry run and the runtime deliberately disagree in several places (decision
C: equalizing strictness is a later call). Each difference is pinned here by
name so a refactor cannot silently change one side.
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import IsolatedConfig


def _status_step(project: str, **extra) -> dict:
    return {"tool": "status", "arguments": {"project": project}, **extra}


class ChainCharacterizationBase(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = IsolatedConfig().__enter__()
        self.addCleanup(self.cfg.__exit__, None, None, None)
        proj = self.cfg.root / "proj"
        proj.mkdir()
        self.project = str(proj)

    def settings(self, **settings) -> None:
        self.cfg.write_registry({"settings": settings})

    def dry(self, steps):
        from portskill import server

        return server.dry_run_user_command_steps(steps)

    def run_step(self, step: dict) -> dict:
        from portskill import mcp
        from portskill.mcp_catalog import system_tool_names

        return mcp._run_one_chain_step(step, mcp._mcp_tools_prefs(), system_tool_names())

    def run_chain(self, steps, name: str = "chain") -> dict:
        from portskill import mcp

        return mcp.call_user_command(name, {"name": name, "steps": steps})["structuredContent"]


class DryRunCharacterizationTests(ChainCharacterizationBase):
    def test_valid_series_and_parallel_chain(self) -> None:
        self.settings()
        out = self.dry([
            _status_step(self.project, mode="parallel"),
            {"tool": "settings_get", "mode": "parallel"},
            {"tool": "preset_list"},
        ])
        self.assertEqual(out, {
            "ok": True,
            "dry_run": True,
            "executed": False,
            "steps": [
                {"ok": True, "tool": "status", "mode": "parallel",
                 "arguments": {"project": self.project}, "message": "ok"},
                {"ok": True, "tool": "settings_get", "mode": "parallel",
                 "arguments": {}, "message": "ok"},
                {"ok": True, "tool": "preset_list", "mode": "series",
                 "arguments": {}, "message": "ok"},
            ],
            "groups": [
                {"kind": "parallel", "tools": ["status", "settings_get"], "count": 2},
                {"kind": "series", "tools": ["preset_list"], "count": 1},
            ],
            "summary": "Run status and settings_get in parallel, then preset_list.",
            "message": "validated without executing tools",
        })

    def test_summary_for_three_parallel_tools(self) -> None:
        self.settings()
        out = self.dry([
            {"tool": "preset_list", "mode": "parallel"},
            {"tool": "settings_get", "mode": "parallel"},
            {"tool": "history_list", "mode": "parallel"},
        ])
        self.assertEqual(
            out["summary"], "Run preset_list, settings_get, and history_list in parallel.")

    def test_mode_normalization_in_dry_run(self) -> None:
        self.settings()
        out = self.dry([
            {"tool": "preset_list", "mode": " PARALLEL "},
            {"tool": "settings_get", "mode": "parallel"},
            {"tool": "history_list", "mode": "sideways"},
        ])
        self.assertEqual([s["mode"] for s in out["steps"]], ["parallel", "parallel", "series"])
        self.assertEqual(out["groups"], [
            {"kind": "parallel", "tools": ["preset_list", "settings_get"], "count": 2},
            {"kind": "series", "tools": ["history_list"], "count": 1},
        ])

    def test_non_string_mode_validates_but_drops_groups(self) -> None:
        # The step check maps non-str mode to series, but grouping calls
        # .strip() on the raw mode, raises, and the dry run swallows it.
        self.settings()
        out = self.dry([{"tool": "preset_list", "mode": 5}])
        self.assertTrue(out["ok"])
        self.assertEqual(out["steps"][0]["mode"], "series")
        self.assertEqual(out["groups"], [])
        self.assertEqual(out["summary"], "")

    def test_non_object_step_and_non_dict_arguments(self) -> None:
        self.settings()
        out = self.dry(["status", {"tool": "preset_list", "arguments": ["x"]}])
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "validation failed")
        self.assertEqual(out["steps"][0], {
            "ok": False, "tool": None, "mode": "series", "message": "step must be an object"})
        self.assertEqual(out["steps"][1]["arguments"], {})
        self.assertTrue(out["steps"][1]["ok"])
        self.assertEqual(out["groups"], [{"kind": "series", "tools": ["preset_list"], "count": 1}])

    def test_empty_chain_is_not_ok_but_reports_validated(self) -> None:
        self.settings()
        out = self.dry([])
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "validated without executing tools")
        self.assertEqual((out["steps"], out["groups"], out["summary"]), ([], [], ""))

    def test_unknown_alias_padded_and_non_string_tools_rejected(self) -> None:
        self.settings()
        for tool in ("no_such_tool", "portskill_path", " status", 7):
            with self.subTest(tool=tool):
                out = self.dry([{"tool": tool}])
                self.assertFalse(out["ok"])
                self.assertEqual(out["steps"], [{
                    "ok": False, "tool": tool, "mode": "series",
                    "message": f"unknown or non-system tool: {tool!r}",
                }])

    def test_nested_user_command_with_system_name(self) -> None:
        self.settings(mcp_user_commands={"status": {"name": "status", "steps": [
            {"tool": "preset_list", "mode": "series", "arguments": {}}]}})
        out = self.dry([_status_step(self.project)])
        self.assertEqual(out["steps"], [{
            "ok": False, "tool": "status", "mode": "series",
            "message": "nested user commands forbidden"}])

    def test_disabled_tool_exact_key(self) -> None:
        self.settings(mcp_tools={"compat_check": False})
        out = self.dry([{"tool": "compat_check"}])
        self.assertEqual(out["steps"], [{
            "ok": False, "tool": "compat_check", "mode": "series",
            "message": "tool disabled in settings.mcp_tools: compat_check"}])

    def test_handoff_tools_disabled_while_handoff_off(self) -> None:
        self.settings()
        out = self.dry([{"tool": "handoff_list", "arguments": {"project": self.project}}])
        self.assertEqual(out["steps"][0]["message"],
                         "tool disabled in settings.mcp_tools: handoff_list")

    def test_argument_schema_checks(self) -> None:
        self.settings()
        cases = {
            "missing": ({"tool": "preset_apply", "arguments": {}}, "missing required: name"),
            "blank": ({"tool": "preset_apply", "arguments": {"name": ""}}, "missing required: name"),
            "none": ({"tool": "preset_apply", "arguments": {"name": None}},
                     "missing required: name; name: expected string"),
            "unknown": ({"tool": "status", "arguments": {"project": "p", "bogus": 1}},
                        "unknown args: bogus"),
            "string": ({"tool": "status", "arguments": {"project": 5}},
                       "project: expected string"),
            "integer": ({"tool": "history_restore", "arguments": {"index": "x"}},
                        "index: expected integer"),
            "boolean": ({"tool": "apply_defaults", "arguments": {"also_stop_off": "yes"}},
                        "also_stop_off: expected boolean"),
            "array": ({"tool": "compat_check", "arguments": {"presets": "a"}},
                      "presets: expected array"),
            "enum": ({"tool": "portskill", "arguments": {"mode": "fly"}},
                     "mode: must be one of ['start', 'stop', 'release', 'restart', 'status']"),
        }
        for label, (step, message) in cases.items():
            with self.subTest(label):
                out = self.dry([step])
                self.assertFalse(out["ok"])
                self.assertEqual(out["steps"][0]["message"], message)
                self.assertEqual(out["steps"][0]["arguments"], step["arguments"])

    def test_option_like_string_values_pass_dry_run(self) -> None:
        self.settings()
        for value in ("-x", "--", "--opt=val", "-1", "-1.5", "-"):
            with self.subTest(value=value):
                out = self.dry([{"tool": "history_list", "arguments": {"environment": value}}])
                self.assertTrue(out["ok"])
                self.assertEqual(out["steps"][0]["arguments"], {"environment": value})


class RuntimeCharacterizationTests(ChainCharacterizationBase):
    def test_step_runner_success_envelope(self) -> None:
        self.settings()
        one = self.run_step({"tool": "preset_list", "mode": " PARALLEL "})
        self.assertEqual(one, {
            "ok": True, "tool": "preset_list", "mode": " PARALLEL ", "arguments": {},
            "isError": False, "result": {"count": 0, "presets": [], "status": "ok"},
        })

    def test_step_runner_rejections(self) -> None:
        self.settings(
            mcp_tools={"compat_check": False},
            mcp_user_commands={"status": {"name": "status", "steps": [
                {"tool": "preset_list", "mode": "series", "arguments": {}}]}},
        )
        self.assertEqual(self.run_step({"tool": "no_such_tool"}), {
            "ok": False, "tool": "no_such_tool", "mode": "series",
            "error": "unknown_or_non_system_tool",
            "message": "Step tool must be an existing system TOOL_DEFS name (got 'no_such_tool')",
        })
        self.assertEqual(self.run_step({"tool": 7, "mode": None})["error"],
                         "unknown_or_non_system_tool")
        self.assertEqual(self.run_step({"tool": "status", "mode": "parallel"}), {
            "ok": False, "tool": "status", "mode": "parallel",
            "error": "nested_user_command_forbidden",
            "message": "v1: user commands cannot nest other user commands",
        })
        self.assertEqual(self.run_step({"tool": "compat_check"}), {
            "ok": False, "tool": "compat_check", "mode": "series",
            "error": "tool_disabled",
            "message": "Step tool disabled in settings.mcp_tools: compat_check",
        })

    def test_runtime_canonicalizes_alias_and_padding(self) -> None:
        self.settings()
        alias = self.run_step(
            {"tool": "portskill_path", "arguments": {"mode": "status", "project": self.project}})
        self.assertTrue(alias["ok"])
        self.assertEqual(alias["tool"], "portskill_path")
        self.assertEqual(alias["result"]["mode"], "status")
        padded = self.run_step({"tool": " status", "arguments": {"project": self.project}})
        self.assertTrue(padded["ok"])
        self.assertEqual(padded["tool"], " status")

    def test_runtime_does_not_check_argument_schema(self) -> None:
        # Dry run rejects project=5 as "expected string"; runtime passes it on.
        self.settings()
        one = self.run_step({"tool": "status", "arguments": {"project": 5}})
        self.assertTrue(one["ok"])
        self.assertEqual(one["arguments"], {"project": 5})

    def test_call_user_command_groups_and_owner_errors(self) -> None:
        self.settings()
        out = self.run_chain([
            {"tool": "preset_list", "mode": "parallel"},
            {"tool": "settings_get", "mode": "parallel"},
            {"tool": "preset_apply", "arguments": {"name": "missing-preset"}},
            {"tool": "no_such_tool"},
        ])
        self.assertFalse(out["ok"])
        self.assertEqual(
            [(s["tool"], s["mode"], s["ok"]) for s in out["steps"]],
            [("preset_list", "parallel", True), ("settings_get", "parallel", True),
             ("preset_apply", "series", False), ("no_such_tool", "series", False)],
        )
        self.assertEqual(out["steps"][2]["result"]["reason"], "preset_not_found")
        self.assertEqual(
            {k: out[k] for k in ("kind", "name", "description", "x-portskill-kind")},
            {"kind": "user-command", "name": "chain", "description": "",
             "x-portskill-kind": "user-command"},
        )

    def test_call_user_command_needs_input_is_step_error(self) -> None:
        self.settings()
        out = self.run_chain([{"tool": "allocate", "arguments": {"count": 1, "project": self.project}}])
        self.assertFalse(out["ok"])
        self.assertEqual(out["steps"][0]["result"]["input"], "tailnet")
        self.assertTrue(out["steps"][0]["isError"])

    def test_call_user_command_normalizes_before_running(self) -> None:
        # Registry normalization strips the tool and maps non-str mode to
        # series, which shields the grouping .strip() from non-str modes.
        self.settings()
        out = self.run_chain([{"tool": " preset_list ", "mode": 5}] * 13)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["steps"]), 12)
        self.assertEqual({(s["tool"], s["mode"]) for s in out["steps"]}, {("preset_list", "series")})

    def test_call_user_command_invalid(self) -> None:
        from portskill import mcp

        self.settings()
        for name, cmd in (("key", {"name": "other", "steps": [{"tool": "preset_list"}]}),
                          ("x", {"name": "x", "steps": ["status"]})):
            with self.subTest(name=name):
                out = mcp.call_user_command(name, cmd)
                self.assertTrue(out["isError"])
                self.assertEqual(out["structuredContent"],
                                 {"ok": False, "error": "invalid_user_command", "name": name})

    def test_group_helper_raises_on_non_string_mode(self) -> None:
        from portskill import mcp

        with self.assertRaises(AttributeError):
            mcp._group_user_command_steps([{"tool": "preset_list", "mode": 5}])

    def test_mcp_tools_call_runs_saved_chain_with_alias(self) -> None:
        from portskill import mcp

        self.settings(mcp_user_commands={"mine": {"name": "mine", "steps": [
            {"tool": "portskill_path", "mode": "series",
             "arguments": {"mode": "status", "project": self.project}}]}})
        response = mcp.mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "mine", "arguments": {}}})
        body = response["result"]["structuredContent"]
        self.assertTrue(body["ok"])
        self.assertEqual(body["steps"][0]["tool"], "portskill_path")
        self.assertEqual(json.loads(response["result"]["content"][0]["text"]), body)


class NamedPolicyDifferenceTests(ChainCharacterizationBase):
    """Dry run vs runtime differences kept on purpose (decision C)."""

    def test_portskill_path_pref_false_pass(self) -> None:
        # Known false pass since c5ea284, kept by Ben's decision 2026-10-23:
        # the dry run reads the exact pref key, the runtime dual-reads the alias.
        self.settings(mcp_tools={"portskill_path": False})
        step = {"tool": "portskill", "arguments": {"mode": "status", "project": self.project}}
        dry = self.dry([step])
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["steps"][0]["message"], "ok")
        run = self.run_step(step)
        self.assertEqual(run, {
            "ok": False, "tool": "portskill", "mode": "series", "error": "tool_disabled",
            "message": "Step tool disabled in settings.mcp_tools: portskill",
        })

    def test_canonical_portskill_pref_refused_on_both_sides(self) -> None:
        self.settings(mcp_tools={"portskill": False})
        step = {"tool": "portskill", "arguments": {"mode": "status", "project": self.project}}
        self.assertFalse(self.dry([step])["ok"])
        self.assertEqual(self.run_step(step)["error"], "tool_disabled")

    def test_dry_run_clears_stop_arguments(self) -> None:
        # Dry run drops stop arguments then reports the required key missing;
        # the runtime passes them through to the owner.
        self.settings()
        step = {"tool": "stop", "arguments": {"range_id": "nope"}}
        dry = self.dry([step])
        self.assertEqual(dry["steps"][0]["arguments"], {})
        self.assertEqual(dry["steps"][0]["message"], "missing required: range_id")
        run = self.run_step(step)
        self.assertEqual(run["arguments"], {"range_id": "nope"})
        self.assertEqual(run["result"]["reason"], "not_found")


class UIDryRunRouteCharacterizationTests(ChainCharacterizationBase):
    def test_dispatch_status_codes(self) -> None:
        from portskill import server

        self.settings()
        cases = (
            ([], 400, {"ok": False, "message": "expected non-empty steps"}),
            ({"tool": "status"}, 400, {"ok": False, "message": "expected non-empty steps"}),
            ([{"tool": "preset_list"}] * 13, 400, {"ok": False, "message": "v1 max 12 steps"}),
        )
        for steps, status, body in cases:
            with self.subTest(steps=steps):
                self.assertEqual(
                    server.dispatch_ui_action({"action": "mcp-user-command-dry-run", "steps": steps}),
                    (status, body))
        code, body = server.dispatch_ui_action(
            {"action": "mcp-user-command-dry-run", "steps": [{"tool": "nope"}]})
        self.assertEqual((code, body["ok"]), (422, False))
        code, body = server.dispatch_ui_action(
            {"action": "mcp-user-command-dry-run", "steps": [{"tool": "preset_list"}]})
        self.assertEqual(code, 0)
        self.assertEqual(body, self.dry([{"tool": "preset_list"}]))


if __name__ == "__main__":
    unittest.main()
