"""Status flags drift read-only; sweep reports stale ranges and releases them
only on explicit opt-in, through the normal exit teardown."""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


def _spawn_detached_sleep() -> tuple[int, int]:
    """Start a sleep that init reaps (a direct child would linger as a zombie
    and still look live). Returns (pid, pgid)."""
    shell = subprocess.Popen(
        ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
        stdout=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    assert shell.stdout is not None
    pid = int(shell.stdout.readline())
    shell.stdout.close()
    shell.wait()
    return pid, shell.pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _lan_ipv4() -> str | None:
    """A bindable local IPv4 address outside 127/8, or None (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
        if address.startswith("127.") or address == "0.0.0.0":
            return None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((address, 0))
        return address
    except OSError:
        return None


def _free_port() -> int:
    """A port nothing holds, below the ephemeral range: an ephemeral port can
    sit in TIME_WAIT from an earlier test's client connection, which the
    (deliberately conservative) bind test counts as held."""
    import random

    from portskill.drift import port_held

    for _ in range(200):
        port = random.randint(20000, 29999)
        if not port_held(port):
            return port
    raise unittest.SkipTest("no free port in 20000-29999")


class DriftSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.project = str((self.iso.root / "project").resolve())
        os.mkdir(self.project)
        self.tailscale = mock.patch(
            "portskill.status.probe_tailscale_status", return_value={"Self": None}
        )
        self.tailscale.start()
        self.addCleanup(self.tailscale.stop)

    # -- fixtures -----------------------------------------------------------

    def _dead_pid(self) -> tuple[int, int]:
        pid, pgid = _spawn_detached_sleep()
        os.killpg(pgid, signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(_alive(pid))
        return pid, pgid

    def _live_pid(self) -> tuple[int, int]:
        pid, pgid = _spawn_detached_sleep()

        def kill() -> None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(kill)
        return pid, pgid

    def _listener(self, host: str = "127.0.0.1", port: int = 0,
                  family: int = socket.AF_INET) -> int:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(8)
        self.addCleanup(sock.close)
        return sock.getsockname()[1]

    def _pair_with_listener_on_second(self) -> int:
        """Return start where start is free and start+1 has a loopback listener."""
        # Both ports come from the pool range (see _free_port): ephemeral
        # ports often sit in TIME_WAIT under the smoke suite's test order.
        from portskill.drift import port_held

        for _ in range(50):
            start = _free_port()
            if start < 65535 and not port_held(start + 1):
                try:
                    self._listener(port=start + 1)
                except OSError:
                    continue
                return start
        self.fail("no pair of free ports in the pool range")

    def _range(self, range_id: str, port: int, *, state: str = "active",
               pid: int | None = None, pgid: int | None = None, tailnet: str = "none",
               end: int | None = None) -> dict:
        from portskill.registry_operations import range_record

        item = range_record(range_id, port, end or port, None, tailnet, default_state="off")
        item["state"] = state
        item["activated_at"] = "2026-10-25T00:00:00Z"
        item["lifecycle"]["pid"] = pid
        item["lifecycle"]["pgid"] = pgid
        return item

    def _write(self, ranges: list[dict], project: str | None = None) -> None:
        self.iso.write_registry({"projects": {project or self.project: {"ranges": ranges}}})
        self._settle()

    def _settle(self) -> None:
        # The helper writes a non-canonical file; the first registry read of
        # any command persists the schema. Settle it so byte comparisons only
        # see writes made by the code under test.
        from portskill.status import status_result

        status_result(None)

    def _bytes(self) -> bytes:
        return self.iso.registry_path.read_bytes()

    def _stored(self, range_id: str, project: str | None = None) -> dict:
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        ranges = data["projects"][project or self.project]["ranges"]
        return next(item for item in ranges if item["id"] == range_id)

    def _sweep(self, **options) -> dict:
        from portskill.sweep import sweep_result

        return sweep_result(options)

    # -- status: drift is reported, never written ---------------------------

    def test_status_flags_dead_pid_without_writing_the_registry(self) -> None:
        from portskill.status import status_result

        pid, pgid = self._dead_pid()
        self._write([self._range("r1", _free_port(), pid=pid, pgid=pgid)])
        before = self._bytes()

        result = status_result(self.project)

        drift = result["ranges"][0]["drift"]
        self.assertEqual(drift["reasons"], ["pid_dead", "port_unbound"])
        self.assertTrue(drift["stale"])
        self.assertIn("portskill sweep", drift["hint"])
        self.assertEqual(result["drift"], {"count": 1, "stale_count": 1, "range_ids": ["r1"]})
        self.assertEqual(result["ranges"][0]["allocation_state"], "active")
        self.assertEqual(self._bytes(), before, "status must not mutate the registry")

    def test_status_flags_active_range_without_pid_and_unbound_port(self) -> None:
        from portskill.status import status_result

        self._write([self._range("r1", _free_port())])
        drift = status_result(self.project)["ranges"][0]["drift"]
        self.assertEqual(drift["reasons"], ["no_pid", "port_unbound"])
        self.assertTrue(drift["stale"])

    def test_healthy_and_non_active_ranges_have_no_drift(self) -> None:
        from portskill.status import status_result

        pid, pgid = self._live_pid()
        dead, dead_group = self._dead_pid()
        self._write([
            self._range("live", self._listener(), pid=pid, pgid=pgid),
            self._range("parked", _free_port(), state="reserved", pid=dead, pgid=dead_group),
        ])
        result = status_result(self.project)
        self.assertEqual([r["drift"] for r in result["ranges"]], [None, None])
        self.assertNotIn("drift", result)

    def test_dead_pid_with_bound_port_is_drift_but_not_stale(self) -> None:
        from portskill.status import status_result

        pid, pgid = self._dead_pid()
        self._write([self._range("r1", self._listener(), pid=pid, pgid=pgid)])
        drift = status_result(self.project)["ranges"][0]["drift"]
        self.assertEqual(drift["reasons"], ["pid_dead", "untracked_listener"])
        self.assertFalse(drift["stale"])

    def test_service_status_poll_carries_drift(self) -> None:
        from portskill.status import service_statuses

        pid, pgid = self._dead_pid()
        item = self._range("r1", _free_port(), pid=pid, pgid=pgid)
        registry = {"projects": {self.project: {"ranges": [item]}}, "machines": {}}
        [service] = service_statuses(registry)
        self.assertEqual(service["drift"]["reasons"], ["pid_dead", "port_unbound"])

    def test_live_ranges_are_never_bind_tested_by_status_or_ui(self) -> None:
        # A lazily-binding dev server that is still starting must not lose its
        # port to a status poll: live ranges get a loopback connect only.
        from portskill import server
        from portskill.status import service_statuses, status_result

        pid, pgid = self._live_pid()
        other, other_group = self._live_pid()
        self._write([
            self._range("serving", self._listener(), pid=pid, pgid=pgid),
            # Live, nothing listening yet (still starting).
            self._range("starting", _free_port(), pid=other, pgid=other_group),
        ])
        starting = self._stored("starting")
        raw = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))

        with mock.patch("portskill.drift.port_held") as port_held:
            result = status_result(self.project)
            services = service_statuses(raw)
            view = server.build_view(raw)

        port_held.assert_not_called()
        drift = {r["id"]: r["drift"] for r in result["ranges"]}
        self.assertIsNone(drift["serving"])
        self.assertEqual(drift["starting"]["reasons"], ["port_unbound"])
        self.assertFalse(drift["starting"]["stale"])
        self.assertEqual(len(services), 2)
        self.assertTrue(view)
        self.assertEqual(starting["state"], "active")

    def test_live_range_listening_on_lan_only_is_a_hint_never_a_release(self) -> None:
        # Accepted trade-off: loopback-only check for live ranges.
        address = _lan_ipv4()
        if address is None:
            self.skipTest("no non-loopback IPv4 address")
        pid, pgid = self._live_pid()
        self._write([self._range("lan", self._listener(host=address), pid=pid, pgid=pgid)])
        before = self._bytes()
        from portskill.status import status_result

        hint = {r["id"]: r["drift"] for r in status_result(self.project)["ranges"]}["lan"]
        result = self._sweep(release=True)

        # Status keeps the hint; sweep never probes a live range (388b7f19),
        # so it neither lists nor releases it.
        self.assertEqual(hint["reasons"], ["port_unbound"])
        self.assertFalse(hint["stale"])
        self.assertEqual(result["stale"], [])
        self.assertEqual(result["released"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(self._bytes(), before)

    def test_sweep_runs_no_connect_or_bind_under_the_lock_for_live_ranges(self) -> None:
        # 388b7f19 A: the scan runs inside mutate_registry. A live range can
        # never be released, so its ports get neither a connect nor a bind
        # there; only the not-alive range is bind-tested (the stale decider).
        from portskill import drift as drift_module
        from portskill import sweep as sweep_module

        live, live_group = self._live_pid()
        other, other_group = self._live_pid()
        dead, dead_group = self._dead_pid()
        serving, starting, stale = self._listener(), _free_port(), _free_port()
        self._write([
            self._range("serving", serving, pid=live, pgid=live_group),
            self._range("starting", starting, pid=other, pgid=other_group),
            self._range("stale", stale, pid=dead, pgid=dead_group),
        ])
        locked = {"held": False}
        probes: list[tuple[str, int, bool]] = []
        real_mutate = sweep_module.mutate_registry

        def mutate(fn, *args, **kwargs):
            def inner(registry):
                locked["held"] = True
                try:
                    return fn(registry)
                finally:
                    locked["held"] = False
            return real_mutate(inner, *args, **kwargs)

        def recorder(kind, real):
            def probe(port):
                probes.append((kind, port, locked["held"]))
                return real(port)
            return probe

        with mock.patch.object(sweep_module, "mutate_registry", mutate), \
                mock.patch.object(drift_module, "port_held", recorder("bind", drift_module.port_held)), \
                mock.patch.object(drift_module, "port_answers", recorder("connect", drift_module.port_answers)), \
                mock.patch.object(drift_module.socket, "create_connection",
                                  side_effect=AssertionError("connect under the sweep lock")):
            result = self._sweep(release=True)

        self.assertEqual(probes, [("bind", stale, True)])
        self.assertEqual([row["range_id"] for row in result["released"]], ["stale"])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(self._stored("serving")["state"], "active")
        self.assertEqual(self._stored("starting")["state"], "active")
        self.assertTrue(_alive(live) and _alive(other))

    def test_pid_less_range_with_a_loopback_listener_is_never_bind_tested(self) -> None:
        # 388b7f19 B: an externally activated range is connect-tested first;
        # an answer proves the port is held, so the service is never bound.
        # The listener is on the second port: every port is connected before
        # any is bound.
        from portskill import drift as drift_module
        from portskill import server
        from portskill.status import service_statuses, status_result

        start = self._pair_with_listener_on_second()
        self._write([self._range("external", start, end=start + 1)])
        raw = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))

        with mock.patch("portskill.drift.port_held") as port_held, \
                mock.patch("portskill.drift.port_answers", wraps=drift_module.port_answers) as answers:
            result = status_result(self.project)
            services = service_statuses(raw)
            view = server.build_view(raw)

        port_held.assert_not_called()
        answers.assert_any_call(start + 1)
        self.assertIsNone({r["id"]: r["drift"] for r in result["ranges"]}["external"])
        self.assertEqual(len(services), 1)
        self.assertTrue(view)

    def test_pid_less_range_with_nothing_answering_falls_back_to_the_bind_test(self) -> None:
        # Connect-first never decides "free": with no answer, bind decides.
        from portskill import drift as drift_module
        from portskill.status import status_result

        port = _free_port()
        self._write([self._range("idle", port)])
        calls: list[tuple[str, int]] = []
        real_answers, real_held = drift_module.port_answers, drift_module.port_held

        def answers(p):
            calls.append(("connect", p))
            return real_answers(p)

        def held(p):
            calls.append(("bind", p))
            return real_held(p)

        with mock.patch.object(drift_module, "port_answers", answers), \
                mock.patch.object(drift_module, "port_held", held):
            drift = {r["id"]: r["drift"] for r in status_result(self.project)["ranges"]}["idle"]

        # status probes each range more than once; every probe connects first.
        self.assertTrue(calls)
        self.assertEqual(calls, [("connect", port), ("bind", port)] * (len(calls) // 2))
        self.assertEqual(drift["reasons"], ["no_pid", "port_unbound"])
        self.assertTrue(drift["stale"])

    def test_pid_less_range_on_a_lan_address_stays_held_in_status(self) -> None:
        # Connect-first misses a LAN-only listener; the bind test still sees
        # it, so status never calls it stale.
        address = _lan_ipv4()
        if address is None:
            self.skipTest("no non-loopback IPv4 address")
        from portskill.status import status_result

        self._write([self._range("lan", self._listener(host=address))])
        drift = {r["id"]: r["drift"] for r in status_result(self.project)["ranges"]}["lan"]
        self.assertIsNone(drift)

    def test_not_live_ranges_still_use_the_bind_test(self) -> None:
        from portskill import drift as drift_module

        dead, dead_group = self._dead_pid()
        port = _free_port()
        self._write([self._range("r1", port, pid=dead, pgid=dead_group)])

        with mock.patch("portskill.drift.port_held", wraps=drift_module.port_held) as port_held, \
                mock.patch("portskill.drift.port_answers") as port_answers:
            result = self._sweep(release=True)

        port_held.assert_any_call(port)
        port_answers.assert_not_called()
        self.assertEqual([row["range_id"] for row in result["released"]], ["r1"])

    # -- sweep: report by default -------------------------------------------

    def test_sweep_reports_stale_without_changing_anything(self) -> None:
        pid, pgid = self._dead_pid()
        live, live_group = self._live_pid()
        self._write([
            self._range("stale", _free_port(), pid=pid, pgid=pgid),
            self._range("live", self._listener(), pid=live, pgid=live_group),
            self._range("parked", _free_port(), state="reserved"),
        ])
        before = self._bytes()

        result = self._sweep()

        self.assertTrue(result["dry_run"])
        self.assertEqual([row["range_id"] for row in result["stale"]], ["stale"])
        self.assertEqual(result["stale"][0]["pid"], pid)
        self.assertEqual(result["stale"][0]["active_since"], "2026-10-25T00:00:00Z")
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["released"], [])
        self.assertEqual(self._bytes(), before, "a report-only sweep must not write")

    def test_sweep_project_filter(self) -> None:
        other = str((self.iso.root / "other").resolve())
        os.mkdir(other)
        self.iso.write_registry({"projects": {
            self.project: {"ranges": [self._range("mine", _free_port())]},
            other: {"ranges": [self._range("theirs", _free_port())]},
        }})
        self._settle()
        result = self._sweep(project=other)
        self.assertEqual([row["range_id"] for row in result["stale"]], ["theirs"])

    # -- sweep: opt-in release ----------------------------------------------

    def test_sweep_release_tears_down_and_releases(self) -> None:
        pid, pgid = self._dead_pid()
        item = self._range("r1", _free_port(), pid=pid, pgid=pgid, tailnet="serve")
        item["tailnet"]["port"] = item["start"]
        item["tailnet"]["configured_at"] = "2026-10-25T00:00:00Z"
        self._write([item])

        with mock.patch("portskill.lifecycle.run_tailnet") as run_tailnet:
            result = self._sweep(release=True)

        run_tailnet.assert_called_once_with("serve", item["start"], off=True)
        self.assertFalse(result["dry_run"])
        self.assertEqual([row["range_id"] for row in result["released"]], ["r1"])
        self.assertEqual(result["failed"], [])
        stored = self._stored("r1")
        self.assertEqual(stored["state"], "released")
        self.assertIsNone(stored["lifecycle"]["pid"])
        self.assertIsNone(stored["tailnet"]["configured_at"])
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        labels = [
            step.get("label")
            for entry in data.get("environment_history", {}).values()
            for step in entry.get("steps", [])
        ]
        self.assertIn("sweep", labels)

    def test_sweep_release_teardown_failure_parks_reserved(self) -> None:
        from portskill.domain import DomainError

        pid, pgid = self._dead_pid()
        item = self._range("r1", _free_port(), pid=pid, pgid=pgid, tailnet="serve")
        item["tailnet"]["port"] = item["start"]
        item["tailnet"]["configured_at"] = "2026-10-25T00:00:00Z"
        self._write([item])

        with mock.patch(
            "portskill.lifecycle.run_tailnet",
            side_effect=DomainError("tailnet_command_failed", "tailscale exploded"),
        ):
            result = self._sweep(release=True)

        self.assertEqual(result["released"], [])
        self.assertEqual(result["failed"][0]["reason"], "tailnet_teardown_failed")
        self.assertEqual(result["warnings"][0]["reason"], "tailnet_teardown_failed")
        stored = self._stored("r1")
        self.assertEqual(stored["state"], "reserved")
        self.assertIsNone(stored["lifecycle"]["pid"], "never leave a dead pid recorded")
        self.assertTrue(stored["tailnet"]["restore_pending"])
        self.assertEqual(stored["tailnet"]["configured_at"], "2026-10-25T00:00:00Z")

    def test_sweep_release_never_touches_a_bound_port_or_live_process(self) -> None:
        dead, dead_group = self._dead_pid()
        live, live_group = self._live_pid()
        self._write([
            self._range("listener", self._listener(), pid=dead, pgid=dead_group),
            self._range("live", self._listener(), pid=live, pgid=live_group),
        ])
        before = self._bytes()

        result = self._sweep(release=True)

        self.assertEqual(result["stale"], [])
        self.assertEqual([row["range_id"] for row in result["skipped"]], ["listener"])
        self.assertIn("untracked_listener", result["skipped"][0]["reasons"])
        self.assertEqual(self._bytes(), before)
        self.assertTrue(_alive(live))

    def test_sweep_never_releases_a_range_listened_on_a_lan_address(self) -> None:
        # Reviewer case (a): `activate`d range, no pid, listener on a LAN IP
        # only. A loopback connect misses it; the bind test must not.
        address = _lan_ipv4()
        if address is None:
            self.skipTest("no non-loopback IPv4 address")
        port = self._listener(host=address)
        self._write([self._range("lan", port)])
        before = self._bytes()

        result = self._sweep(release=True)

        self.assertEqual(result["stale"], [])
        self.assertEqual(result["released"], [])
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self._stored("lan")["state"], "active")

    def test_sweep_never_releases_a_range_listened_on_a_later_port(self) -> None:
        # Reviewer case (b): range start..start+1, listener on start+1 only.
        start = self._pair_with_listener_on_second()
        self._write([self._range("pair", start, end=start + 1)])
        before = self._bytes()

        result = self._sweep(release=True)

        self.assertEqual(result["stale"], [])
        self.assertEqual(result["released"], [])
        self.assertEqual(self._bytes(), before)

    def test_port_held_sees_ipv6_only_listener(self) -> None:
        from portskill.drift import port_held

        try:
            port = self._listener(host="::1", family=socket.AF_INET6)
        except OSError:
            self.skipTest("no IPv6 loopback")
        self.assertTrue(port_held(port))
        self.assertFalse(port_held(_free_port()))

    def test_sweep_release_frees_only_confirmed_ranges_still_stale(self) -> None:
        dead, dead_group = self._dead_pid()
        self._write([
            self._range("confirmed", _free_port()),
            self._range("unconfirmed", _free_port()),
            # Reviewed as stale, but something listens on it now.
            self._range("relistened", self._listener(), pid=dead, pgid=dead_group),
        ])

        result = self._sweep(release=True, range_ids=["confirmed", "relistened", "gone"])

        self.assertEqual([row["range_id"] for row in result["released"]], ["confirmed"])
        reasons = {row["range_id"]: row.get("skip_reason") for row in result["skipped"]}
        self.assertEqual(reasons, {
            "relistened": "no_longer_stale",
            "unconfirmed": "not_confirmed",
            "gone": "unknown_range",
        })
        self.assertEqual(self._stored("confirmed")["state"], "released")
        self.assertEqual(self._stored("unconfirmed")["state"], "active")
        self.assertEqual(self._stored("relistened")["state"], "active")

    def test_sweep_rejects_malformed_range_ids(self) -> None:
        from portskill.domain import DomainError

        self._write([self._range("r1", _free_port())])
        with self.assertRaises(DomainError) as caught:
            self._sweep(release=True, range_ids=[1])
        self.assertEqual(caught.exception.reason, "invalid_range_ids")
        self.assertEqual(self._stored("r1")["state"], "active")

    def test_sweep_release_works_when_the_project_directory_is_gone(self) -> None:
        gone = str((self.iso.root / "deleted-checkout").resolve())
        os.mkdir(gone)
        self._write([self._range("r1", _free_port())], project=gone)
        shutil.rmtree(gone)

        result = self._sweep(release=True)

        self.assertEqual([row["range_id"] for row in result["released"]], ["r1"])
        self.assertEqual(self._stored("r1", gone)["state"], "released")

    def test_park_for_exit_clears_a_dead_pid_on_plain_stop(self) -> None:
        # Regression: lifecycle() replaces the lifecycle dict on each call, and
        # the no-live-process branch used to write to a stale reference.
        from portskill.lifecycle import park_item_for_exit

        pid, pgid = self._dead_pid()
        item = self._range("r1", _free_port(), pid=pid, pgid=pgid)
        ok, detail = park_item_for_exit(item, self.project, also_release=False)
        self.assertTrue(ok)
        self.assertEqual(detail["action"], "stopped_reserved")
        self.assertEqual(item["state"], "reserved")
        self.assertIsNone(item["lifecycle"]["pid"])
        self.assertIsNone(item["lifecycle"]["pgid"])
        self.assertTrue(item["lifecycle"]["stopped_at"])

    # -- surfaces -----------------------------------------------------------

    def test_mcp_and_ui_reach_the_same_sweep(self) -> None:
        from portskill import mcp, server

        self._write([self._range("r1", _free_port())])
        before = self._bytes()

        tool = mcp.call_tool("sweep", {})
        self.assertFalse(tool["isError"])
        self.assertEqual([r["range_id"] for r in tool["structuredContent"]["stale"]], ["r1"])

        code, body = server.dispatch_ui_action({"action": "sweep"})
        self.assertEqual(code, 0)
        self.assertTrue(body["result"]["dry_run"])
        self.assertEqual(self._bytes(), before)

        tool = mcp.call_tool("sweep", {"release": True, "range_ids": ["other"]})
        self.assertFalse(tool["isError"])
        self.assertEqual(tool["structuredContent"]["released"], [])
        self.assertEqual(self._stored("r1")["state"], "active")

        code, body = server.dispatch_ui_action(
            {"action": "sweep", "release": True, "range_ids": ["r1"]}
        )
        self.assertEqual(code, 0)
        self.assertEqual([r["range_id"] for r in body["result"]["released"]], ["r1"])
        self.assertEqual(self._stored("r1")["state"], "released")

    def test_cli_sweep_release_honours_range_ids(self) -> None:
        self._write([self._range("r1", _free_port()), self._range("r2", _free_port())])
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "portskill.cli", "sweep", "--release", "--range-id", "r2"],
            capture_output=True, text=True, env=self.iso.subprocess_env(),
            cwd=self.project,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual([r["range_id"] for r in payload["released"]], ["r2"])
        self.assertEqual(self._stored("r1")["state"], "active")
        self.assertEqual(self._stored("r2")["state"], "released")

    # -- confirmed ids that cannot be released: distinct skip reasons -------

    def _write_two_projects(self) -> str:
        """Scope: self.project holds a stale and a reserved range; another project a stale one."""
        other = str((self.iso.root / "other").resolve())
        os.mkdir(other)
        self.ports = {"stale": _free_port(), "kept": _free_port(), "foreign": _free_port()}
        self.iso.write_registry({"projects": {
            self.project: {"ranges": [
                self._range("stale", self.ports["stale"]),
                self._range("kept", self.ports["kept"], state="reserved"),
            ]},
            other: {"ranges": [self._range("foreign", self.ports["foreign"])]},
        }})
        self._settle()
        return other

    def _sweep_cli(self, range_ids: list[str], project: str | None) -> dict:
        argv = [sys.executable, "-B", "-m", "portskill.cli", "sweep", "--release"]
        for range_id in range_ids:
            argv += ["--range-id", range_id]
        if project:
            argv += ["--project", project]
        proc = subprocess.run(
            argv, capture_output=True, text=True, env=self.iso.subprocess_env(), cwd=self.project,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _sweep_mcp(self, range_ids: list[str], project: str | None) -> dict:
        from portskill import mcp

        args: dict = {"release": True, "range_ids": range_ids}
        if project:
            args["project"] = project
        tool = mcp.call_tool("sweep", args)
        self.assertFalse(tool["isError"], tool)
        return tool["structuredContent"]

    def _assert_skip(self, surface, range_id: str, project_filter: str | None,
                     reason: str, owner: str | None, start: int | None) -> None:
        before = self._bytes()

        result = surface([range_id], project_filter)

        self.assertEqual(result["released"], [])
        rows = {row["range_id"]: row for row in result["skipped"]}
        self.assertEqual(rows[range_id]["skip_reason"], reason)
        self.assertEqual(rows[range_id]["project"], owner)
        self.assertEqual(rows[range_id]["start"], start)
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self._stored("stale")["state"], "active")
        self.assertEqual(self._stored("kept")["state"], "reserved")

    def _check_all_reasons(self, surface) -> None:
        other = self._write_two_projects()
        with self.subTest("unknown_range"):
            self._assert_skip(surface, "ghost", None, "unknown_range", None, None)
        with self.subTest("not_in_scope"):
            self._assert_skip(surface, "foreign", self.project, "not_in_scope",
                              other, self.ports["foreign"])
            self.assertEqual(self._stored("foreign", other)["state"], "active")
        with self.subTest("no_longer_stale"):
            self._assert_skip(surface, "kept", self.project, "no_longer_stale",
                              self.project, self.ports["kept"])
        with self.subTest("no_longer_stale without a project filter"):
            self._assert_skip(surface, "kept", None, "no_longer_stale",
                              self.project, self.ports["kept"])

    def test_cli_sweep_release_reports_distinct_skip_reasons(self) -> None:
        self._check_all_reasons(self._sweep_cli)

    def test_mcp_sweep_release_reports_distinct_skip_reasons(self) -> None:
        self._check_all_reasons(self._sweep_mcp)

    def test_not_confirmed_rows_keep_project_and_start(self) -> None:
        self._write_two_projects()
        result = self._sweep(release=True, range_ids=["ghost"], project=self.project)
        rows = {row["range_id"]: row for row in result["skipped"]}
        self.assertEqual(rows["stale"]["skip_reason"], "not_confirmed")
        self.assertEqual(rows["stale"]["project"], self.project)
        self.assertEqual(rows["stale"]["start"], self.ports["stale"])
        self.assertNotIn("foreign", rows)
        self.assertEqual(result["released"], [])

    def test_cli_sweep_defaults_to_report_only(self) -> None:
        self._write([self._range("r1", _free_port())])
        before = self._bytes()
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "portskill.cli", "sweep"],
            capture_output=True, text=True, env=self.iso.subprocess_env(),
            cwd=self.project,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual([r["range_id"] for r in payload["stale"]], ["r1"])
        self.assertEqual(self._bytes(), before)


if __name__ == "__main__":
    unittest.main()
