"""--mcp-user-command-upsert: a non-string name is invalid_args, not settings_unavailable."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from tests.helpers import IsolatedConfig, ROOT

STEPS = [{"tool": "status", "arguments": {}, "mode": "series"}]


def _cli(*argv: str):
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else str(ROOT) + os.pathsep + existing
    done = subprocess.run(
        [sys.executable, "-m", "portskill.cli", *argv],
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )
    lines = (done.stdout or "").strip().splitlines()
    return done.returncode, (json.loads(lines[-1]) if lines else None), done.stderr


class UpsertNameTests(unittest.TestCase):
    def _settings(self, iso) -> dict:
        return json.loads(iso.registry_path.read_text()).get("settings") or {}

    def _upsert(self, body: dict):
        return _cli("settings", "set", "--mcp-user-command-upsert", json.dumps(body))

    def test_list_or_dict_name_is_invalid_args(self) -> None:
        for name in (["deploy"], {"deploy": 1}):
            with self.subTest(name=name), IsolatedConfig() as iso:
                iso.write_registry()
                before = self._settings(iso)
                code, payload, stderr = self._upsert({"name": name, "steps": STEPS})
                self.assertEqual(payload["reason"], "invalid_args", payload)
                self.assertIn("name must be a string", payload["message"])
                self.assertNotEqual(code, 0)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(self._settings(iso), before)  # nothing written

    def test_string_name_still_upserts(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            code, payload, _ = self._upsert({"name": " deploy ", "description": "d", "steps": STEPS})
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(self._settings(iso)["mcp_user_commands"], {
                "deploy": {"name": "deploy", "description": "d", "steps": STEPS},
            })

    def test_missing_name_still_falls_back_as_before(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            code, payload, _ = self._upsert({"steps": STEPS})
            self.assertEqual(code, 0, payload)
            self.assertIn("cmd", self._settings(iso)["mcp_user_commands"])


if __name__ == "__main__":
    unittest.main()
