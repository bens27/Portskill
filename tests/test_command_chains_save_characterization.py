"""Characterization of the UI ``mcp-user-command-save`` chain validator.

Written against e358543 / c5ea284 BEFORE the save branch moved into
portskill.command_chains (slice 6, reviewer P2) and kept green through it.
Everything goes through the public server.dispatch_ui_action entry point and
the real CLI owner against an isolated config. The only test double is a
pass-through spy on settings.normalize_mcp_user_commands, the owner seam both
revisions share, recording the command the branch handed over before the owner
normalizes mode and truncates to 12 steps (persisted state alone would hide
the branch's own output).

The save validator is deliberately looser than the dry run and the runtime:
it only checks names against the system catalog. Each quirk below is pinned
by name so the extraction cannot change it silently.
"""
from __future__ import annotations

import unittest
from unittest import mock

from tests.helpers import IsolatedConfig

UNKNOWN = "each step.tool must be a system TOOL_DEFS name (got {!r})"


class SaveChainCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = IsolatedConfig().__enter__()
        self.addCleanup(self.cfg.__exit__, None, None, None)

    def settings(self, **settings) -> None:
        self.cfg.write_registry({"settings": settings})

    def save(self, steps, name="chain", **extra):
        from portskill import server, settings

        self.handed = []
        real = settings.normalize_mcp_user_commands

        def spy(commands):
            # First call per save is the single upserted {name: command}.
            if not self.handed:
                self.handed.append(dict(commands))
            return real(commands)

        with mock.patch.object(settings, "normalize_mcp_user_commands", spy):
            return server.dispatch_ui_action(
                {"action": "mcp-user-command-save", "name": name, "steps": steps, **extra}
            )

    def saved(self, steps, name="chain", **extra) -> dict:
        """Command the branch handed to the owner; checks it was persisted."""
        code, body = self.save(steps, name, **extra)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertEqual(len(self.handed), 1)
        ((key, cmd),) = self.handed[0].items()
        self.assertEqual(key, cmd["name"])
        self.assertIn(cmd["name"], body["result"]["settings"]["mcp_user_commands"])
        return cmd

    def rejected(self, steps, name="chain", **extra) -> str:
        code, body = self.save(steps, name, **extra)
        self.assertEqual(code, 400, body)
        self.assertEqual(set(body), {"ok", "message"})
        self.assertFalse(body["ok"])
        self.assertEqual(self.handed, [])
        return body["message"]

    def test_valid_steps_saved_with_raw_mode_and_dict_arguments(self) -> None:
        self.settings()
        cmd = self.saved(
            [
                {"tool": "status", "arguments": {"project": "/p"}, "mode": "parallel"},
                {"tool": "preset_list", "mode": " PARALLEL "},
                {"tool": "settings_get", "mode": ""},
                {"tool": "preset_list", "arguments": ["x"]},
            ],
            description="my chain",
        )
        self.assertEqual(cmd, {
            "name": "chain",
            "description": "my chain",
            "steps": [
                {"tool": "status", "arguments": {"project": "/p"}, "mode": "parallel"},
                {"tool": "preset_list", "arguments": {}, "mode": " PARALLEL "},
                {"tool": "settings_get", "arguments": {}, "mode": "series"},
                {"tool": "preset_list", "arguments": {}, "mode": "series"},
            ],
        })

    def test_non_string_description_becomes_empty(self) -> None:
        self.settings()
        self.assertEqual(self.saved([{"tool": "preset_list"}], description=5)["description"], "")

    def test_stop_arguments_cleared(self) -> None:
        self.settings()
        cmd = self.saved([{"tool": "stop", "arguments": {"range_id": "r"}}])
        self.assertEqual(cmd["steps"], [{"tool": "stop", "arguments": {}, "mode": "series"}])

    def test_arguments_are_not_schema_checked(self) -> None:
        self.settings()
        cmd = self.saved([{"tool": "status", "arguments": {"project": 5, "bogus": 1}},
                          {"tool": "preset_apply"}])
        self.assertEqual(cmd["steps"][0]["arguments"], {"project": 5, "bogus": 1})

    def test_unknown_alias_and_padded_tools_rejected_with_step_repr(self) -> None:
        self.settings()
        for step in ({"tool": "nope"}, {"tool": "portskill_path"}, {"tool": " status"},
                     {"tool": None}, {"tool": 7}, {}, {"tool": "--help"}):
            with self.subTest(step=step):
                self.assertEqual(self.rejected([step]), UNKNOWN.format(step))

    def test_non_object_step_rejected_with_its_repr(self) -> None:
        self.settings()
        self.assertEqual(self.rejected([{"tool": "preset_list"}, "status", {"tool": "zz"}]),
                         UNKNOWN.format("status"))

    def test_first_bad_step_wins(self) -> None:
        self.settings()
        self.assertEqual(self.rejected([{"tool": "a"}, {"tool": "b"}]), UNKNOWN.format({"tool": "a"}))

    def test_unhashable_tool_raises(self) -> None:
        # Pinned quirk: set membership on a list/dict tool raises TypeError.
        self.settings()
        for tool in (["status"], {"a": 1}):
            with self.subTest(tool=tool), self.assertRaises(TypeError):
                self.save([{"tool": tool}])

    def test_disabled_tool_is_accepted(self) -> None:
        self.settings(mcp_tools={"compat_check": False, "portskill_path": False})
        cmd = self.saved([{"tool": "compat_check"}, {"tool": "portskill"}])
        self.assertEqual([s["tool"] for s in cmd["steps"]], ["compat_check", "portskill"])

    def test_nested_user_command_rejected_only_as_unknown(self) -> None:
        self.settings(mcp_user_commands={"mine": {"name": "mine", "steps": [{"tool": "preset_list"}]}})
        self.assertEqual(self.rejected([{"tool": "mine"}]), UNKNOWN.format({"tool": "mine"}))

    def test_system_named_user_command_is_not_treated_as_nested(self) -> None:
        # Needs a real step: the registry drops step-less commands on read.
        self.settings(mcp_user_commands={"preset_list": {"name": "preset_list",
                                                         "steps": [{"tool": "status"}]}})
        from portskill.registry import read_settings

        self.assertIn("preset_list", read_settings()["mcp_user_commands"])
        self.assertEqual(self.saved([{"tool": "preset_list"}])["steps"][0]["tool"], "preset_list")

    def test_more_than_twelve_steps_handed_to_owner(self) -> None:
        self.settings()
        self.assertEqual(len(self.saved([{"tool": "preset_list"}] * 13)["steps"]), 13)

    def test_owner_normalizes_what_the_branch_hands_over(self) -> None:
        code, body = self.save([{"tool": "preset_list", "mode": " PARALLEL "}] * 13)
        self.assertEqual(code, 0)
        stored = body["result"]["settings"]["mcp_user_commands"]["chain"]["steps"]
        self.assertEqual(len(stored), 12)
        self.assertEqual({s["mode"] for s in stored}, {"parallel"})

    def test_name_and_steps_shape_messages(self) -> None:
        self.settings()
        cases = (
            ({"name": None, "steps": [{"tool": "preset_list"}]}, "expected name"),
            ({"name": "  ", "steps": [{"tool": "preset_list"}]}, "expected name"),
            ({"name": "bad name", "steps": [{"tool": "preset_list"}]},
             "name must be letters, numbers, _ or -"),
            ({"name": "--opt=val", "steps": [{"tool": "preset_list"}]},
             "name must be letters, numbers, _ or -"),
            ({"name": "status", "steps": [{"tool": "nope"}]}, "cannot shadow system tool: status"),
            ({"name": "chain", "steps": []}, "expected non-empty steps"),
            ({"name": "chain", "steps": {"tool": "status"}}, "expected non-empty steps"),
            ({"name": "status", "steps": []}, "cannot shadow system tool: status"),
        )
        from portskill import server

        for extra, message in cases:
            with self.subTest(extra=extra):
                code, body = server.dispatch_ui_action({"action": "mcp-user-command-save", **extra})
                self.assertEqual((code, body), (400, {"ok": False, "message": message}))

    def test_name_is_stripped(self) -> None:
        self.settings()
        self.assertEqual(self.saved([{"tool": "preset_list"}], name="  padded  ")["name"], "padded")

    def test_option_like_names(self) -> None:
        # Pinned quirk: the name regex lets option-like names through; the CLI
        # owner then parses some of them as flags (exit 2; 422 over HTTP).
        self.settings()
        for name in ("-1", "_x"):
            with self.subTest(name=name):
                self.assertEqual(self.saved([{"tool": "preset_list"}], name=name)["name"], name)
        for name in ("-x", "--", "-"):
            with self.subTest(name=name):
                code, body = self.save([{"tool": "preset_list"}], name=name)
                self.assertEqual(code, 2)
                self.assertEqual(self.handed, [])  # argparse fails before the upsert
                self.assertIn("expected one argument", body["message"])


if __name__ == "__main__":
    unittest.main()
