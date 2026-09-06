"""Session Handoff UI section + MCP tools (vendored kit, stdlib)."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import IsolatedConfig
from tests.test_ui_collapsed import _details_tags, _has_open_attr

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "vendor" / "session-handoff-kit" / "codex" / "hooks" / "handoff_ledger.py"
SKILL = ROOT / "vendor" / "session-handoff-kit" / "codex" / "skills" / "session-handoff" / "SKILL.md"

HANDOFF_TOOLS = (
    "handoff_status",
    "handoff_skill",
    "handoff_template",
    "handoff_list",
    "handoff_resolve",
    "handoff_new_path",
    "handoff_resume",
    "handoff_supersede",
    "handoff_install_help",
)


class HandoffMarkupTests(unittest.TestCase):
    def test_section_exists_collapsed_not_coming_soon(self) -> None:
        from port_registry_app.server import build_view, render_page

        with IsolatedConfig() as iso:
            iso.write_registry()
            html = render_page(
                build_view({}),
                tailscale={"chip": "Needs login", "state": "needs_login", "logged_in": False},
            )
        self.assertIn("Session Handoff", html)
        self.assertIn('id="pr-handoff-details"', html)
        self.assertNotIn("Coming soon", html)
        markup = html.split("</style>", 1)[-1]
        tags = _details_tags(markup, "pr-handoff-details")
        self.assertTrue(tags)
        for tag in tags:
            self.assertFalse(_has_open_attr(tag), tag)
        self.assertIn("Kit present", html)
        self.assertIn("Enable / add", html)
        self.assertIn("Install matrix", html)
        self.assertIn("Add / manage", html)
        self.assertIn("Claude Code", html)
        self.assertIn("Claude Cowork", html)
        self.assertIn("Codex CLI", html)
        self.assertIn("Claude chat / Desktop", html)
        self.assertIn("Chrome / Edge extension", html)
        self.assertIn('data-pr-action="handoff-copy"', html)
        self.assertIn('data-pr-action="handoff-package"', html)
        self.assertIn('data-pr-action="handoff-codex-install"', html)
        self.assertIn("/plugin marketplace add", html)
        self.assertIn("package.sh", html)
        self.assertIn("Copy add commands", html)
        self.assertIn("Package plugin", html)
        self.assertIn("Run install.sh", html)
        self.assertIn("Package skill", html)
        self.assertIn("Copy extension path", html)
        self.assertIn("Portskill cannot run /plugin", html)
        self.assertIn("handoff_status", html)
        self.assertIn("not nested", html)
        self.assertIn("session-handoff/*", html)
        self.assertIn("Write-a-Handoff skill", html)
        self.assertIn('data-pr-action="handoff-skill-download"', html)
        self.assertIn('id="pr-handoff-skill-file"', html)
        self.assertIn("~/.config/port-registry/", html)

    def test_flat_tool_names_live_in_handoff_not_mcp_panel(self) -> None:
        from port_registry_app.server import (
            build_view,
            handoff_panel_html,
            mcp_tools_panel_html,
        )

        with IsolatedConfig() as iso:
            iso.write_registry()
            view = build_view({})
        mcp = mcp_tools_panel_html(view)
        handoff = handoff_panel_html(view)
        self.assertNotIn("session-handoff/*", mcp)
        self.assertNotIn("not nested", mcp)
        self.assertIn("session-handoff/*", handoff)
        self.assertIn("handoff_status", handoff)
        self.assertIn("not nested", handoff)
        self.assertIn("x-portskill-kind: user-command", mcp)
        self.assertIn("same enable map", mcp)
        sys_i = mcp.find('id="pr-mcp-system-details"')
        conn_i = mcp.find('id="pr-mcp-connect"')
        user_i = mcp.find('id="pr-mcp-user-commands-section"')
        comp_i = mcp.find('id="pr-mcp-user-composer"')
        self.assertTrue(0 <= sys_i < conn_i < user_i < comp_i, (sys_i, conn_i, user_i, comp_i))
        self.assertIn('id="pr-mcp-connect-stdio"', mcp)
        self.assertIn('id="pr-mcp-connect-http"', mcp)
        self.assertNotIn(" open", mcp.split('id="pr-mcp-connect-stdio"', 1)[0][-80:])


class HandoffMcpTests(unittest.TestCase):
    def test_tools_list_includes_handoff_by_default(self) -> None:
        from port_registry_app.mcp import enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": {}}})
            names = [t["name"] for t in enabled_tool_defs()]
            resp = mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        for name in HANDOFF_TOOLS:
            self.assertIn(name, names)
        rpc_names = [t["name"] for t in resp["result"]["tools"]]
        for name in HANDOFF_TOOLS:
            self.assertIn(name, rpc_names)
        for name in names:
            self.assertFalse(name.startswith("session-handoff/"), name)

    def test_vendored_md_pins_kit_sha(self) -> None:
        text = (ROOT / "vendor" / "session-handoff-kit" / "VENDORED.md").read_text(encoding="utf-8")
        self.assertIn("7587834", text)
        self.assertIn("/Users/bens/Development/handoff-manager/", text)
        self.assertIn("Session Handoff Kit", text)

    def test_tools_list_omits_when_toggled_off(self) -> None:
        from port_registry_app.mcp import enabled_tool_defs, mcp_handle

        off = {name: False for name in HANDOFF_TOOLS}
        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"mcp_tools": off}})
            names = [t["name"] for t in enabled_tool_defs()]
            resp = mcp_handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            call = mcp_handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "handoff_status", "arguments": {}},
            })
        for name in HANDOFF_TOOLS:
            self.assertNotIn(name, names)
        rpc_names = [t["name"] for t in resp["result"]["tools"]]
        for name in HANDOFF_TOOLS:
            self.assertNotIn(name, rpc_names)
        self.assertIn("error", call)
        self.assertEqual(call["error"]["code"], -32001)

    def test_skill_and_status_without_override(self) -> None:
        from port_registry_app.mcp import mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry()
            status = mcp_handle({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "handoff_status", "arguments": {}},
            })
            skill = mcp_handle({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "handoff_skill", "arguments": {}},
            })
            template = mcp_handle({
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "handoff_template", "arguments": {}},
            })
        st = status["result"]["structuredContent"]
        self.assertTrue(st.get("ok"))
        self.assertTrue(st.get("installed"))
        self.assertIn("vendor/session-handoff-kit", st.get("kit_path") or "")
        self.assertIsNone(st.get("error"))
        sk = skill["result"]["structuredContent"]
        self.assertIn("name: session-handoff", sk.get("text") or "")
        self.assertIn("0.7.0", sk.get("text") or "")
        tm = template["result"]["structuredContent"]
        self.assertIn("status:", tm.get("text") or "")

    def test_list_via_vendored_ledger_on_temp_dir(self) -> None:
        from port_registry_app.mcp import mcp_handle

        self.assertTrue(LEDGER.is_file(), f"missing vendored ledger: {LEDGER}")
        with tempfile.TemporaryDirectory(prefix="handoff-ledger-") as tmp:
            root = pathlib.Path(tmp)
            handoffs = root / ".handoffs"
            handoffs.mkdir()
            sample = handoffs / "20260905-1200-portskill-demo.md"
            sample.write_text(
                "---\n"
                "topic: portskill-demo\n"
                "status: open\n"
                "description: fixture for Portskill ledger wrap\n"
                "created: 2026-09-05T12:00\n"
                "---\n"
                "# demo\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(LEDGER), "list", str(root), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            listed = json.loads(proc.stdout)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["topic"], "portskill-demo")

            with IsolatedConfig() as iso:
                iso.write_registry()
                resp = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_list",
                        "arguments": {"project": str(root)},
                    },
                })
            body = resp["result"]["structuredContent"]
            self.assertTrue(body.get("ok"))
            self.assertEqual(body.get("count"), 1)
            self.assertEqual(body["handoffs"][0]["topic"], "portskill-demo")

    def test_ledger_resolve_new_path_resume_supersede(self) -> None:
        from port_registry_app.mcp import mcp_handle

        self.assertTrue(LEDGER.is_file(), f"missing vendored ledger: {LEDGER}")
        with tempfile.TemporaryDirectory(prefix="handoff-ledger-ops-") as tmp:
            root = pathlib.Path(tmp)
            handoffs = root / ".handoffs"
            handoffs.mkdir()
            sample = handoffs / "20260905-1300-ledger-ops.md"
            sample.write_text(
                "---\n"
                "topic: ledger-ops\n"
                "status: open\n"
                "description: fixture for resolve/resume/supersede\n"
                "created: 2026-09-05T13:00\n"
                "---\n"
                "# ops\n",
                encoding="utf-8",
            )
            other = handoffs / "20260905-1305-other-topic.md"
            other.write_text(
                "---\n"
                "topic: other-topic\n"
                "status: open\n"
                "description: second open handoff\n"
                "created: 2026-09-05T13:05\n"
                "---\n"
                "# other\n",
                encoding="utf-8",
            )
            with IsolatedConfig() as iso:
                iso.write_registry()
                resolved = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 20,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_resolve",
                        "arguments": {"topic": "ledger-ops", "project": str(root)},
                    },
                })
                new_path = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 21,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_new_path",
                        "arguments": {"topic": "fresh-topic", "project": str(root)},
                    },
                })
                resumed = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 22,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_resume",
                        "arguments": {"path": str(sample)},
                    },
                })
                superseded = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 23,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_supersede",
                        "arguments": {"path": str(other), "by": str(sample)},
                    },
                })
                listed = mcp_handle({
                    "jsonrpc": "2.0",
                    "id": 24,
                    "method": "tools/call",
                    "params": {
                        "name": "handoff_list",
                        "arguments": {"project": str(root)},
                    },
                })
            body = resolved["result"]["structuredContent"]
            self.assertTrue(body.get("ok"))
            self.assertEqual(body.get("topic"), "ledger-ops")
            self.assertEqual(body.get("authoritative"), str(sample))
            self.assertEqual(len(body.get("chain") or []), 1)
            np_body = new_path["result"]["structuredContent"]
            self.assertTrue(np_body.get("ok"))
            self.assertIn("fresh-topic", np_body.get("filename") or "")
            self.assertIn(str(handoffs), np_body.get("path") or "")
            self.assertIn("created", np_body)
            self.assertFalse(pathlib.Path(np_body["path"]).exists(), "new-path must not write a file")
            self.assertTrue(resumed["result"]["structuredContent"].get("ok"))
            self.assertTrue(superseded["result"]["structuredContent"].get("ok"))
            self.assertIn("status: resumed", sample.read_text(encoding="utf-8"))
            self.assertIn("status: superseded", other.read_text(encoding="utf-8"))
            listed_body = listed["result"]["structuredContent"]
            self.assertTrue(listed_body.get("ok"))
            self.assertEqual(listed_body.get("count"), 0)

    def test_codex_install_writes_isolated_home(self) -> None:
        from port_registry_app.handoff import run_codex_install

        with tempfile.TemporaryDirectory(prefix="handoff-codex-") as tmp:
            result = run_codex_install(codex_home=tmp)
            home = pathlib.Path(tmp)
            self.assertTrue(result.get("ok"), result)
            self.assertTrue((home / "hooks" / "handoff_ledger.py").is_file())
            self.assertTrue((home / "hooks" / "context_watch.py").is_file())
            self.assertTrue((home / "skills" / "session-handoff" / "SKILL.md").is_file())
            self.assertTrue((home / "hooks.json").is_file())
            self.assertIn("config.toml", result.get("honesty") or "")

    def test_list_fails_closed_when_kit_override_invalid(self) -> None:
        from port_registry_app.handoff import call_handoff_tool

        with IsolatedConfig() as iso:
            iso.write_registry()
            missing = iso.root / "not-a-kit"
            missing.mkdir()
            result = call_handoff_tool(
                "handoff_list",
                {"project": str(iso.root)},
                settings={"handoff_kit": str(missing)},
            )
        self.assertTrue(result.get("isError"))
        body = result.get("structuredContent") or {}
        self.assertEqual(body.get("error"), "kit_missing")


class HandoffCustomSkillTests(unittest.TestCase):
    def test_upload_persists_and_mcp_reads_custom_skill(self) -> None:
        from port_registry_app.handoff import default_custom_skill_path
        from port_registry_app.mcp import mcp_handle
        from port_registry_app.server import (
            build_view,
            dispatch_ui_action,
            handoff_panel_html,
            load_registry,
        )

        custom = (
            "---\nname: session-handoff\nmetadata:\n  version: \"custom-test\"\n---\n"
            "# Custom Write-a-Handoff\n\nreplacement skill body\n"
        )
        with IsolatedConfig() as iso:
            iso.write_registry()
            code, body = dispatch_ui_action({
                "action": "handoff-skill-upload",
                "filename": "SKILL.md",
                "content": custom,
            })
            self.assertEqual(code, 0, body)
            self.assertTrue(body.get("ok"), body)
            stored = default_custom_skill_path()
            self.assertTrue(stored.is_file(), stored)
            self.assertIn("replacement skill body", stored.read_text(encoding="utf-8"))
            shown = iso.run_cli(["settings", "get"])
            self.assertEqual(shown.returncode, 0, shown.stderr)
            settings = json.loads(shown.stdout).get("settings") or {}
            self.assertEqual(settings.get("handoff_skill"), str(stored))

            skill = mcp_handle({
                "jsonrpc": "2.0",
                "id": 40,
                "method": "tools/call",
                "params": {"name": "handoff_skill", "arguments": {}},
            })
            text = skill["result"]["structuredContent"].get("text") or ""
            self.assertIn("replacement skill body", text)
            self.assertIn("custom-test", text)

            html = handoff_panel_html(build_view(load_registry()))
            self.assertIn("skill: custom", html)
            self.assertIn(str(stored), html)

    def test_download_bundled_skill_for_review(self) -> None:
        from port_registry_app.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            iso.write_registry()
            code, body = dispatch_ui_action({"action": "handoff-skill-download"})
        self.assertEqual(code, 0, body)
        self.assertTrue(body.get("ok"), body)
        self.assertIn("name: session-handoff", body.get("download") or "")
        self.assertEqual(body.get("filename"), "SKILL.md")
        self.assertEqual(body.get("source"), "vendored")

    def test_clear_restores_bundled_skill(self) -> None:
        from port_registry_app.mcp import mcp_handle
        from port_registry_app.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            iso.write_registry()
            dispatch_ui_action({
                "action": "handoff-skill-upload",
                "content": "# custom only\n",
            })
            code, body = dispatch_ui_action({"action": "handoff-skill-clear"})
            self.assertEqual(code, 0, body)
            skill = mcp_handle({
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "handoff_skill", "arguments": {}},
            })
        text = skill["result"]["structuredContent"].get("text") or ""
        self.assertIn("name: session-handoff", text)
        self.assertNotIn("# custom only", text)

    def test_skill_upload_works_without_http_bearer(self) -> None:
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer

        from port_registry_app.server import Handler
        from tests.helpers import free_loopback_port

        with IsolatedConfig() as iso:
            iso.write_registry()
            port = free_loopback_port()
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{port}/port-registry/actions"
            payload = json.dumps({
                "action": "handoff-skill-upload",
                "content": "# noauth\n",
            }).encode("utf-8")
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(int(getattr(resp, "status", None) or resp.getcode()), 200)
                    body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(body.get("ok"), body)
            finally:
                httpd.shutdown()
                httpd.server_close()


class HandoffVendorTests(unittest.TestCase):
    def test_vendored_skill_front_matter(self) -> None:
        self.assertTrue(SKILL.is_file())
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: session-handoff", text)
        self.assertIn("0.7.0", text)
        self.assertIn("context-watch", text)
        readme = ROOT / "vendor" / "session-handoff-kit" / "README.md"
        self.assertTrue(readme.is_file())
        self.assertTrue(readme.read_text(encoding="utf-8").startswith("# Session Handoff Kit"))


if __name__ == "__main__":
    unittest.main()
