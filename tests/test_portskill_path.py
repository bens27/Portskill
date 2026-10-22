"""portskill orchestrator: skip-predicate matrix + needs_input resume + MCP toggle."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from portskill.path import (
    PATH_MODES,
    PHASES_BY_MODE,
    SKIP_ACTIVATE_ACTIVE,
    SKIP_ALLOCATE_RESERVED,
    SKIP_RELEASE_ALREADY,
    SKIP_START_PLACEHOLDER,
    SKIP_START_RUNNING,
    SKIP_STOP_NOT_RUNNING,
    SKIP_TAILNET_ALREADY,
    SKIP_TAILNET_FUNNEL_LISTEN,
    SKIP_TAILNET_NOT_LOGGED_IN,
    SKIP_TAILNET_NOT_REQUESTED,
    SKIP_WIRE_ALREADY,
    PathSkipFacts,
    needs_input_for_tailnet_skip,
    skip_reason,
    tailnet_is_requested,
)
from tests.helpers import IsolatedConfig, parse_cli_json


def _facts(**overrides) -> PathSkipFacts:
    base = dict(
        has_unreleased_range=False,
        range_state=None,
        scripts_exist=False,
        wire_pending=False,
        live_process=False,
        start_ready=False,
        tailnet_requested=False,
        tailnet_logged_in=False,
        tailnet_already_configured=False,
        funnel_listen=False,
    )
    base.update(overrides)
    return PathSkipFacts(**base)


class PathSkipPredicateTests(unittest.TestCase):
    def test_modes_and_start_phases(self) -> None:
        self.assertEqual(
            PATH_MODES,
            ("start", "stop", "release", "restart", "status"),
        )
        self.assertEqual(
            PHASES_BY_MODE["start"],
            ("allocate", "wire", "activate", "start", "tailnet"),
        )

    def test_skip_matrix(self) -> None:
        cases = [
            ("allocate", _facts(), None),
            ("allocate", _facts(has_unreleased_range=True, range_state="reserved"), SKIP_ALLOCATE_RESERVED),
            ("wire", _facts(scripts_exist=False, wire_pending=False), None),
            ("wire", _facts(scripts_exist=True, wire_pending=False), SKIP_WIRE_ALREADY),
            ("wire", _facts(scripts_exist=True, wire_pending=True), None),
            ("activate", _facts(range_state="reserved"), None),
            ("activate", _facts(has_unreleased_range=True, range_state="active"), SKIP_ACTIVATE_ACTIVE),
            ("start", _facts(live_process=True, start_ready=True), SKIP_START_RUNNING),
            ("start", _facts(live_process=False, start_ready=False), SKIP_START_PLACEHOLDER),
            ("start", _facts(live_process=False, start_ready=True), None),
            ("tailnet", _facts(funnel_listen=True, tailnet_requested=True), SKIP_TAILNET_FUNNEL_LISTEN),
            ("tailnet", _facts(), SKIP_TAILNET_NOT_REQUESTED),
            (
                "tailnet",
                _facts(tailnet_requested=True, tailnet_logged_in=False),
                SKIP_TAILNET_NOT_LOGGED_IN,
            ),
            (
                "tailnet",
                _facts(
                    tailnet_requested=True,
                    tailnet_logged_in=True,
                    tailnet_already_configured=True,
                ),
                SKIP_TAILNET_ALREADY,
            ),
            (
                "tailnet",
                _facts(tailnet_requested=True, tailnet_logged_in=True),
                None,
            ),
            ("stop", _facts(live_process=False, range_state="reserved"), SKIP_STOP_NOT_RUNNING),
            ("stop", _facts(live_process=True, range_state="active"), None),
            ("release", _facts(has_unreleased_range=False), SKIP_RELEASE_ALREADY),
            ("release", _facts(has_unreleased_range=True, range_state="reserved"), None),
            ("status", _facts(), None),
        ]
        for phase, facts, expected in cases:
            with self.subTest(phase=phase, expected=expected):
                self.assertEqual(skip_reason(phase, facts), expected)

    def test_funnel_listen_precedes_other_tailnet_skips(self) -> None:
        facts = _facts(
            funnel_listen=True,
            tailnet_requested=True,
            tailnet_logged_in=True,
            tailnet_already_configured=True,
        )
        self.assertEqual(skip_reason("tailnet", facts), SKIP_TAILNET_FUNNEL_LISTEN)
        self.assertFalse(needs_input_for_tailnet_skip(SKIP_TAILNET_FUNNEL_LISTEN, facts))

    def test_needs_input_only_when_tailnet_requested_and_not_logged_in(self) -> None:
        requested = _facts(tailnet_requested=True, tailnet_logged_in=False)
        self.assertTrue(
            needs_input_for_tailnet_skip(SKIP_TAILNET_NOT_LOGGED_IN, requested)
        )
        self.assertFalse(
            needs_input_for_tailnet_skip(SKIP_TAILNET_NOT_REQUESTED, _facts())
        )
        self.assertTrue(tailnet_is_requested("serve"))
        self.assertTrue(tailnet_is_requested("funnel"))
        self.assertFalse(tailnet_is_requested("none"))
        self.assertFalse(tailnet_is_requested(None))


def _skip_map(payload: dict) -> dict[str, str]:
    return {e["phase"]: e["reason"] for e in payload.get("skipped") or [] if "reason" in e}


def _ran_phases(payload: dict) -> list[str]:
    return [e["phase"] for e in payload.get("ran") or []]


class PathCliMcpTests(unittest.TestCase):
    def test_mcp_allocate_forwards_targets_while_path_drops_them(self) -> None:
        from portskill.mcp import mcp_handle, tool_argv

        target_args = {
            "machine": "missing-remote",
            "host": "remote.example",
            "scheme": "https",
        }
        allocate_argv = tool_argv("allocate", {"count": 1, **target_args})
        self.assertEqual(
            allocate_argv,
            [
                "allocate",
                "--count",
                "1",
                "--machine",
                "missing-remote",
                "--host",
                "remote.example",
                "--scheme",
                "https",
            ],
        )
        path_argv = tool_argv("portskill", {"mode": "start", **target_args})
        for unsupported_flag in ("--machine", "--host", "--scheme"):
            self.assertNotIn(unsupported_flag, path_argv)

        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            dispatched = mcp_handle({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "allocate",
                    "arguments": {
                        "count": 1,
                        "project": str(proj),
                        "tailnet": "none",
                        **target_args,
                    },
                },
            })

        self.assertNotIn("error", dispatched)
        result = dispatched["result"]
        self.assertTrue(result.get("isError"))
        self.assertEqual(
            result.get("structuredContent", {}).get("reason"),
            "machine_not_found",
        )

    def test_allocate_rejects_invalid_machine_host_and_scheme(self) -> None:
        cases = [
            (["--machine", "missing-machine"], "machine_not_found"),
            (["--host", "bad host"], "invalid_host"),
            (["--scheme", "ftp"], "invalid_scheme"),
        ]
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            for extra, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    allocate = iso.run_cli([
                        "allocate",
                        "--count",
                        "1",
                        "--project",
                        str(proj),
                        "--tailnet",
                        "none",
                        *extra,
                    ])
                    self.assertEqual(allocate.returncode, 2, allocate.stderr or allocate.stdout)
                    allocate_error = parse_cli_json(allocate)
                    self.assertEqual(allocate_error.get("reason"), expected_reason)

    def test_path_and_allocate_share_port_overlap_validation(self) -> None:
        with IsolatedConfig() as iso:
            owner = iso.root / "owner"
            target = iso.root / "target"
            owner.mkdir()
            target.mkdir()
            claimed = iso.run_cli([
                "allocate",
                "--count",
                "1",
                "--project",
                str(owner),
                "--tailnet",
                "none",
                "--start",
                "20123",
            ])
            self.assertEqual(claimed.returncode, 0, claimed.stderr or claimed.stdout)

            common = [
                "--project",
                str(target),
                "--tailnet",
                "none",
                "--start",
                "20123",
            ]
            allocate = iso.run_cli(["allocate", "--count", "1", *common])
            path = iso.run_cli(["path", "--mode", "start", *common])

            self.assertEqual(allocate.returncode, 2, allocate.stderr or allocate.stdout)
            self.assertEqual(path.returncode, 2, path.stderr or path.stdout)
            allocate_error = parse_cli_json(allocate)
            path_error = parse_cli_json(path)
            self.assertEqual(allocate_error.get("reason"), "port_in_use")
            self.assertEqual(path_error.get("reason"), "port_in_use")
            self.assertEqual(path_error.get("message"), allocate_error.get("message"))

    def test_tailnet_phase_checks_auth_before_shared_serve(self) -> None:
        from portskill.cli import range_record
        from portskill.path import run_path

        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            item = range_record("r1", 20123, 20123, None, "none")
            item["state"] = "active"
            item["lifecycle"]["pid"] = os.getpid()
            iso.write_registry({
                "projects": {str(proj.resolve()): {"ranges": [item]}},
            })
            calls: list[tuple[str, str]] = []

            def checked(mode: str) -> dict:
                calls.append(("auth", mode))
                return {"logged_in": True}

            def served(mode: str, port: int, *, off: bool) -> None:
                calls.append(("serve", mode))
                self.assertEqual(port, 20123)
                self.assertFalse(off)

            with (
                mock.patch(
                    "portskill.cli.probe_tailscale_status",
                    return_value={"logged_in": True},
                ),
                mock.patch(
                    "portskill.cli.ensure_tailscale_authenticated",
                    side_effect=checked,
                ),
                mock.patch("portskill.cli.run_tailnet", side_effect=served),
            ):
                payload = run_path(
                    mode="start",
                    project=str(proj),
                    tailnet="serve",
                )

            self.assertEqual(payload.get("status"), "ok")
            self.assertEqual(calls, [("auth", "serve"), ("serve", "serve")])
            self.assertIn("tailnet", _ran_phases(payload))

    def test_start_skip_matrix_then_idempotent(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            first = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--command",
                "sleep 60",
                "--default-state",
                "on",
            ])
            self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
            payload = parse_cli_json(first)
            self.assertEqual(payload.get("status"), "ok")
            self.assertEqual(_ran_phases(payload), ["allocate", "wire", "activate", "start"])
            self.assertEqual(_skip_map(payload).get("tailnet"), SKIP_TAILNET_NOT_REQUESTED)
            self.assertNotIn("needs_input", payload)
            range_id = (payload.get("result") or {}).get("range", {}).get("id")
            self.assertTrue(range_id)

            second = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--command",
                "sleep 60",
                "--default-state",
                "on",
            ])
            self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
            again = parse_cli_json(second)
            skips = _skip_map(again)
            self.assertEqual(skips.get("allocate"), SKIP_ALLOCATE_RESERVED)
            self.assertEqual(skips.get("wire"), SKIP_WIRE_ALREADY)
            self.assertEqual(skips.get("activate"), SKIP_ACTIVATE_ACTIVE)
            self.assertEqual(skips.get("start"), SKIP_START_RUNNING)
            self.assertEqual(skips.get("tailnet"), SKIP_TAILNET_NOT_REQUESTED)
            self.assertEqual(_ran_phases(again), [])

            stop = iso.run_cli(["path", "--mode", "stop", "--project", str(proj)])
            self.assertEqual(stop.returncode, 0, stop.stderr or stop.stdout)
            stopped = parse_cli_json(stop)
            self.assertIn("stop", _ran_phases(stopped))
            item = (stopped.get("result") or {}).get("range") or {}
            # Default settings.stop_also_release is true — path stop releases.
            self.assertEqual(item.get("state"), "released")

            release = iso.run_cli(["path", "--mode", "release", "--project", str(proj)])
            self.assertEqual(release.returncode, 0, release.stderr or release.stdout)
            released = parse_cli_json(release)
            self.assertEqual(_skip_map(released).get("release"), SKIP_RELEASE_ALREADY)

    def test_needs_input_resume_when_tailnet_login_required(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            first = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--command",
                "sleep 60",
                "--tailnet",
                "serve",
            ])
            self.assertEqual(first.returncode, 3, first.stderr or first.stdout)
            payload = parse_cli_json(first)
            self.assertTrue(payload.get("needs_input"))
            self.assertEqual(payload.get("input"), "tailscale_login")
            self.assertEqual(payload.get("resume_hint"), "tailscale login")
            self.assertEqual(_skip_map(payload).get("tailnet"), SKIP_TAILNET_NOT_LOGGED_IN)
            self.assertIn("allocate", _ran_phases(payload))
            self.assertIn("start", _ran_phases(payload))

            resume_same = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--command",
                "sleep 60",
                "--tailnet",
                "serve",
            ])
            self.assertEqual(resume_same.returncode, 3, resume_same.stderr or resume_same.stdout)
            again = parse_cli_json(resume_same)
            self.assertTrue(again.get("needs_input"))
            skips = _skip_map(again)
            self.assertEqual(skips.get("allocate"), SKIP_ALLOCATE_RESERVED)
            self.assertEqual(skips.get("start"), SKIP_START_RUNNING)
            self.assertEqual(skips.get("tailnet"), SKIP_TAILNET_NOT_LOGGED_IN)

            none = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--tailnet",
                "none",
            ])
            self.assertEqual(none.returncode, 0, none.stderr or none.stdout)
            done = parse_cli_json(none)
            self.assertEqual(done.get("status"), "ok")
            self.assertFalse(done.get("needs_input"))
            self.assertEqual(_skip_map(done).get("tailnet"), SKIP_TAILNET_NOT_REQUESTED)

            iso.run_cli(["path", "--mode", "stop", "--project", str(proj)])

    def test_default_tailnet_serve_chains_without_flag(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            setr = iso.run_cli(["settings", "set", "--default-tailnet", "serve"])
            self.assertEqual(setr.returncode, 0, setr.stderr or setr.stdout)
            first = iso.run_cli(["path", "--mode", "start", "--project", str(proj), "--command", "sleep 60"])
            # Serve was requested by the default, so the chain stops for login.
            self.assertEqual(first.returncode, 3, first.stderr or first.stdout)
            self.assertEqual(_skip_map(parse_cli_json(first)).get("tailnet"), SKIP_TAILNET_NOT_LOGGED_IN)
            override = iso.run_cli(["path", "--mode", "start", "--project", str(proj), "--tailnet", "none"])
            self.assertEqual(override.returncode, 0, override.stderr or override.stdout)
            self.assertEqual(_skip_map(parse_cli_json(override)).get("tailnet"), SKIP_TAILNET_NOT_REQUESTED)
            # Serve was never configured, so stop must not try to tear it down.
            stopped = iso.run_cli(["path", "--mode", "stop", "--project", str(proj)])
            self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)

    def test_service_launch_passes_port(self) -> None:
        from portskill.cli import service_launch

        argv, env = service_launch({"start": 20123}, "npm run dev -- --port $PORT --x ${PORT}")
        self.assertEqual(argv, ["npm", "run", "dev", "--", "--port", "20123", "--x", "20123"])
        self.assertEqual(env["PORT"], "20123")
        self.assertIsNone(service_launch({"start": 20123}, None)[0])

    def test_funnel_listen_skipped_never_applied(self) -> None:
        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.write_listen({"version": 1, "host": "127.0.0.1", "port": 20123, "listening": False})
            proc = iso.run_cli([
                "path",
                "--mode",
                "start",
                "--project",
                str(proj),
                "--start",
                "20123",
                "--tailnet",
                "funnel",
            ])
            # Allocate/activate run; start skips placeholder; tailnet skips funnel_listen.
            # Funnel-of-listen is a skip, not needs_input.
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            payload = parse_cli_json(proc)
            self.assertEqual(_skip_map(payload).get("tailnet"), SKIP_TAILNET_FUNNEL_LISTEN)
            self.assertFalse(payload.get("needs_input"))
            self.assertIn("allocate", _ran_phases(payload))

    def test_mcp_list_call_and_toggle(self) -> None:
        from portskill.mcp import enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.write_registry({"settings": {"mcp_tools": {}}})
            names = [t["name"] for t in enabled_tool_defs()]
            self.assertIn("portskill", names)
            self.assertNotIn("portskill_path", names)
            for primitive in ("allocate", "activate", "start", "stop", "release", "status"):
                self.assertIn(primitive, names)

            listed = mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            rpc_names = [t["name"] for t in listed["result"]["tools"]]
            self.assertIn("portskill", rpc_names)
            self.assertNotIn("portskill_path", rpc_names)
            self.assertIn("allocate", rpc_names)
            portskill_def = next(t for t in listed["result"]["tools"] if t["name"] == "portskill")
            desc = portskill_def.get("description") or ""
            self.assertTrue(desc.startswith("One MCP tool for your agent to handle all port management functions."))
            for trigger in ("register", "serve", "host", "Tailscale", "mode=start"):
                self.assertIn(trigger, desc)
            path_properties = portskill_def["inputSchema"]["properties"]
            for unsupported_target in ("machine", "host", "scheme"):
                self.assertNotIn(unsupported_target, path_properties)

            called = mcp_handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "portskill",
                    "arguments": {
                        "mode": "status",
                        "project": str(proj),
                    },
                },
            })
            self.assertNotIn("error", called)
            body = called["result"]["structuredContent"]
            self.assertEqual(body.get("mode"), "status")
            self.assertIn("status", _ran_phases(body))

            # Compat alias still dispatches while the tool is enabled.
            aliased = mcp_handle({
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "portskill_path",
                    "arguments": {
                        "mode": "status",
                        "project": str(proj),
                    },
                },
            })
            self.assertNotIn("error", aliased)
            alias_body = aliased["result"]["structuredContent"]
            self.assertEqual(alias_body.get("mode"), "status")

            iso.write_registry({"settings": {"mcp_tools": {"portskill_path": False}}})
            hidden = [t["name"] for t in enabled_tool_defs()]
            self.assertNotIn("portskill", hidden)
            self.assertNotIn("portskill_path", hidden)
            self.assertIn("allocate", hidden)
            rejected = mcp_handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "portskill", "arguments": {"mode": "status"}},
            })
            self.assertEqual(rejected["error"]["code"], -32001)
            self.assertIn("portskill", rejected["error"]["message"])
            rejected_alias = mcp_handle({
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {"name": "portskill_path", "arguments": {"mode": "status"}},
            })
            self.assertEqual(rejected_alias["error"]["code"], -32001)
            self.assertIn("portskill_path", rejected_alias["error"]["message"])

            still = mcp_handle({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            })
            self.assertNotIn("error", still)


if __name__ == "__main__":
    unittest.main()
