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
        from portskill.drift import port_held

        for _ in range(50):
            second = self._listener()
            if second > 1 and not port_held(second - 1):
                return second - 1
        self.skipTest("no free port just below a listener")

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
            "gone": "no_longer_stale",
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
