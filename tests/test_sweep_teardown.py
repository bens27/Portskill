"""Sweep release: Tailscale teardown and stop scripts never run under the registry lock."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig
from tests.test_drift_sweep import _free_port


class _Crash(BaseException):
    """Stands in for the sweep process dying mid-teardown."""


class SweepTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.project = str((self.iso.root / "project").resolve())
        os.makedirs(os.path.join(self.project, ".portskill"))
        # A real, executable stop script: sweep must never run it.
        self.stop_marker = self.iso.root / "stop-ran"
        stop = os.path.join(self.project, ".portskill", "stop.sh")
        with open(stop, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\ntouch '{self.stop_marker}'\n")
        os.chmod(stop, 0o755)
        patcher = mock.patch(
            "portskill.status.probe_tailscale_status", return_value={"Self": None}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ports = {"mapped": _free_port(), "plain": _free_port(), "mapped2": _free_port()}
        self.calls: list[dict] = []

    # -- fixtures -----------------------------------------------------------

    def _range(self, range_id: str, *, mapped: bool) -> dict:
        from portskill.registry_operations import range_record

        port = self.ports[range_id]
        item = range_record(range_id, port, port, None, "serve" if mapped else "none", default_state="off")
        item["state"] = "active"
        item["activated_at"] = "2026-10-25T00:00:00Z"
        if mapped:
            item["tailnet"]["port"] = port
            item["tailnet"]["configured_at"] = "2026-10-25T00:00:00Z"
        return item

    def _write(self) -> None:
        from portskill.status import status_result

        self.iso.write_registry({"projects": {self.project: {"ranges": [
            self._range("mapped", mapped=True),
            self._range("plain", mapped=False),
        ]}}})
        status_result(None)

    def _stored(self, range_id: str) -> dict:
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        return next(r for r in data["projects"][self.project]["ranges"] if r["id"] == range_id)

    def _lock_held(self) -> bool:
        lock_path = self.iso.registry_path.with_name(self.iso.registry_path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False

    def _fake_tailnet(self, *, fail: bool = False, during=None):
        from portskill.domain import DomainError

        def run_tailnet(mode, port, off=False):
            self.calls.append({"mode": mode, "port": port, "off": off, "locked": self._lock_held()})
            if during is not None:
                during()
            if fail:
                raise DomainError("tailnet_command_failed", "tailscale command exited with 1")

        return mock.patch("portskill.lifecycle.run_tailnet", side_effect=run_tailnet)

    def _sweep(self) -> dict:
        from portskill.sweep import sweep_result

        return sweep_result({"release": True, "range_ids": ["mapped", "plain"]})

    # -- ordering -----------------------------------------------------------

    def test_lock_probe_sees_the_registry_lock(self) -> None:
        from portskill.registry_operations import locked_registry

        self._write()
        seen = locked_registry(lambda registry: (self._lock_held(), []))
        self.assertTrue(seen)
        self.assertFalse(self._lock_held())

    def test_no_stop_script_or_tailscale_runs_while_the_lock_is_held(self) -> None:
        self._write()
        real_run = subprocess.run
        under_lock: list = []

        def spy(argv, *args, **kwargs):
            if self._lock_held():
                under_lock.append(list(argv) if isinstance(argv, (list, tuple)) else argv)
            return real_run(argv, *args, **kwargs)

        with self._fake_tailnet(), mock.patch("subprocess.run", side_effect=spy):
            result = self._sweep()

        self.assertEqual(sorted(r["range_id"] for r in result["released"]), ["mapped", "plain"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(self.calls, [
            {"mode": "serve", "port": self.ports["mapped"], "off": True, "locked": False},
        ])
        # Only the liveness probe (ps) may run under the lock; never a stop
        # script and never tailscale.
        self.assertTrue(all(argv[0] == "ps" for argv in under_lock), under_lock)
        self.assertFalse(self.stop_marker.exists())
        mapped = self._stored("mapped")
        self.assertEqual(mapped["state"], "released")
        self.assertIsNone(mapped["tailnet"]["configured_at"])
        self.assertNotIn("sweep_teardown", mapped["tailnet"])
        self.assertNotIn("restore_pending", mapped["tailnet"])
        self.assertEqual(self._stored("plain")["state"], "released")

    def test_mapping_free_range_is_released_in_the_first_write(self) -> None:
        self._write()
        seen: dict = {}

        def during() -> None:
            seen["plain"] = self._stored("plain")["state"]
            seen["mapped"] = self._stored("mapped")

        with self._fake_tailnet(during=during):
            self._sweep()

        self.assertEqual(seen["plain"], "released")
        # While `off` runs, the mapped range is parked reserved, never released.
        self.assertEqual(seen["mapped"]["state"], "reserved")
        self.assertTrue(seen["mapped"]["tailnet"]["configured_at"])
        self.assertTrue(seen["mapped"]["tailnet"]["restore_pending"])
        self.assertIn("token", seen["mapped"]["tailnet"]["sweep_teardown"])
        self.assertIsNone(seen["mapped"]["lifecycle"]["pid"])

    # -- failures -----------------------------------------------------------

    def test_teardown_failure_is_recorded_and_the_range_stays_parked(self) -> None:
        self._write()

        with self._fake_tailnet(fail=True):
            result = self._sweep()

        self.assertEqual([r["range_id"] for r in result["released"]], ["plain"])
        self.assertEqual([r["range_id"] for r in result["failed"]], ["mapped"])
        failed = result["failed"][0]
        self.assertEqual(failed["reason"], "tailnet_teardown_failed")
        self.assertEqual(failed["project"], self.project)
        self.assertNotIn("_changed", failed)
        self.assertEqual(result["warnings"][0]["reason"], "tailnet_teardown_failed")
        self.assertFalse(self.calls[0]["locked"])
        mapped = self._stored("mapped")
        self.assertEqual(mapped["state"], "reserved")
        self.assertTrue(mapped["tailnet"]["configured_at"])
        self.assertTrue(mapped["tailnet"]["restore_pending"])
        self.assertNotIn("sweep_teardown", mapped["tailnet"])
        self.assertIsNone(mapped["lifecycle"]["pid"])

    def test_range_changed_during_teardown_is_left_alone(self) -> None:
        from portskill.registry_operations import locked_registry

        self._write()

        def user_releases() -> None:
            if self._lock_held():
                return  # teardown under the lock: re-locking would deadlock; fail below instead
            def mutate(registry):
                from portskill.lifecycle import release_item

                item = next(r for r in registry["projects"][self.project]["ranges"] if r["id"] == "mapped")
                item["tailnet"]["configured_at"] = None
                release_item(item)
                return None, [self.project]

            locked_registry(mutate)

        with self._fake_tailnet(during=user_releases):
            result = self._sweep()

        self.assertEqual([r["reason"] for r in result["failed"]], ["changed_during_teardown"])
        self.assertEqual(self._stored("mapped")["state"], "released")

    # -- the marker: start/restart refusal and expiry -----------------------

    def test_start_is_refused_while_the_teardown_runs(self) -> None:
        from portskill.domain import DomainError
        from portskill.process_commands import start_result

        self._write()
        refused: list = []

        def try_start() -> None:
            if self._lock_held():
                refused.append("ran_under_the_lock")  # re-locking would deadlock
                return
            with self.assertRaises(DomainError) as caught:
                start_result({"project": self.project, "range_id": "mapped"})
            refused.append(caught.exception.reason)

        with self._fake_tailnet(during=try_start):
            result = self._sweep()

        self.assertEqual(refused, ["teardown_in_progress"])
        self.assertFalse(self.stop_marker.exists())
        self.assertEqual(sorted(r["range_id"] for r in result["released"]), ["mapped", "plain"])

    def test_activate_and_launch_refuse_a_fresh_marker_only(self) -> None:
        from portskill.domain import DomainError
        from portskill.lifecycle import activate_item, teardown_marker
        from portskill.service import launch_service

        item = self._range("mapped", mapped=True)
        item["state"] = "reserved"
        item["tailnet"]["sweep_teardown"] = {"token": "t", "at": time.time()}
        self.assertIsNotNone(teardown_marker(item))
        self.assertEqual(launch_service(item, self.project).reason, "teardown_in_progress")
        with self.assertRaises(DomainError) as caught:
            activate_item(item, self.project, "mapped", "none")
        self.assertEqual(caught.exception.reason, "teardown_in_progress")

        item["tailnet"]["sweep_teardown"]["at"] = time.time() - 121
        self.assertIsNone(teardown_marker(item))
        self.assertNotEqual(launch_service(item, self.project).reason, "teardown_in_progress")
        activate_item(item, self.project, "mapped", "none")  # no tailscale for mode none
        self.assertEqual(item["state"], "active")

        for malformed in ("x", {"token": "t"}, {"token": "t", "at": True}):
            item["tailnet"]["sweep_teardown"] = malformed
            self.assertIsNone(teardown_marker(item))

    def test_crashed_sweep_leaves_a_safe_parked_range_that_expires(self) -> None:
        from portskill import lifecycle
        from portskill.domain import DomainError
        from portskill.process_commands import start_result

        self._write()

        def crash() -> None:
            raise _Crash()

        with self._fake_tailnet(during=crash), self.assertRaises(_Crash):
            self._sweep()

        mapped = self._stored("mapped")
        self.assertEqual(mapped["state"], "reserved")  # never handed out again
        self.assertTrue(mapped["tailnet"]["configured_at"])
        self.assertTrue(mapped["tailnet"]["restore_pending"])
        self.assertIn("sweep_teardown", mapped["tailnet"])
        self.assertEqual(self._stored("plain")["state"], "released")

        with self.assertRaises(DomainError) as caught:
            start_result({"project": self.project, "range_id": "mapped"})
        self.assertEqual(caught.exception.reason, "teardown_in_progress")

        later = time.time() + lifecycle.TEARDOWN_MARKER_TTL_SECONDS + 1
        with mock.patch("portskill.teardown_marker.time.time", return_value=later):
            with self.assertRaises(DomainError) as caught:
                start_result({"project": self.project, "range_id": "mapped"})
        # Past the TTL the marker no longer blocks; the start fails on its own
        # (no start script in this fixture), not on the marker.
        self.assertNotEqual(caught.exception.reason, "teardown_in_progress")

    def test_plain_stop_still_tears_down_under_its_own_lock(self) -> None:
        # Other park_item_for_exit callers are unchanged: stop still runs the
        # teardown itself and releases on success.
        from portskill.process_commands import stop_result

        self._write()
        with self._fake_tailnet():
            result = stop_result({"project": self.project, "range_id": "mapped", "also_release": True})
        self.assertEqual(result["status"], "ok")
        self.assertEqual([c["off"] for c in self.calls], [True])
        self.assertEqual(self._stored("mapped")["state"], "released")

    # -- lock 2 trusts more than the token (review fix) ----------------------

    def _edit(self, range_id: str, edit) -> None:
        """Change one stored range in its own locked write (as a concurrent command would)."""
        from portskill.registry_operations import locked_registry

        def mutate(registry):
            item = next(r for r in registry["projects"][self.project]["ranges"] if r["id"] == range_id)
            edit(item)
            return None, [self.project]

        locked_registry(mutate)

    @staticmethod
    def _reactivate(item: dict) -> None:
        # What a start does once the marker has expired: active, live pid, and
        # the sweep marker (same token) still on the record.
        item["state"] = "active"
        item["lifecycle"]["pid"] = os.getpid()
        item["tailnet"].pop("restore_pending", None)

    def test_range_restarted_after_marker_expiry_is_not_released(self) -> None:
        self._write()

        def expired_then_started() -> None:
            if self._lock_held():
                return  # teardown under the lock: re-locking would deadlock; fail below instead
            self._edit("mapped", self._reactivate)

        with self._fake_tailnet(during=expired_then_started):
            result = self._sweep()

        self.assertEqual([r["range_id"] for r in result["failed"]], ["mapped"])
        self.assertEqual(result["failed"][0]["reason"], "changed_during_teardown")
        mapped = self._stored("mapped")
        self.assertEqual(mapped["state"], "active")  # never released under a live process
        self.assertEqual(mapped["lifecycle"]["pid"], os.getpid())

    def _write_two_mapped(self) -> None:
        from portskill.status import status_result

        self.iso.write_registry({"projects": {self.project: {"ranges": [
            self._range("mapped", mapped=True),
            self._range("mapped2", mapped=True),
        ]}}})
        status_result(None)

    def _sweep_two(self) -> dict:
        from portskill.sweep import sweep_result

        return sweep_result({"release": True, "range_ids": ["mapped", "mapped2"]})

    def test_each_teardown_restamps_an_ageing_marker(self) -> None:
        self._write_two_mapped()
        seen: list = []

        def during() -> None:
            if self._lock_held():
                return
            if not seen:
                # A slow first `off`: the second range's marker is nearly expired.
                def age(item):
                    item["tailnet"]["sweep_teardown"]["at"] = time.time() - 100
                self._edit("mapped2", age)
            seen.append(self._stored("mapped2")["tailnet"].get("sweep_teardown"))

        with self._fake_tailnet(during=during):
            result = self._sweep_two()

        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sorted(r["range_id"] for r in result["released"]), ["mapped", "mapped2"])
        # While the second `off` ran, its marker had been re-stamped (still fresh).
        self.assertLess(time.time() - seen[1]["at"], 30)
        self.assertEqual(seen[1]["token"], seen[0]["token"])

    def test_range_changed_before_its_teardown_is_not_torn_down(self) -> None:
        self._write_two_mapped()

        def during() -> None:
            if self._lock_held():
                return
            if len(self.calls) == 1:
                self._edit("mapped2", self._reactivate)

        with self._fake_tailnet(during=during):
            result = self._sweep_two()

        # mapped2 was restarted before its turn: its live mapping is left alone.
        self.assertEqual([c["port"] for c in self.calls], [self.ports["mapped"]])
        self.assertEqual([r["range_id"] for r in result["released"]], ["mapped"])
        self.assertEqual([(r["range_id"], r["reason"]) for r in result["failed"]],
                         [("mapped2", "changed_during_teardown")])
        mapped2 = self._stored("mapped2")
        self.assertEqual(mapped2["state"], "active")
        self.assertTrue(mapped2["tailnet"]["configured_at"])

    def test_future_marker_beyond_the_ttl_counts_as_expired(self) -> None:
        from portskill.lifecycle import TEARDOWN_MARKER_TTL_SECONDS, teardown_marker

        item = self._range("mapped", mapped=True)
        item["tailnet"]["sweep_teardown"] = {"token": "t", "at": time.time() + 10}
        self.assertIsNotNone(teardown_marker(item))  # small skew still guards
        item["tailnet"]["sweep_teardown"]["at"] = time.time() + TEARDOWN_MARKER_TTL_SECONDS + 5
        self.assertIsNone(teardown_marker(item))

    # -- path guards (review fix) --------------------------------------------

    def _park_by_crashed_sweep(self) -> None:
        self._write()

        def crash() -> None:
            raise _Crash()

        with self._fake_tailnet(during=crash), self.assertRaises(_Crash):
            self._sweep()
        self.assertEqual(self._stored("mapped")["state"], "reserved")

    def test_path_activate_refuses_a_range_under_teardown(self) -> None:
        from portskill.domain import DomainError
        from portskill.path import run_path

        self._park_by_crashed_sweep()
        with mock.patch("portskill.path.probe_tailscale_status", return_value={"logged_in": False}):
            with self.assertRaises(DomainError) as caught:
                run_path(mode="start", project=self.project, range_id="mapped")
        self.assertEqual(caught.exception.reason, "teardown_in_progress")
        mapped = self._stored("mapped")
        self.assertEqual(mapped["state"], "reserved")
        self.assertTrue(mapped["tailnet"]["restore_pending"])

    def test_path_tailnet_already_keeps_restore_pending_under_teardown(self) -> None:
        from portskill.path import SKIP_TAILNET_ALREADY, run_path

        self._park_by_crashed_sweep()
        # Bypass the activate guard to reach the tailnet phase on the parked range.
        with (
            mock.patch("portskill.path.refuse_teardown_in_progress"),
            mock.patch("portskill.path.probe_tailscale_status", return_value={"logged_in": True}),
            self._fake_tailnet(),
        ):
            try:
                payload = run_path(mode="start", project=self.project, range_id="mapped", tailnet="serve")
            except Exception as exc:  # noqa: BLE001 - only the tailnet phase matters here
                self.fail(f"path start raised {exc!r}")
        skipped = {e.get("phase"): e.get("reason") for e in payload.get("skipped") or []}
        self.assertEqual(skipped.get("tailnet"), SKIP_TAILNET_ALREADY, payload)
        self.assertTrue(self._stored("mapped")["tailnet"].get("restore_pending"))


if __name__ == "__main__":
    unittest.main()
