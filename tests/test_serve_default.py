"""New installs default Serve of Portskill listen OFF; explicit true is kept."""
from __future__ import annotations

import json
import unittest

from tests.helpers import IsolatedConfig, parse_cli_json


class ServePortskillDefaultTests(unittest.TestCase):
    def test_default_settings_and_normalize(self) -> None:
        from port_registry_app.cli import default_settings, initial_registry, normalize_settings

        self.assertFalse(default_settings().get("serve_portskill_on_tailscale"))
        self.assertFalse(initial_registry(20000, 29999)["settings"].get("serve_portskill_on_tailscale"))
        self.assertFalse(normalize_settings({}).get("serve_portskill_on_tailscale"))
        self.assertFalse(normalize_settings({"serve_portskill_on_tailscale": False}).get("serve_portskill_on_tailscale"))
        self.assertFalse(normalize_settings({"serve_portskill_on_tailscale": "off"}).get("serve_portskill_on_tailscale"))
        self.assertTrue(normalize_settings({"serve_portskill_on_tailscale": True}).get("serve_portskill_on_tailscale"))
        self.assertTrue(normalize_settings({"serve_portskill_on_tailscale": "on"}).get("serve_portskill_on_tailscale"))

    def test_existing_true_registry_is_not_flipped(self) -> None:
        from port_registry_app.cli import normalize_settings
        from port_registry_app.server import load_registry

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"serve_portskill_on_tailscale": True}})
            loaded = load_registry()
            self.assertTrue((loaded.get("settings") or {}).get("serve_portskill_on_tailscale"))
            raw = json.loads(iso.registry_path.read_text(encoding="utf-8"))
            self.assertTrue((raw.get("settings") or {}).get("serve_portskill_on_tailscale"))
            self.assertTrue(normalize_settings(raw.get("settings")).get("serve_portskill_on_tailscale"))

    def test_settings_get_new_install_is_off(self) -> None:
        with IsolatedConfig() as iso:
            got = iso.run_cli(["settings", "get"])
            self.assertEqual(got.returncode, 0, got.stderr or got.stdout)
            settings = parse_cli_json(got)["settings"]
            self.assertFalse(settings.get("serve_portskill_on_tailscale"))

    def test_settings_set_roundtrip(self) -> None:
        with IsolatedConfig() as iso:
            on = iso.run_cli(["settings", "set", "--serve-portskill-on-tailscale", "on"])
            self.assertEqual(on.returncode, 0, on.stderr or on.stdout)
            self.assertTrue(parse_cli_json(on)["settings"].get("serve_portskill_on_tailscale"))
            off = iso.run_cli(["settings", "set", "--serve-portskill-on-tailscale", "off"])
            self.assertEqual(off.returncode, 0, off.stderr or off.stdout)
            self.assertFalse(parse_cli_json(off)["settings"].get("serve_portskill_on_tailscale"))

    def test_ui_toggle_defaults_unchecked(self) -> None:
        from port_registry_app.server import build_view, portskill_serve_toggle_html

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {}})
            html = portskill_serve_toggle_html(
                build_view({
                    "version": 1,
                    "pool": {"start": 20000, "end": 29999},
                    "projects": {},
                    "presets": {},
                    "settings": {},
                }),
                {"chip": "Needs login", "state": "needs_login", "logged_in": False},
            )
        self.assertIn("Serve Portskill on Tailscale", html)
        self.assertIn('data-pr-switch="serve-portskill"', html)
        self.assertIn('aria-checked="false"', html)
        switch = html.split("data-pr-switch", 1)[-1].split("</label>", 1)[0]
        self.assertNotRegex(switch, r"\schecked(\s|/|>)")
        self.assertIn("New installs default off", html)


if __name__ == "__main__":
    unittest.main()
