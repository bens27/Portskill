"""(c) MCP tools/list respects settings.mcp_tools toggles; call rejected."""
from __future__ import annotations

import unittest

from tests.helpers import IsolatedConfig


class McpToolToggleTests(unittest.TestCase):
    def test_tools_list_omits_disabled(self) -> None:
        from port_registry_app.mcp import TOOL_DEFS, enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {"allocate": False, "status": True}}})
            listed = enabled_tool_defs()
            names = [t["name"] for t in listed]
            self.assertNotIn("allocate", names)
            self.assertIn("status", names)
            all_system = {t["name"] for t in TOOL_DEFS}
            self.assertEqual(set(names) & all_system, all_system - {"allocate"})
            # tools/list JSON-RPC
            resp = mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            rpc_names = [t["name"] for t in resp["result"]["tools"]]
            self.assertNotIn("allocate", rpc_names)
            self.assertIn("status", rpc_names)

    def test_tools_call_disabled_rejected(self) -> None:
        from port_registry_app.mcp import mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {"allocate": False}}})
            resp = mcp_handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "allocate", "arguments": {"count": 1}},
            })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32001)
        self.assertIn("tool_disabled", resp["error"]["message"])
        self.assertIn("allocate", resp["error"]["message"])

    def test_missing_toggle_defaults_enabled(self) -> None:
        from port_registry_app.mcp import enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            names = [t["name"] for t in enabled_tool_defs()]
            self.assertIn("allocate", names)
            resp = mcp_handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            })
        self.assertNotIn("error", resp)
        self.assertIn("result", resp)


class McpToolsProfileTests(unittest.TestCase):
    LEAN_ENABLED = (
        "portskill",
        "status",
        "settings_get",
        "stop",
        "release",
        "allocate",
    )
    LEAN_HIDDEN_SAMPLES = (
        "activate",
        "start",
        "doctor",
        "preset_list",
        "settings_set",
        "handoff_status",
        "handoff_list",
        "handoff_new_path",
        "environment_export",
        "history_list",
    )

    def _apply_profile(self, iso, name: str):
        proc = iso.run_cli(["settings", "set", "--mcp-tools-profile", name])
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        from tests.helpers import parse_cli_json

        return parse_cli_json(proc)

    def test_default_install_stays_full(self) -> None:
        from port_registry_app.cli import default_settings, normalize_settings
        from port_registry_app.mcp import enabled_tool_defs, TOOL_DEFS

        settings = default_settings()
        self.assertEqual(settings.get("mcp_tools_profile"), "full")
        self.assertEqual(settings.get("mcp_tools"), {})
        normalized = normalize_settings({"mcp_tools": {}})
        self.assertEqual(normalized.get("mcp_tools_profile"), "full")
        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            names = {t["name"] for t in enabled_tool_defs()}
            all_system = {t["name"] for t in TOOL_DEFS}
            self.assertEqual(names & all_system, all_system)

    def test_apply_lean_writes_map_and_hides_disabled(self) -> None:
        from port_registry_app.mcp import LEAN_MCP_TOOLS_ENABLED, enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            payload = self._apply_profile(iso, "lean")
            settings = payload["settings"]
            self.assertEqual(settings["mcp_tools_profile"], "lean")
            tools = settings["mcp_tools"]
            for name in self.LEAN_ENABLED:
                self.assertTrue(tools.get(name), name)
            self.assertEqual(set(LEAN_MCP_TOOLS_ENABLED), set(self.LEAN_ENABLED))
            for name in self.LEAN_HIDDEN_SAMPLES:
                self.assertFalse(tools.get(name, True), name)

            listed = [t["name"] for t in enabled_tool_defs()]
            for name in self.LEAN_ENABLED:
                self.assertIn(name, listed)
            for name in self.LEAN_HIDDEN_SAMPLES:
                self.assertNotIn(name, listed)

            resp = mcp_handle({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
            rpc_names = [t["name"] for t in resp["result"]["tools"]]
            self.assertIn("portskill", rpc_names)
            self.assertNotIn("portskill_path", rpc_names)
            self.assertIn("status", rpc_names)
            self.assertNotIn("handoff_status", rpc_names)
            self.assertNotIn("activate", rpc_names)

    def test_lean_disabled_tools_call_rejected(self) -> None:
        from port_registry_app.mcp import mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            self._apply_profile(iso, "lean")
            hidden = mcp_handle({
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "handoff_status", "arguments": {}},
            })
            self.assertIn("error", hidden)
            self.assertEqual(hidden["error"]["code"], -32001)
            self.assertIn("tool_disabled", hidden["error"]["message"])
            self.assertIn("handoff_status", hidden["error"]["message"])

            crud = mcp_handle({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "activate", "arguments": {"range_id": "r1"}},
            })
            self.assertEqual(crud["error"]["code"], -32001)
            self.assertIn("activate", crud["error"]["message"])

            ok = mcp_handle({
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            })
            self.assertNotIn("error", ok)
            self.assertIn("result", ok)

    def test_apply_full_restores_all_tools(self) -> None:
        from port_registry_app.mcp import TOOL_DEFS, enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            self._apply_profile(iso, "lean")
            payload = self._apply_profile(iso, "full")
            settings = payload["settings"]
            self.assertEqual(settings["mcp_tools_profile"], "full")
            system = {t["name"] for t in TOOL_DEFS}
            leftover = set(settings.get("mcp_tools") or {}) & system
            self.assertEqual(leftover, set())

            names = {t["name"] for t in enabled_tool_defs()}
            self.assertEqual(names & system, system)
            resp = mcp_handle({
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "handoff_status", "arguments": {}},
            })
            self.assertNotIn("error", resp)

    def test_settings_set_mcp_applies_lean_profile(self) -> None:
        from port_registry_app.mcp import enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            applied = mcp_handle({
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {
                    "name": "settings_set",
                    "arguments": {"mcp_tools_profile": "lean"},
                },
            })
            self.assertNotIn("error", applied)
            body = applied["result"].get("structuredContent") or {}
            settings = body.get("settings") or {}
            self.assertEqual(settings.get("mcp_tools_profile"), "lean")
            names = [t["name"] for t in enabled_tool_defs()]
            self.assertIn("portskill", names)
            self.assertNotIn("portskill_path", names)
            self.assertNotIn("preset_save", names)
            self.assertNotIn("handoff_list", names)


if __name__ == "__main__":
    unittest.main()
