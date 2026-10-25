"""History restore never rolls a range it already killed back to active with a
dead pid when something later in the same restore fails."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


def _spawn_detached_sleep() -> tuple[int, int]:
    """Start a sleep that init reaps (a direct child would linger as a zombie
    and still look live after SIGTERM). Returns (pid, pgid)."""
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


class HistoryRestoreRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.project = str((self.iso.root / "project").resolve())
        os.mkdir(self.project)
        self.pid, self.pgid = _spawn_detached_sleep()
        self.addCleanup(self._kill)

    def _kill(self) -> None:
        try:
            os.killpg(self.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _range(self, range_id: str, port: int, *, active: bool = False) -> dict:
        from portskill.registry_operations import range_record

        item = range_record(range_id, port, port, None, "none", default_state="off")
        if active:
            item["state"] = "active"
            item["lifecycle"]["pid"] = self.pid
            item["lifecycle"]["pgid"] = self.pgid
        return item

    def _baseline(self, ranges: list[dict]) -> None:
        from portskill.history import history_clear_result

        self.iso.write_registry({"projects": {self.project: {"ranges": ranges}}})
        history_clear_result()

    def _set_ranges(self, ranges: list[dict]) -> None:
        data = self._read()
        data["projects"][self.project]["ranges"] = ranges
        self.iso.registry_path.write_text(json.dumps(data), encoding="utf-8")

    def _read(self) -> dict:
        return json.loads(self.iso.registry_path.read_text(encoding="utf-8"))

    def _stored(self, range_id: str) -> dict:
        ranges = self._read()["projects"][self.project]["ranges"]
        return next(item for item in ranges if item["id"] == range_id)

    def _process_gone(self) -> bool:
        deadline = time.monotonic() + 2.0
        while _alive(self.pid):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True

    def test_later_process_that_will_not_die_keeps_earlier_kills_committed(self) -> None:
        from portskill import history
        from portskill.domain import DomainError

        self._baseline([])
        stuck = self._range("r2", 22150, active=True)
        stuck["lifecycle"]["pid"] = None
        stuck["lifecycle"]["pgid"] = None
        after = self._range("r3", 22160, active=True)
        after["lifecycle"]["pid"] = None
        after["lifecycle"]["pgid"] = None
        self._set_ranges([self._range("r1", 22140, active=True), stuck, after])
        real_live = history.range_has_live_process

        def live(item):
            return True if item.get("id") in ("r2", "r3") else real_live(item)

        with mock.patch.object(history, "range_has_live_process", side_effect=live), \
                mock.patch.object(
                    history,
                    "terminate_lifecycle_processes",
                    wraps=history.terminate_lifecycle_processes,
                ) as terminate:
            with self.assertRaises(DomainError) as caught:
                history.history_restore_result({"index": 0})
        # No range after the stuck one is stopped.
        self.assertEqual([c.args[0]["id"] for c in terminate.call_args_list], ["r1", "r2"])
        self.assertEqual(self._stored("r3")["state"], "active")
        # r1 was killed: it must be committed released, not rolled back to
        # active with its dead pid.
        self.assertTrue(self._process_gone())
        first = self._stored("r1")
        self.assertEqual(first["state"], "released")
        self.assertIsNone(first["lifecycle"]["pid"])
        payload = caught.exception.as_dict()
        self.assertEqual(payload["reason"], "process_still_running")
        self.assertEqual(payload["cursor"], 0)
        self.assertEqual([f["range_id"] for f in payload["failures"]], ["r2"])
        # r2 is untouched: still active (its process is genuinely live).
        self.assertEqual(self._stored("r2")["state"], "active")

    def test_conflict_with_range_the_loop_does_not_release_is_refused_first(self) -> None:
        """Id-less and duplicate-id cohort ranges are not released by the loop,
        so the pre-flight must not treat their ports as free either."""
        from portskill.domain import DomainError
        from portskill.history import history_restore_result

        def idless() -> list[dict]:
            item = self._range("x", 22150)
            del item["id"]
            return [item]

        def duplicate() -> list[dict]:
            return [self._range("dup", 22150), self._range("dup", 22170)]

        for name, blockers in (("id-less", idless), ("duplicate id", duplicate)):
            with self.subTest(name):
                self._baseline([self._range("r2", 22150)])
                self._set_ranges([self._range("r1", 22140, active=True), *blockers()])
                with self.assertRaises(DomainError) as caught:
                    history_restore_result({"index": 0})
                self.assertEqual(caught.exception.as_dict()["reason"], "port_in_use")
                self.assertTrue(_alive(self.pid))
                first = self._stored("r1")
                self.assertEqual(first["state"], "active")
                self.assertEqual(first["lifecycle"]["pid"], self.pid)

    def test_snapshot_port_conflict_is_refused_before_killing_anything(self) -> None:
        from portskill.domain import DomainError
        from portskill.history import history_restore_result

        self._baseline([self._range("r2", 22150), self._range("r3", 22160)])
        # r2 and r3 swapped ports since the baseline, and r1 (not in the
        # baseline) is serving: applying r2 back onto 22150 collides with r3.
        before_ranges = [
            self._range("r1", 22140, active=True),
            self._range("r2", 22160),
            self._range("r3", 22150),
        ]
        self._set_ranges(before_ranges)
        before = self._read()
        with self.assertRaises(DomainError) as caught:
            history_restore_result({"index": 0})
        self.assertEqual(caught.exception.as_dict()["reason"], "port_in_use")
        # Refused up front: r1 still runs, and nothing was written.
        self.assertTrue(_alive(self.pid))
        first = self._stored("r1")
        self.assertEqual(first["state"], "active")
        self.assertEqual(first["lifecycle"]["pid"], self.pid)
        self.assertEqual(self._read()["projects"], before["projects"])


if __name__ == "__main__":
    unittest.main()
