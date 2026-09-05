"""Wave C: refuse non-loopback bind; stdio preference in UI/docs."""
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


class StdioPreferenceDocTests(unittest.TestCase):
    def test_readme_and_security_prefer_stdio(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for blob, name in ((readme, "README.md"), (security, "SECURITY.md"), (skill, "SKILL.md")):
            low = blob.lower()
            self.assertIn("stdio", low, f"{name} missing stdio")
            self.assertIn("preferred", low, f"{name} missing preferred")
            self.assertIn("local-trust", low, f"{name} missing local-trust")
        self.assertIn("--allow-non-loopback", readme)
        self.assertIn("--allow-non-loopback", security)
        self.assertIn("refused", readme.lower())
        self.assertIn("refused", security.lower())
        self.assertIn("examples/mcp.stdio.json", readme)


if __name__ == "__main__":
    unittest.main()
