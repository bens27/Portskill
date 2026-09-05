"""(a) version / identity consistency: pyproject ↔ __version__ ↔ doctor."""
from __future__ import annotations

import pathlib
import re
import unittest

from tests.helpers import IsolatedConfig, parse_cli_json

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        raise AssertionError("pyproject.toml missing version")
    return m.group(1)


class VersionIdentityTests(unittest.TestCase):
    def test_pyproject_matches_package_and_mcp(self) -> None:
        from port_registry_app import __version__
        from port_registry_app.mcp import SERVER_NAME, SERVER_VERSION

        py_ver = _pyproject_version()
        self.assertEqual(__version__, py_ver)
        self.assertEqual(SERVER_VERSION, py_ver)
        self.assertEqual(SERVER_NAME, "portskill")

    def test_doctor_version_matches_package(self) -> None:
        from port_registry_app import __version__

        with IsolatedConfig() as iso:
            proc = iso.run_cli(["doctor"])
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = parse_cli_json(proc)
        self.assertEqual(payload.get("version"), __version__)
        self.assertEqual(payload.get("version"), _pyproject_version())


if __name__ == "__main__":
    unittest.main()
