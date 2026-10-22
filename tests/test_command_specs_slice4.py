"""Slice 4 environment, compatibility, and preset command regressions."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class Slice4RuntimeTests(unittest.TestCase):
    _COMPAT_REGISTRY = {
        "projects": {
            "/review/project": {
                "ranges": [{
                    "id": "r1",
                    "start": 20001,
                    "end": 20001,
                    "default_state": "on",
                    "state": "reserved",
                    "note": "web",
                }],
            },
        }
    }

    @staticmethod
    def _run_cli(argv):
        from portskill import cli

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    @staticmethod
    def _mcp_call(name, arguments):
        from portskill import mcp

        return mcp.mcp_handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })["result"]

    @staticmethod
    def _mcp_compat(arguments):
        return Slice4RuntimeTests._mcp_call("compat_check", arguments)

    @staticmethod
    def _environment_file(iso, project, *, name="imported"):
        source = iso.root / f"{name}.json"
        source.write_text(json.dumps({
            "version": 1,
            "kind": "portskill-environment",
            "name": name,
            "services": [
                {
                    "project": str(project.resolve()),
                    "note": "first",
                    "count": 1,
                    "tailnet": "none",
                    "default_state": "on",
                },
                {
                    "project": str(project.resolve()),
                    "note": "second",
                    "count": 1,
                    "tailnet": "none",
                    "default_state": "on",
                },
            ],
        }, sort_keys=True) + "\n")
        return source

    @staticmethod
    def _preset_registry(project):
        return {
            "settings": {"require_compat": False},
            "presets": {
                "mixed": {
                    "name": "mixed",
                    "description": "",
                    "services": [
                        {
                            "project": str(project.resolve()),
                            "note": "first",
                            "count": 1,
                            "tailnet": "none",
                            "default_state": "on",
                        },
                        {
                            "project": str(project.resolve()),
                            "note": "second",
                            "count": 1,
                            "tailnet": "none",
                            "default_state": "on",
                        },
                    ],
                },
            },
        }

    @staticmethod
    def _controlled_start(calls):
        def start(item, project, registry=None):
            calls.append((item["note"], project, registry is not None))
            if item["note"] == "first":
                item["state"] = "active"
                return True, {"action": "started", "range": item}
            return False, {
                "reason": "controlled_start_failure",
                "range_id": item["id"],
                "range": item,
            }

        return start

    @staticmethod
    def _http_action(body):
        from portskill import server

        raw = json.dumps(body).encode()
        handler = object.__new__(server.Handler)
        handler.path = "/portskill/actions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /portskill/actions HTTP/1.1"
        handler.command = "POST"
        handler.client_address = ("127.0.0.1", 1234)
        handler._refuse_untrusted_host = lambda: False
        handler._refuse_mutating_guard = lambda: False
        handler._require_http_access = lambda **_kwargs: True
        handler.log_message = lambda *_args: None
        handler.do_POST()
        header, payload = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(header.split()[1]), json.loads(payload)

    def test_mcp_compat_preserves_legacy_argument_semantics(self) -> None:
        cases = (
            (
                {"names": ["missing"]},
                {"missing_presets": ["missing"], "sources": ["preset:missing"]},
            ),
            (
                {"presets": "one,two"},
                {"missing_presets": ["one,two"], "sources": ["preset:one,two"]},
            ),
            (
                {"presets": ["one"], "preset": "two"},
                {"missing_presets": ["one"], "sources": ["preset:one"]},
            ),
            (
                {"services": [{
                    "project": "/review/project",
                    "note": "web",
                    "start": 20001,
                    "count": 1,
                }]},
                {"claim_count": 1, "sources": ["services"]},
            ),
            (
                {"include_default_on": True, "project": ["/review/other"]},
                {"claim_count": 0, "sources": ["default_on"]},
            ),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments), IsolatedConfig() as iso:
                iso.write_registry(self._COMPAT_REGISTRY)
                result = self._mcp_compat(arguments)
            structured = result["structuredContent"]
            for key, value in expected.items():
                self.assertEqual(structured[key], value)

    def test_ui_compat_preserves_whitespace_and_rejects_integer(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            iso.write_registry(self._COMPAT_REGISTRY)
            whitespace = server.dispatch_ui_action({
                "action": "compat-check",
                "presets": "   ",
            })
            integer = server.dispatch_ui_action({
                "action": "compat-check",
                "presets": 1,
            })

        self.assertEqual(whitespace[0], 0)
        self.assertEqual(whitespace[1]["compatReport"]["claim_count"], 0)
        self.assertEqual(whitespace[1]["compatReport"]["sources"], [])
        self.assertEqual(integer, (1, {"ok": False, "message": "'int' object is not iterable"}))

    def test_http_compat_preserves_whitespace_and_rejects_integer(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry(self._COMPAT_REGISTRY)
            whitespace = self._http_action({
                "action": "compat-check",
                "presets": "   ",
            })
            integer = self._http_action({
                "action": "compat-check",
                "presets": 1,
            })

        self.assertEqual(whitespace[0], 200)
        self.assertEqual(whitespace[1]["compatReport"]["claim_count"], 0)
        self.assertEqual(whitespace[1]["compatReport"]["sources"], [])
        self.assertEqual(integer, (422, {"ok": False, "message": "'int' object is not iterable"}))

    def test_migrated_apply_adapters_propagate_needs_input(self) -> None:
        from portskill.domain import DomainError

        needs_input = {
            "status": "needs_input",
            "reason": "tailnet_required",
            "needs_input": True,
            "prompt": "Choose Tailnet mode",
        }
        for adapter in ("preset_apply", "environment_import"):
            with self.subTest(adapter=adapter), IsolatedConfig() as iso:
                project = iso.root / "project"
                project.mkdir()
                if adapter == "preset_apply":
                    iso.write_registry(self._preset_registry(project))
                    invoke = lambda: self._mcp_call(
                        "preset_apply", {"name": "mixed"}
                    )
                else:
                    iso.write_registry({"settings": {"require_compat": False}})
                    source = self._environment_file(iso, project)
                    invoke = lambda: self._run_cli([
                        "environment", "import", "--file", str(source),
                        "--apply-defaults",
                    ])

                with mock.patch(
                    "portskill.apply_defaults.start_item_for_apply",
                    side_effect=DomainError(
                        "needs_input", exit_code=3, payload=needs_input
                    ),
                ) as start:
                    result = invoke()

                self.assertEqual(start.call_count, 1)
                if adapter == "preset_apply":
                    self.assertTrue(result["isError"])
                    self.assertEqual(result["structuredContent"], {
                        **needs_input,
                        "ok": False,
                    })
                else:
                    code, payload, stderr = result
                    self.assertEqual(code, 3)
                    self.assertEqual(payload, needs_input)
                    self.assertEqual(stderr, "")

                registry = json.loads(iso.registry_path.read_text())
                persisted = registry["projects"][str(project.resolve())]["ranges"]
                self.assertEqual([item["note"] for item in persisted], [
                    "first", "second",
                ])

    def test_migrated_apply_adapters_persist_mixed_start_results(self) -> None:
        for adapter in ("preset_apply", "environment_import"):
            with self.subTest(adapter=adapter), IsolatedConfig() as iso:
                project = iso.root / "project"
                project.mkdir()
                calls = []
                if adapter == "preset_apply":
                    iso.write_registry(self._preset_registry(project))
                    invoke = lambda: self._mcp_call(
                        "preset_apply", {"name": "mixed"}
                    )
                else:
                    iso.write_registry({"settings": {"require_compat": False}})
                    source = self._environment_file(iso, project)
                    invoke = lambda: self._run_cli([
                        "environment", "import", "--file", str(source),
                        "--apply-defaults",
                    ])

                with mock.patch(
                    "portskill.apply_defaults.start_item_for_apply",
                    side_effect=self._controlled_start(calls),
                ):
                    result = invoke()

                self.assertEqual(
                    calls,
                    [
                        ("first", str(project.resolve()), True),
                        ("second", str(project.resolve()), True),
                    ],
                )
                if adapter == "preset_apply":
                    self.assertFalse(result["isError"])
                    payload = result["structuredContent"]
                else:
                    code, payload, stderr = result
                    self.assertEqual(code, 0)
                    self.assertEqual(stderr, "")
                applied = payload["apply_defaults"]
                self.assertEqual(applied["started_count"], 1)
                self.assertEqual(applied["error_count"], 1)
                self.assertEqual(
                    applied["errors"][0]["reason"],
                    "controlled_start_failure",
                )

                registry = json.loads(iso.registry_path.read_text())
                persisted = registry["projects"][str(project.resolve())]["ranges"]
                self.assertEqual(
                    [(item["note"], item["state"]) for item in persisted],
                    [("first", "active"), ("second", "reserved")],
                )


class Slice4ProjectionTests(unittest.TestCase):
    def test_migrated_mcp_specs_occupy_frozen_positions(self) -> None:
        from portskill.command_specs import (
            COMPAT_COMMAND,
            ENVIRONMENT_EXPORT_COMMAND,
            ENVIRONMENT_IMPORT_COMMAND,
            PRESET_APPLY_COMMAND,
            PRESET_DELETE_COMMAND,
            PRESET_LIST_COMMAND,
            PRESET_SAVE_COMMAND,
        )
        from portskill.mcp_catalog import TOOL_DEFS, _mcp_tool_definition

        for spec in (
            ENVIRONMENT_EXPORT_COMMAND,
            ENVIRONMENT_IMPORT_COMMAND,
            COMPAT_COMMAND,
            PRESET_SAVE_COMMAND,
            PRESET_LIST_COMMAND,
            PRESET_APPLY_COMMAND,
            PRESET_DELETE_COMMAND,
        ):
            with self.subTest(spec=spec.id):
                self.assertEqual(
                    TOOL_DEFS[spec.mcp.catalog_position],
                    _mcp_tool_definition(spec),
                )

    def test_cli_parsers_are_generated_from_slice4_specs(self) -> None:
        from portskill.cli import parser

        parsed = parser().parse_args(
            [
                "environment", "export", "--name", "dev", "--project", "a",
                "--project", "b",
            ]
        )
        self.assertEqual(parsed.env_command, "export")
        self.assertEqual(parsed.project, ["a", "b"])

        root = parser().parse_args(["compat", "--presets", "one,two"])
        child = parser().parse_args(["compat", "check", "--name", "three"])
        self.assertEqual(root.presets, "one,two")
        self.assertEqual(child.name, ["three"])

        preset = parser().parse_args(
            ["preset", "apply", "--name", "dev", "--also-stop-off"]
        )
        self.assertEqual(preset.preset_command, "apply")
        self.assertTrue(preset.also_stop_off)

    def test_mcp_dispatch_uses_slice4_specs_and_preserves_policy(self) -> None:
        from portskill import mcp
        from portskill.command_specs import COMPAT_COMMAND, ENVIRONMENT_EXPORT_COMMAND

        with mock.patch(
            "portskill.mcp.execute_command",
            return_value={"compatible": False},
        ) as execute:
            response = mcp._call_tool("compat_check", {"presets": ["dev"]})
        execute.assert_called_once_with(COMPAT_COMMAND, {"presets": ["dev"]})
        self.assertTrue(response["isError"])
        self.assertFalse(response["structuredContent"]["ok"])

        with mock.patch(
            "portskill.mcp.execute_command", return_value={"name": "dev"}
        ) as execute:
            response = mcp._call_tool("environment_export", {"name": "dev"})
        execute.assert_called_once_with(ENVIRONMENT_EXPORT_COMMAND, {"name": "dev"})
        self.assertEqual(
            response["structuredContent"],
            {"status": "ok", "environment": {"name": "dev"}},
        )


class Slice4UIBindingTests(unittest.TestCase):
    @staticmethod
    def _replace_ui_fields(spec, replacements):
        original = spec.params
        mutated = tuple(
            replace(param, ui=replace(param.ui, fields=replacements[param.key]))
            if param.key in replacements
            else param
            for param in original
        )
        object.__setattr__(spec, "params", mutated)
        return original

    def test_export_import_and_compat_use_declared_ui_fields(self) -> None:
        from portskill import server
        from portskill.command_specs import (
            COMPAT_COMMAND,
            ENVIRONMENT_EXPORT_COMMAND,
            ENVIRONMENT_IMPORT_COMMAND,
        )

        original = self._replace_ui_fields(
            ENVIRONMENT_EXPORT_COMMAND, {"name": ("alternateName",)}
        )
        try:
            with mock.patch(
                "portskill.environments.environment_export_result",
                side_effect=lambda options: {"options": options},
            ) as owner:
                code, response = server.dispatch_ui_action({
                    "action": "export-environment",
                    "name": "guard",
                    "alternateName": "  declared  ",
                })
        finally:
            object.__setattr__(ENVIRONMENT_EXPORT_COMMAND, "params", original)
        self.assertEqual(code, 0)
        owner.assert_called_once_with({"name": "declared"})
        self.assertEqual(response["filename"], "guard.json")
        self.assertIn('"name": "declared"', response["download"])

        original = self._replace_ui_fields(
            ENVIRONMENT_IMPORT_COMMAND, {"file": ("alternateFile",)}
        )
        try:
            with mock.patch(
                "portskill.environments.environment_import_result",
                side_effect=lambda options: {"status": "ok", "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action({
                    "action": "import-environment",
                    "path": "guard.json",
                    "alternateFile": "declared.json",
                })
        finally:
            object.__setattr__(ENVIRONMENT_IMPORT_COMMAND, "params", original)
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["options"], {"file": "declared.json"})
        owner.assert_called_once_with({"file": "declared.json"})

        original = self._replace_ui_fields(
            COMPAT_COMMAND, {"presets": ("alternatePresets", "alternateNames")}
        )
        try:
            with mock.patch(
                "portskill.compatibility.compatibility_result",
                side_effect=lambda options: {"compatible": True, "options": options},
            ) as owner:
                code, response = server.dispatch_ui_action({
                    "action": "compat-check",
                    "presets": ["guard"],
                    "alternatePresets": "",
                    "alternateNames": " alpha, beta ",
                })
        finally:
            object.__setattr__(COMPAT_COMMAND, "params", original)
        expected = {"presets": " alpha, beta "}
        self.assertEqual(code, 0)
        self.assertEqual(response["compatReport"]["options"], expected)
        owner.assert_called_once_with(expected)

    def test_preset_actions_use_declared_ui_fields_and_coercions(self) -> None:
        from portskill import server
        from portskill.command_specs import (
            PRESET_APPLY_COMMAND,
            PRESET_DELETE_COMMAND,
            PRESET_SAVE_COMMAND,
        )

        cases = (
            (
                PRESET_SAVE_COMMAND,
                {"name": ("alternateName",), "description": ("alternateDescription",)},
                "preset-save",
                {"name": "guard", "alternateName": "  saved  ", "alternateDescription": ""},
                "portskill.presets.preset_save_result",
                {"name": "saved"},
            ),
            (
                PRESET_APPLY_COMMAND,
                {"name": ("alternateName",), "also_stop_off": ("alternateStop",)},
                "preset-apply",
                {"name": "guard", "alternateName": "  applied  ", "alternateStop": 1},
                "portskill.presets.preset_apply_result",
                {"name": "applied", "also_stop_off": True},
            ),
            (
                PRESET_DELETE_COMMAND,
                {"name": ("alternateName",)},
                "preset-delete",
                {"name": "guard", "alternateName": "  deleted  "},
                "portskill.presets.preset_delete_result",
                {"name": "deleted"},
            ),
        )
        for spec, replacements, action, body, owner_path, expected in cases:
            with self.subTest(action=action):
                original = self._replace_ui_fields(spec, replacements)
                try:
                    with mock.patch(
                        owner_path,
                        side_effect=lambda options: {"status": "ok", "options": options},
                    ) as owner:
                        code, response = server.dispatch_ui_action(
                            {"action": action, **body}
                        )
                finally:
                    object.__setattr__(spec, "params", original)
                self.assertEqual(code, 0)
                self.assertEqual(response["result"]["options"], expected)
                owner.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
