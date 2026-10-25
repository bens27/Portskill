"""status warns when a project mirror still names the pre-rename .port-registry/ paths."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, parse_cli_json


def _range(start_script: str, *, range_id: str = "r1", port: int = 21000) -> dict:
    return {
        "id": range_id,
        "start": port,
        "end": port,
        "state": "reserved",
        "tailnet": {"mode": "none"},
        "lifecycle": {
            "start_script": start_script,
            "stop_script": ".port-registry/stop.sh",
            "start_log": ".port-registry/start.log",
            "stop_log": ".portskill/stop.log",
        },
    }


class LegacyMirrorPathTests(unittest.TestCase):
    def _project(self, iso: IsolatedConfig, mirror: dict | str | None) -> str:
        project = (iso.root / "project").resolve()
        project.mkdir()
        if mirror is not None:
            text = mirror if isinstance(mirror, str) else json.dumps(mirror)
            (project / ".portskill.json").write_text(text, encoding="utf-8")
        iso.write_registry({"projects": {str(project): {"ranges": [_range(".portskill/start.sh")]}}})
        return str(project)

    def _status(self, project: str) -> dict:
        from portskill.status import status_result

        with mock.patch("portskill.status.probe_tailscale_status", return_value={"Self": None}):
            return status_result(project)

    def test_status_warns_and_leaves_the_mirror_untouched(self) -> None:
        with IsolatedConfig() as iso:
            absolute = str((iso.root / "project").resolve() / ".port-registry" / "start.sh")
            mirror = {"version": 1, "project_path": "x", "ranges": [_range(absolute)]}
            project = self._project(iso, mirror)
            mirror_path = iso.root / "project" / ".portskill.json"
            before = mirror_path.read_bytes()
            result = self._status(project)
            self.assertEqual(mirror_path.read_bytes(), before)

        warnings = result["mirror_warnings"]
        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertEqual(warning["project"], project)
        self.assertEqual(warning["code"], "legacy_lifecycle_paths")
        self.assertEqual(warning["mirror"], project + "/.portskill.json")
        self.assertIn(".port-registry/", warning["hint"])
        self.assertEqual(
            [(p["range_id"], p["field"], p["value"], p["replacement"]) for p in warning["paths"]],
            [
                ("r1", "start_log", ".port-registry/start.log", ".portskill/start.log"),
                ("r1", "start_script", absolute, absolute.replace("/.port-registry/", "/.portskill/")),
                ("r1", "stop_script", ".port-registry/stop.sh", ".portskill/stop.sh"),
            ],
        )

    def test_no_warning_for_clean_missing_or_malformed_mirrors(self) -> None:
        clean = {"version": 1, "ranges": [{"id": "r1", "lifecycle": {
            "start_script": ".portskill/start.sh", "note": "my.port-registry/x", "pid": 12}}]}
        for mirror in (clean, None, "{not json", "[]", '{"ranges": {"r1": 1}}',
                       '{"ranges": [1, {"lifecycle": [".port-registry/start.sh"]}]}'):
            with self.subTest(mirror=mirror), IsolatedConfig() as iso:
                result = self._status(self._project(iso, mirror))
            self.assertNotIn("mirror_warnings", result)

    def test_find_payload_and_cli_carry_the_warning(self) -> None:
        from portskill.status import status_result

        with IsolatedConfig() as iso:
            mirror = {"version": 1, "ranges": [_range(".port-registry/start.sh")]}
            project = self._project(iso, mirror)
            with mock.patch("portskill.status.probe_tailscale_status", return_value={"Self": None}):
                found = status_result(find="project")
            completed = iso.run_cli(["status", "--project", project])

        self.assertEqual(found["matches"], 1)
        self.assertEqual(found["mirror_warnings"][0]["project"], project)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(parse_cli_json(completed)["mirror_warnings"][0]["project"], project)

    def test_next_project_write_rewrites_relative_legacy_paths(self) -> None:
        with IsolatedConfig() as iso:
            mirror = {"version": 1, "ranges": [_range(".port-registry/start.sh")]}
            project = self._project(iso, mirror)
            # The registry copy carries the legacy paths too, as an old install wrote it.
            iso.write_registry({"projects": {project: {"ranges": [_range(".port-registry/start.sh")]}}})
            self.assertIn("mirror_warnings", self._status(project))
            released = iso.run_cli(["release", "--range-id", "r1", "--project", project])
            self.assertEqual(released.returncode, 0, released.stderr or released.stdout)
            rewritten = json.loads((iso.root / "project" / ".portskill.json").read_text(encoding="utf-8"))
            result = self._status(project)

        lifecycle = rewritten["ranges"][0]["lifecycle"]
        self.assertEqual(lifecycle["start_script"], ".portskill/start.sh")
        self.assertEqual(lifecycle["stop_script"], ".portskill/stop.sh")
        self.assertEqual(lifecycle["start_log"], ".portskill/start.log")
        self.assertNotIn("mirror_warnings", result)


if __name__ == "__main__":
    unittest.main()
