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


if __name__ == "__main__":
    unittest.main()
