"""History restore releases ranges missing from the snapshot with stop's
teardown contract: a failed Tailscale teardown never leaves a dead pid active."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class HistoryRestoreTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.project = str((self.iso.root / "project").resolve())
        os.mkdir(self.project)
        # Detach the sleep from this test process so init reaps it after
        # SIGTERM; a direct child would linger as a zombie and still look live.
        shell = subprocess.Popen(
            ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
            stdout=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        assert shell.stdout is not None
        self.pid = int(shell.stdout.readline())
        shell.stdout.close()
        shell.wait()
        self.pgid = shell.pid  # the backgrounded sleep keeps the shell's pgid
        self.addCleanup(self._kill)

    def _kill(self) -> None:
        try:
            os.killpg(self.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _served_range_after_empty_baseline(self) -> dict:
        from portskill.history import history_clear_result
        from portskill.registry_operations import range_record

        self.iso.write_registry({"projects": {self.project: {"ranges": []}}})
        history_clear_result()  # baseline: no ranges
        item = range_record("r1", 22140, 22140, None, "serve", default_state="off")
        item["state"] = "active"
        item["tailnet"]["port"] = 22140
        item["tailnet"]["configured_at"] = "2026-10-25T00:00:00Z"
        item["lifecycle"]["pid"] = self.pid
        item["lifecycle"]["pgid"] = self.pgid
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        data["projects"][self.project]["ranges"] = [item]
        self.iso.registry_path.write_text(json.dumps(data), encoding="utf-8")
        return item

    def _stored(self) -> dict:
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        return data["projects"][self.project]["ranges"][0]

    def _process_gone(self) -> bool:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def test_restore_tears_down_and_releases(self) -> None:
        from portskill import lifecycle
        from portskill.history import history_restore_result

        self._served_range_after_empty_baseline()
        with mock.patch.object(lifecycle, "run_tailnet") as run_tailnet:
            payload = history_restore_result({"index": 0})
        self.assertEqual(payload["status"], "ok")
        run_tailnet.assert_called_once_with("serve", 22140, off=True)
        stored = self._stored()
        self.assertEqual(stored["state"], "released")
        self.assertIsNone(stored["tailnet"]["configured_at"])
        self.assertIsNone(stored["lifecycle"]["pid"])
        self.assertTrue(self._process_gone())

    def test_restore_teardown_failure_parks_reserved_and_commits(self) -> None:
        from portskill import lifecycle
        from portskill.domain import DomainError
        from portskill.history import history_restore_result

        before = self._served_range_after_empty_baseline()
        failure = DomainError("tailnet_command_failed", "exit 1")
        with mock.patch.object(lifecycle, "run_tailnet", side_effect=failure):
            with self.assertRaises(DomainError) as caught:
                history_restore_result({"index": 0})
        payload = caught.exception.as_dict()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["reason"], "tailnet_teardown_failed")
        self.assertIn("Fix Tailscale", payload["message"])
        self.assertEqual(payload["failures"][0]["range_id"], "r1")
        self.assertEqual(payload["failures"][0]["error"]["reason"], "tailnet_command_failed")
        # Committed, never rolled back to active with the dead pid.
        self.assertTrue(self._process_gone())
        stored = self._stored()
        self.assertEqual(stored["state"], "reserved")
        self.assertIsNone(stored["lifecycle"]["pid"])
        self.assertIsNone(stored["lifecycle"]["pgid"])
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertEqual(stored["tailnet"]["configured_at"], before["tailnet"]["configured_at"])
        self.assertIs(stored["tailnet"]["restore_pending"], True)
        # Once Tailscale works, the same restore finishes the job.
        with mock.patch.object(lifecycle, "run_tailnet") as run_tailnet:
            retried = history_restore_result({"index": 0})
        self.assertEqual(retried["status"], "ok")
        run_tailnet.assert_called_once_with("serve", 22140, off=True)
        stored = self._stored()
        self.assertEqual(stored["state"], "released")
        self.assertNotIn("restore_pending", stored["tailnet"])


if __name__ == "__main__":
    unittest.main()
