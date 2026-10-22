"""Shared registry paths, settings normalization, and adapter consistency."""
from __future__ import annotations

import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class RegistrySettingsTests(unittest.TestCase):
    def test_cli_server_and_mcp_share_normalized_settings(self) -> None:
        from portskill.cli import normalize_settings
        from portskill.mcp import _load_settings_from_registry
        from portskill.server import load_registry

        raw_settings = {
            "auto_apply_preset": "  demo  ",
            "auto_apply_on_launch": "yes",
            "auto_exit_on_shutdown": "off",
            "overlap_policy": " DENY ",
            "require_compat": False,
            "open_environment_tabs": [" alpha ", "alpha", "", 42, "beta"],
            "focused_environment": " beta ",
            "mcp_tools": {" status ": "off", "allocate": "yes", "": True},
            "mcp_tools_profile": " LEAN ",
            "stop_also_release": "0",
            "default_tailnet": " SERVE ",
            "serve_portskill_on_tailscale": "true",
            "handoff_enabled": "1",
        }
        expected = normalize_settings(raw_settings)

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": raw_settings})

            proc = iso.run_cli(["settings", "get"])
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            cli_settings = json.loads(proc.stdout)["settings"]
            server_settings = load_registry()["settings"]
            mcp_settings = _load_settings_from_registry()

            self.assertEqual(cli_settings, expected)
            self.assertEqual(server_settings, expected)
            self.assertEqual(mcp_settings, expected)
            self.assertEqual(expected["default_tailnet"], "serve")
            self.assertEqual(expected["overlap_policy"], "deny")
            self.assertEqual(expected["open_environment_tabs"], ["alpha", "beta"])
            self.assertEqual(expected["mcp_tools_profile"], "lean")
            self.assertEqual(expected["mcp_tools"], {"status": False, "allocate": True})


class RegistryPathTests(unittest.TestCase):
    def test_config_dir_isolates_all_artifacts(self) -> None:
        from portskill.registry import (
            config_dir,
            http_auth_path,
            http_passkey_path,
            http_sessions_path,
            listen_path,
            registry_path,
        )

        with tempfile.TemporaryDirectory(prefix="portskill-config-") as tmp:
            root = pathlib.Path(tmp) / "config"
            env = {
                "PORTSKILL_CONFIG_DIR": str(root),
                "PORTSKILL_REGISTRY_PATH": "",
                "PORTSKILL_LISTEN_PATH": "",
                "PORTSKILL_HTTP_AUTH_PATH": "",
                "PORTSKILL_HTTP_PASSKEY_PATH": "",
                "PORTSKILL_HTTP_SESSIONS_PATH": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(config_dir(), root)
                self.assertEqual(registry_path(), root / "registry.json")
                self.assertEqual(listen_path(), root / "listen.json")
                self.assertEqual(http_auth_path(), root / "http_auth.json")
                self.assertEqual(http_passkey_path(), root / "http_passkey.json")
                self.assertEqual(http_sessions_path(), root / "http_sessions.json")

    def test_legacy_specific_path_overrides_take_precedence(self) -> None:
        from portskill.registry import listen_path, registry_path

        with tempfile.TemporaryDirectory(prefix="portskill-paths-") as tmp:
            root = pathlib.Path(tmp)
            env = {
                "PORTSKILL_CONFIG_DIR": str(root / "config"),
                "PORTSKILL_REGISTRY_PATH": str(root / "legacy-registry.json"),
                "PORTSKILL_LISTEN_PATH": str(root / "legacy-listen.json"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(registry_path(), root / "legacy-registry.json")
                self.assertEqual(listen_path(), root / "legacy-listen.json")


class RegistryPersistenceTests(unittest.TestCase):
    def test_atomic_write_preserves_existing_permissions(self) -> None:
        from portskill.registry import atomic_write_text

        with tempfile.TemporaryDirectory(prefix="portskill-atomic-") as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            path.write_text("old\n", encoding="utf-8")
            path.chmod(0o640)

            atomic_write_text(path, "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_atomic_write_failure_keeps_original_and_cleans_temp(self) -> None:
        from portskill.registry import atomic_write_text

        with tempfile.TemporaryDirectory(prefix="portskill-atomic-") as tmp:
            root = pathlib.Path(tmp)
            path = root / "registry.json"
            path.write_text("original\n", encoding="utf-8")

            with mock.patch(
                "portskill.registry.os.replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    atomic_write_text(path, "replacement\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(root.glob(".registry.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
