"""portskill_path: skip-predicate matrix + needs_input resume + MCP toggle."""
from __future__ import annotations

import unittest

from port_registry_app.path import (
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
            self.assertEqual(item.get("state"), "reserved")

            release = iso.run_cli(["path", "--mode", "release", "--project", str(proj)])
            self.assertEqual(release.returncode, 0, release.stderr or release.stdout)
            released = parse_cli_json(release)
            self.assertIn("release", _ran_phases(released))
            self.assertEqual((released.get("result") or {}).get("range", {}).get("state"), "released")

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
        from port_registry_app.mcp import enabled_tool_defs, mcp_handle

        with IsolatedConfig() as iso:
            proj = iso.root / "proj"
            proj.mkdir()
            iso.write_registry({"settings": {"mcp_tools": {}}})
            names = [t["name"] for t in enabled_tool_defs()]
            self.assertIn("portskill_path", names)
            for primitive in ("allocate", "activate", "start", "stop", "release", "status"):
                self.assertIn(primitive, names)

            listed = mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            rpc_names = [t["name"] for t in listed["result"]["tools"]]
            self.assertIn("portskill_path", rpc_names)
            self.assertIn("allocate", rpc_names)

            called = mcp_handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "portskill_path",
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

            iso.write_registry({"settings": {"mcp_tools": {"portskill_path": False}}})
            hidden = [t["name"] for t in enabled_tool_defs()]
            self.assertNotIn("portskill_path", hidden)
            self.assertIn("allocate", hidden)
            rejected = mcp_handle({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "portskill_path", "arguments": {"mode": "status"}},
            })
            self.assertEqual(rejected["error"]["code"], -32001)
            self.assertIn("portskill_path", rejected["error"]["message"])

            still = mcp_handle({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            })
            self.assertNotIn("error", still)


if __name__ == "__main__":
    unittest.main()
