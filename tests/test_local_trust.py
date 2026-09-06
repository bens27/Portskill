"""Wave C: refuse non-loopback bind; surface-model copy in UI/docs."""
from __future__ import annotations

import io
import pathlib
import unittest
from contextlib import redirect_stdout

from tests.helpers import IsolatedConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NonLoopbackRefuseTests(unittest.TestCase):
    def test_gate_helpers(self) -> None:
        from port_registry_app.cli import (
            ALLOW_NON_LOOPBACK_FLAG,
            is_loopback_host,
            listen_allows_non_loopback,
            non_loopback_refuse_message,
        )

        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("127.4.5.6"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.10"))
        msg = non_loopback_refuse_message("0.0.0.0")
        self.assertIn("Refusing", msg)
        self.assertIn("0.0.0.0", msg)
        self.assertIn(ALLOW_NON_LOOPBACK_FLAG, msg)
        self.assertFalse(listen_allows_non_loopback({"host": "0.0.0.0"}))
        self.assertTrue(listen_allows_non_loopback({"allow_non_loopback": True}))

    def test_main_refuses_non_loopback_without_flag(self) -> None:
        from port_registry_app.server import main

        buf = io.StringIO()
        with IsolatedConfig() as iso:
            iso.write_registry()
            with redirect_stdout(buf):
                code = main(["--host", "0.0.0.0", "--no-open", "--no-auto-apply"])
            self.assertFalse(iso.listen_path.is_file())
        self.assertEqual(code, 2)
        out = buf.getvalue()
        self.assertIn("Refusing", out)
        self.assertIn("0.0.0.0", out)
        self.assertIn("--allow-non-loopback", out)

    def test_help_documents_footgun_flag(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "port_registry_app", "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("--allow-non-loopback", text)
        self.assertIn("FOOTGUN", text)


class SurfaceCopyDocTests(unittest.TestCase):
    def test_readme_and_security_document_surfaces_without_stdio_hierarchy(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        sidecar = (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
        for blob, name in (
            (readme, "README.md"),
            (security, "SECURITY.md"),
            (skill, "SKILL.md"),
            (sidecar, "skill/SKILL.md"),
        ):
            low = blob.lower()
            self.assertIn("stdio", low, f"{name} missing stdio")
            self.assertNotIn("dogfood", low, f"{name} still says dogfood")
            self.assertNotIn("prefer stdio", low, f"{name} still ranks stdio")
            self.assertNotIn("stdio preferred", low, f"{name} still says stdio preferred")
            self.assertNotIn("preferred agent path", low, f"{name} still prefers stdio")
            self.assertNotIn("preferred for agents", low, f"{name} still prefers stdio for agents")
            self.assertIn("funnel", low, f"{name} missing Funnel-of-listen fact")
        self.assertIn("first-class surface", readme.lower())
        self.assertIn("same local listener", readme.lower())
        self.assertIn("--allow-non-loopback", readme)
        self.assertIn("--allow-non-loopback", security)
        self.assertIn("refused", readme.lower())
        self.assertIn("refused", security.lower())
        self.assertIn("examples/mcp.stdio.json", readme)
        self.assertIn("*Human-written pre-amble*", readme)
        self.assertIn("*End of human-written pre-amble*", readme)

    def test_listen_hints_do_not_rank_stdio(self) -> None:
        import json

        from port_registry_app.mcp import discovery_payload
        from port_registry_app.server import build_listen_payload

        with IsolatedConfig() as iso:
            iso.write_registry()
            payload = build_listen_payload("127.0.0.1", 20000)
            iso.listen_path.write_text(
                json.dumps({
                    "host": "127.0.0.1",
                    "port": 20000,
                    "mcp_url": "http://127.0.0.1:20000/mcp",
                }),
                encoding="utf-8",
            )
            fallback = discovery_payload()["setup"]
        stdio = str(payload["setup"]["cursor_mcp_stdio_hint"]).lower()
        http = str(payload["setup"]["cursor_mcp_http_hint"]).lower()
        self.assertIn("one agent connection option", stdio)
        self.assertNotIn("prefer", stdio)
        self.assertNotIn("preferred", stdio)
        self.assertNotIn("dogfood", http)
        self.assertIn("same local listener", http)
        self.assertIn("blocked", http)
        self.assertNotIn("dogfood", str(fallback["cursor_mcp_http_hint"]).lower())
        self.assertNotIn("prefer stdio", str(fallback["cursor_mcp_http_hint"]).lower())
        self.assertIn("one agent connection option", fallback["cursor_mcp_stdio_hint"].lower())


if __name__ == "__main__":
    unittest.main()
