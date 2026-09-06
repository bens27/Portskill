"""(e) Compose / System Tools details start collapsed; loopback banner markup."""
from __future__ import annotations

import re
import unittest

def _details_tags(markup: str, cls: str) -> list[str]:
    tags = re.findall(r"<details\b[^>]*>", markup)
    return [t for t in tags if cls in t]


def _has_open_attr(tag: str) -> bool:
    return re.search(r"\sopen(\s|=|/|>)", tag) is not None


class CollapsedMarkupTests(unittest.TestCase):
    def _render_with_project(self) -> str:
        from port_registry_app.server import build_view, render_page

        raw = {
            "version": 1,
            "pool": {"start": 20000, "end": 29999},
            "projects": {
                "/tmp/portskill-demo": {
                    "ranges": [{
                        "id": "r-demo",
                        "start": 20001,
                        "end": 20001,
                        "state": "reserved",
                        "note": "demo",
                        "tailnet": {"mode": "none"},
                        "default_state": "off",
                    }],
                }
            },
            "presets": {},
            "settings": {},
        }
        view = build_view(raw)
        html = render_page(
            view,
            tailscale={"chip": "Needs login", "state": "needs_login", "logged_in": False},
        )
        return html

    def test_system_tools_and_repo_details_start_collapsed(self) -> None:
        from tests.helpers import IsolatedConfig

        with IsolatedConfig() as iso:
            iso.write_registry()
            html = self._render_with_project()
        markup = html.split("</style>", 1)[-1]
        system = _details_tags(markup, "pr-mcp-system-details")
        self.assertTrue(system, "pr-mcp-system-details missing from rendered HTML")
        for tag in system:
            self.assertFalse(_has_open_attr(tag), f"System tools details not collapsed: {tag}")
            self.assertIn("pr-mcp-system-details", tag)

        handoff = _details_tags(markup, "pr-handoff-details")
        self.assertTrue(handoff, "pr-handoff-details missing from rendered HTML")
        for tag in handoff:
            self.assertFalse(_has_open_attr(tag), f"Session Handoff details not collapsed: {tag}")
            self.assertIn("pr-handoff-details", tag)

        projects = _details_tags(markup, "pr-project")
        self.assertTrue(projects, "details.pr-project missing from rendered HTML")
        for tag in projects:
            self.assertFalse(_has_open_attr(tag), f"repo details not collapsed: {tag}")
            self.assertIn("pr-project", tag)

        settings = _details_tags(markup, "pr-subpanel")
        self.assertTrue(settings, "details.pr-subpanel (Settings) missing from rendered HTML")
        for tag in settings:
            self.assertFalse(_has_open_attr(tag), f"Settings details not collapsed: {tag}")
            self.assertIn("pr-subpanel", tag)
        self.assertIn('id="pr-settings"', html)
        self.assertIn("pr-disclose-hint", html)

    def test_serve_url_details_start_collapsed_with_chevron_hint(self) -> None:
        from port_registry_app.server import portskill_serve_chip_html

        html = portskill_serve_chip_html(
            {
                "chip": "Serving",
                "state": "serving",
                "serve_url": "https://example.ts.net",
                "message": "ok",
            }
        )
        tags = _details_tags(html, "pr-serve-url-details")
        self.assertTrue(tags, "Serve URL details missing when serving")
        for tag in tags:
            self.assertFalse(_has_open_attr(tag), f"Serve URL details not collapsed: {tag}")
        self.assertIn("pr-disclose-hint", html)
        self.assertIn("Serve URL", html)

    def test_loopback_has_no_bind_banner(self) -> None:
        from tests.helpers import IsolatedConfig, active_listen

        with IsolatedConfig() as iso, active_listen({
            "host": "127.0.0.1",
            "port": 20000,
            "ui_url": "http://127.0.0.1:20000/",
            "mcp_url": "http://127.0.0.1:20000/mcp",
        }):
            iso.write_registry()
            html = self._render_with_project()
        self.assertNotIn('id="pr-bind-banner"', html)
        self.assertNotIn('id="pr-bind-chip"', html)
        self.assertNotIn("Not loopback", html)

    def test_non_loopback_shows_banner_and_chip(self) -> None:
        from tests.helpers import IsolatedConfig, active_listen

        with IsolatedConfig() as iso, active_listen({
            "host": "0.0.0.0",
            "port": 20000,
            "ui_url": "http://0.0.0.0:20000/",
            "mcp_url": "http://0.0.0.0:20000/mcp",
        }):
            iso.write_registry()
            html = self._render_with_project()
        self.assertIn('id="pr-bind-banner"', html)
        self.assertIn('id="pr-bind-chip"', html)
        self.assertIn("Not loopback", html)
        self.assertIn("0.0.0.0", html)
        self.assertIn("not loopback", html.lower())
        self.assertIn("--allow-non-loopback", html)

    def test_mcp_panel_describes_http_and_stdio_without_ranking(self) -> None:
        from tests.helpers import IsolatedConfig

        with IsolatedConfig() as iso:
            iso.write_registry()
            html = self._render_with_project()
        self.assertIn('id="pr-mcp-connect"', html)
        self.assertNotIn("stdio preferred", html.lower())
        self.assertNotIn("preferred for agents", html.lower())
        self.assertNotIn("dogfood", html.lower())
        self.assertIn("HTTP MCP uses the same local listener as this UI", html)
        self.assertIn("does not require a token unless the optional passkey gate is enabled", html)
        self.assertIn("--mcp-stdio", html)
        self.assertIn("mcpServers", html)
        self.assertIn('id="pr-mcp-stdio-config"', html)
        self.assertIn('id="pr-mcp-stdio-copy"', html)
        self.assertIn("Copy stdio config", html)
        self.assertIn('id="pr-mcp-http-note"', html)
        self.assertIn("Stdio MCP (agent install option)", html)
        markup = html.split("</style>", 1)[-1]
        connect = _details_tags(markup, "pr-mcp-connect-details")
        self.assertTrue(connect, "Agent Connection details missing")
        for tag in connect:
            self.assertFalse(_has_open_attr(tag), f"Agent Connection details not collapsed: {tag}")
        self.assertIn('id="pr-mcp-connect-stdio"', html)
        self.assertIn('id="pr-mcp-connect-http"', html)

    def test_collapsed_project_summary_shows_counts(self) -> None:
        from port_registry_app.server import build_view, render_page

        from tests.helpers import IsolatedConfig

        raw = {
            "version": 1,
            "pool": {"start": 20000, "end": 29999},
            "projects": {
                "/tmp/portskill-counts": {
                    "ranges": [
                        {
                            "id": "r-active-serve",
                            "start": 20001,
                            "end": 20001,
                            "state": "active",
                            "note": "served",
                            "tailnet": {"mode": "serve"},
                            "default_state": "on",
                        },
                        {
                            "id": "r-reserved",
                            "start": 20002,
                            "end": 20002,
                            "state": "reserved",
                            "note": "idle",
                            "tailnet": {"mode": "none"},
                            "default_state": "off",
                        },
                    ],
                }
            },
            "presets": {},
            "settings": {},
        }
        with IsolatedConfig() as iso:
            iso.write_registry(raw)
            html = render_page(
                build_view(raw),
                tailscale={"chip": "Needs login", "state": "needs_login", "logged_in": False},
            )
        markup = html.split("</style>", 1)[-1]
        tags = _details_tags(markup, "pr-project")
        self.assertTrue(tags)
        for tag in tags:
            self.assertFalse(_has_open_attr(tag), f"project details not collapsed: {tag}")
        self.assertIn("2 registered", html)
        self.assertIn("1 active", html)
        self.assertIn("1 Tailnet-served", html)
        self.assertIn("data-pr-project-counts", html)


class AdHocCodesignScriptTests(unittest.TestCase):
    def test_build_app_documents_adhoc_and_darwin_gate(self) -> None:
        import pathlib
        import subprocess

        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "scripts" / "build-app.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("codesign --force --deep --sign -", text)
        self.assertIn("uname -s", text)
        self.assertIn("Darwin", text)
        self.assertIn("Ad-hoc", text)
        self.assertIn("not notarized", text.lower())
        self.assertIn("strip_finder_junk", text)
        self.assertIn("replace_app_bundle", text)
        syn = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(syn.returncode, 0, syn.stderr or syn.stdout)


class PlaybookTests(unittest.TestCase):
    def test_playbook_retired_developer_path(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "PLAYBOOK.md").read_text(encoding="utf-8")
        self.assertIn("retired", text.lower())
        self.assertIn("not** a supported", text.lower())
        self.assertIn("README.md", text)
        self.assertIn("./scripts/run.sh", text)
        self.assertNotIn("right-click", text.lower())
        self.assertNotIn("AirDrop", text)
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("./scripts/run.sh", readme)
        self.assertIn("Quick start", readme)
        self.assertNotIn("Mac friends", readme)
        self.assertIn("does not ship a notarized or signed binary", readme.lower())
        self.assertIn("./scripts/build-app.sh", readme)
        self.assertIn("./scripts/install-keepalive.sh", readme)
        self.assertNotIn("install-mac.sh", readme)
        self.assertNotIn("notarize-mac.sh", readme)
        security = (root / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("No signed / notarized distribution", security)
        self.assertNotIn("install-mac.sh", security)
        self.assertNotIn("notarize-mac.sh", security)


if __name__ == "__main__":
    unittest.main()
