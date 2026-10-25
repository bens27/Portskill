"""Boolean params are typed at the MCP and UI boundaries.

A JSON string "false" is truthy in Python: before this guard, sweep
{"release": "false"} released ranges and status {"all": "false"} listed every
project. Only a real JSON boolean is accepted; any other present value is an
invalid_args error raised before anything runs. An absent key keeps the
command's default.
"""
from __future__ import annotations

import json
import os
import pathlib
import unittest
from unittest import mock

from tests import test_drift_sweep as drift
from tests.helpers import IsolatedConfig

BAD_VALUES = ("false", "true", 1, 0, None, "", [], {})


def _mcp_boolean_cases():
    from portskill.command_specs import COMMAND_SPECS, MISSING

    for spec in COMMAND_SPECS:
        if spec.mcp is None:
            continue
        for param in spec.params:
            if param.kind != "boolean" or param.mcp is None:
                continue
            name = param.key if param.mcp.name is MISSING else param.mcp.name
            for tool in (spec.mcp.name, *spec.mcp.aliases):
                yield spec, tool, name


def _ui_boolean_cases():
    from portskill.command_specs import COMMAND_SPECS

    for spec in COMMAND_SPECS:
        if spec.ui is None:
            continue
        for param in spec.params:
            if param.kind != "boolean" or param.ui is None:
                continue
            for action in spec.ui.actions:
                for field in param.ui.fields:
                    yield spec, action, field


def _tree_bytes(root: pathlib.Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class BooleanParamTableTests(unittest.TestCase):
    """Generic: every kind=boolean param of COMMAND_SPECS, through each surface."""

    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)

    def test_cases_cover_the_known_flags(self) -> None:
        mcp_cases = {(tool, name) for _, tool, name in _mcp_boolean_cases()}
        self.assertIn(("sweep", "release"), mcp_cases)
        self.assertIn(("status", "all"), mcp_cases)
        ui_cases = {(action, field) for _, action, field in _ui_boolean_cases()}
        self.assertIn(("sweep", "release"), ui_cases)
        self.assertIn(("deactivate", "alsoRelease"), ui_cases)

    def test_mcp_rejects_non_boolean_before_running_anything(self) -> None:
        from portskill import command_runtime, mcp

        cases = list(_mcp_boolean_cases())
        self.assertGreaterEqual(len(cases), 15)
        handler = mock.Mock(side_effect=AssertionError("handler must not run"))
        with mock.patch.object(command_runtime, "resolve_handler", return_value=handler), \
                mock.patch.object(mcp, "execute_command", side_effect=AssertionError("must not run")):
            for _, tool, name in cases:
                for bad in BAD_VALUES:
                    with self.subTest(tool=tool, name=name, value=bad):
                        result = mcp.call_tool(tool, {name: bad})
                        self.assertTrue(result["isError"])
                        body = result["structuredContent"]
                        self.assertEqual(body.get("reason"), "invalid_args", body)
                        self.assertIn(name, body.get("message", ""))
        handler.assert_not_called()

    def test_mcp_accepts_json_booleans_and_absent_keys(self) -> None:
        from portskill.command_runtime import validate_mcp_boolean_args

        for _, tool, name in _mcp_boolean_cases():
            with self.subTest(tool=tool, name=name):
                validate_mcp_boolean_args(tool, {name: True})
                validate_mcp_boolean_args(tool, {name: False})
                validate_mcp_boolean_args(tool, {})

    def test_ui_binder_rejects_non_boolean(self) -> None:
        from portskill import server
        from portskill.domain import DomainError

        cases = list(_ui_boolean_cases())
        self.assertTrue(cases)
        for _, action, field in cases:
            for bad in BAD_VALUES:
                with self.subTest(action=action, field=field, value=bad):
                    with self.assertRaises(DomainError) as caught:
                        server._ui_command_options(action, {"action": action, field: bad})
                    self.assertEqual(caught.exception.reason, "invalid_args")

    def test_ui_binder_accepts_json_booleans(self) -> None:
        from portskill import server

        for spec, action, field in _ui_boolean_cases():
            for good in (True, False):
                with self.subTest(action=action, field=field, value=good):
                    server._ui_command_options(action, {"action": action, field: good})

    def test_ui_settings_save_flags_are_typed(self) -> None:
        from portskill import server

        before = _tree_bytes(self.iso.root)
        for field in server._UI_SETTINGS_BOOLEAN_FIELDS:
            for bad in BAD_VALUES:
                with self.subTest(field=field, value=bad):
                    code, body = server.dispatch_ui_action(
                        {"action": "settings-save", field: bad}
                    )
                    self.assertNotEqual(code, 0)
                    self.assertFalse(body["ok"])
                    self.assertEqual(body["result"]["reason"], "invalid_args")
        self.assertEqual(_tree_bytes(self.iso.root), before)

    def test_ui_discover_import_rejects_with_envelope(self) -> None:
        from portskill import server

        code, body = server.dispatch_ui_action(
            {"action": "discover-import", "all_missing": "false"}
        )
        self.assertNotEqual(code, 0)
        self.assertFalse(body["ok"])
        self.assertEqual(body["result"]["reason"], "invalid_args")


class SweepAndStatusBooleanTests(unittest.TestCase):
    """End to end on a stale range: only JSON true releases."""

    _range = drift.DriftSweepTests._range
    _write = drift.DriftSweepTests._write
    _settle = drift.DriftSweepTests._settle
    _stored = drift.DriftSweepTests._stored
    _dead_pid = drift.DriftSweepTests._dead_pid

    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.project = str((self.iso.root / "project").resolve())
        os.mkdir(self.project)
        (pathlib.Path(self.project) / ".portskill.json").write_text(
            json.dumps({"note": "fixture"}), encoding="utf-8"
        )
        patcher = mock.patch(
            "portskill.status.probe_tailscale_status", return_value={"Self": None}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        tailnet = mock.patch("portskill.lifecycle.run_tailnet")
        tailnet.start()
        self.addCleanup(tailnet.stop)
        pid, pgid = self._dead_pid()
        self._write([self._range("stale", drift._free_port(), pid=pid, pgid=pgid)])

    def _snapshot(self) -> dict[str, bytes]:
        return _tree_bytes(self.iso.root)

    def test_bad_release_values_change_nothing(self) -> None:
        from portskill import mcp, server

        before = self._snapshot()
        for bad in ("false", "true", 1, 0, None):
            with self.subTest(surface="mcp", value=bad):
                result = mcp.call_tool("sweep", {"release": bad})
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["reason"], "invalid_args")
            with self.subTest(surface="ui", value=bad):
                code, body = server.dispatch_ui_action({"action": "sweep", "release": bad})
                self.assertNotEqual(code, 0)
                self.assertFalse(body["ok"])
                self.assertEqual(body["result"]["reason"], "invalid_args")
        self.assertEqual(self._snapshot(), before, "a rejected sweep must not write")
        self.assertEqual(self._stored("stale")["state"], "active")

    def test_json_false_is_the_same_as_omitted(self) -> None:
        from portskill import mcp, server

        before = self._snapshot()
        for args in ({}, {"release": False}):
            with self.subTest(surface="mcp", args=args):
                result = mcp.call_tool("sweep", args)
                self.assertFalse(result.get("isError"), result)
                body = result["structuredContent"]
                self.assertTrue(body["dry_run"])
                self.assertEqual([row["range_id"] for row in body["stale"]], ["stale"])
            with self.subTest(surface="ui", args=args):
                code, body = server.dispatch_ui_action({"action": "sweep", **args})
                self.assertEqual(code, 0, body)
                self.assertTrue(body["result"]["dry_run"])
        self.assertEqual(self._snapshot(), before)

    def test_json_true_releases(self) -> None:
        from portskill import mcp

        result = mcp.call_tool("sweep", {"release": True})
        self.assertFalse(result.get("isError"), result)
        body = result["structuredContent"]
        self.assertFalse(body["dry_run"])
        self.assertEqual([row["range_id"] for row in body["released"]], ["stale"])
        self.assertEqual(self._stored("stale")["state"], "released")

    def test_status_all_is_typed(self) -> None:
        from portskill import mcp

        for bad in ("false", "true", 1, 0, None):
            with self.subTest(value=bad):
                result = mcp.call_tool("status", {"all": bad})
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["reason"], "invalid_args")
        for good in (True, False):
            with self.subTest(value=good):
                self.assertFalse(mcp.call_tool("status", {"all": good}).get("isError"))


if __name__ == "__main__":
    unittest.main()
