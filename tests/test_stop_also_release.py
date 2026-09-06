"""settings.stop_also_release: default true preserves release; false keeps reserved."""
from __future__ import annotations

import unittest

from tests.helpers import IsolatedConfig, parse_cli_json


def _range_state(payload: dict) -> str | None:
    rng = payload.get("range") or (payload.get("result") or {}).get("range") or {}
    return rng.get("state")


class StopAlsoReleaseTests(unittest.TestCase):
    def _start_sleep(self, iso: IsolatedConfig, proj) -> str:
        first = iso.run_cli([
            "path",
            "--mode",
            "start",
            "--project",
            str(proj),
            "--command",
            "sleep 60",
        ])
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        payload = parse_cli_json(first)
        range_id = (payload.get("result") or {}).get("range", {}).get("id")
        self.assertTrue(range_id)
        return range_id

    def test_default_true_and_normalize(self) -> None:
        from port_registry_app.cli import default_settings, normalize_settings

        self.assertTrue(default_settings().get("stop_also_release"))
        self.assertTrue(normalize_settings({}).get("stop_also_release"))
        self.assertFalse(normalize_settings({"stop_also_release": False}).get("stop_also_release"))
        self.assertFalse(normalize_settings({"stop_also_release": "off"}).get("stop_also_release"))
        self.assertTrue(normalize_settings({"stop_also_release": "on"}).get("stop_also_release"))

    def test_settings_get_set_roundtrip(self) -> None:
        with IsolatedConfig() as iso:
            got = iso.run_cli(["settings", "get"])
            self.assertEqual(got.returncode, 0, got.stderr or got.stdout)
            settings = parse_cli_json(got)["settings"]
            self.assertTrue(settings.get("stop_also_release"))

            off = iso.run_cli(["settings", "set", "--stop-also-release", "off"])
            self.assertEqual(off.returncode, 0, off.stderr or off.stdout)
            self.assertFalse(parse_cli_json(off)["settings"].get("stop_also_release"))

            on = iso.run_cli(["settings", "set", "--stop-also-release", "on"])
            self.assertEqual(on.returncode, 0, on.stderr or on.stdout)
            self.assertTrue(parse_cli_json(on)["settings"].get("stop_also_release"))

    def test_cli_stop_default_releases(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            range_id = self._start_sleep(iso, proj)
            stopped = iso.run_cli(["stop", "--range-id", range_id, "--project", str(proj)])
            self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
            payload = parse_cli_json(stopped)
            self.assertTrue(payload.get("also_release"))
            self.assertEqual(_range_state(payload), "released")

    def test_cli_stop_false_keeps_reserved(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.run_cli(["settings", "set", "--stop-also-release", "off"])
            range_id = self._start_sleep(iso, proj)
            stopped = iso.run_cli(["stop", "--range-id", range_id, "--project", str(proj)])
            self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
            payload = parse_cli_json(stopped)
            self.assertFalse(payload.get("also_release"))
            self.assertEqual(_range_state(payload), "reserved")
            released = iso.run_cli(["release", "--range-id", range_id, "--project", str(proj)])
            self.assertEqual(released.returncode, 0, released.stderr or released.stdout)
            self.assertEqual(parse_cli_json(released).get("range", {}).get("state"), "released")

    def test_cli_override_beats_setting(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.run_cli(["settings", "set", "--stop-also-release", "off"])
            range_id = self._start_sleep(iso, proj)
            stopped = iso.run_cli([
                "stop",
                "--range-id",
                range_id,
                "--project",
                str(proj),
                "--also-release",
                "on",
            ])
            self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
            payload = parse_cli_json(stopped)
            self.assertTrue(payload.get("also_release"))
            self.assertEqual(_range_state(payload), "released")

    def test_bulk_and_path_honor_setting(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.run_cli(["settings", "set", "--stop-also-release", "off"])
            self._start_sleep(iso, proj)
            path_stop = iso.run_cli(["path", "--mode", "stop", "--project", str(proj)])
            self.assertEqual(path_stop.returncode, 0, path_stop.stderr or path_stop.stdout)
            path_payload = parse_cli_json(path_stop)
            self.assertEqual((path_payload.get("result") or {}).get("range", {}).get("state"), "reserved")

            iso.run_cli(["path", "--mode", "start", "--project", str(proj), "--command", "sleep 60"])
            bulk = iso.run_cli(["stop", "--all"])
            self.assertEqual(bulk.returncode, 0, bulk.stderr or bulk.stdout)
            bulk_payload = parse_cli_json(bulk)
            self.assertFalse(bulk_payload.get("also_release"))
            for item in bulk_payload.get("stopped") or []:
                rng = item.get("range") or {}
                if rng:
                    self.assertEqual(rng.get("state"), "reserved")

    def test_mcp_settings_set_and_stop_override(self) -> None:
        from port_registry_app.mcp import mcp_handle

        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            applied = mcp_handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "settings_set",
                    "arguments": {"stop_also_release": False},
                },
            })
            self.assertNotIn("error", applied)
            settings = (applied["result"].get("structuredContent") or {}).get("settings") or {}
            self.assertFalse(settings.get("stop_also_release"))

            range_id = self._start_sleep(iso, proj)
            stopped = mcp_handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "stop",
                    "arguments": {
                        "range_id": range_id,
                        "project": str(proj),
                    },
                },
            })
            self.assertNotIn("error", stopped)
            body = stopped["result"]["structuredContent"]
            self.assertEqual(_range_state(body), "reserved")

            iso.run_cli(["path", "--mode", "start", "--project", str(proj), "--command", "sleep 60"])
            forced = mcp_handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "stop",
                    "arguments": {
                        "range_id": range_id,
                        "project": str(proj),
                        "also_release": True,
                    },
                },
            })
            self.assertNotIn("error", forced)
            self.assertEqual(_range_state(forced["result"]["structuredContent"]), "released")

    def test_settings_panel_has_checkbox_and_pitch(self) -> None:
        from port_registry_app.mcp import PORTSKILL_TOOL_DESCRIPTION
        from port_registry_app.server import build_view, render_page

        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {}})
            html = render_page(
                build_view({
                    "version": 1,
                    "pool": {"start": 20000, "end": 29999},
                    "projects": {},
                    "presets": {},
                    "settings": {},
                }),
                tailscale={"chip": "Needs login", "state": "needs_login", "logged_in": False},
            )
        self.assertIn('id="pr-stop-also-release"', html)
        self.assertIn("Stop also Release", html)
        self.assertIn('id="pr-mcp-portskill-pitch"', html)
        self.assertIn(PORTSKILL_TOOL_DESCRIPTION, html)
        self.assertIn("one MCP tool for your agent to handle all port management functions", html)


if __name__ == "__main__":
    unittest.main()
