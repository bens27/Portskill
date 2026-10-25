"""path --mode restart re-applies the recorded Tailnet mapping (fake tailscale)."""
from __future__ import annotations

import json
import os
import pathlib
import signal
import stat
import unittest

from tests.helpers import IsolatedConfig, parse_cli_json

# Fake tailscale: `status --json` answers from a state file; every other call
# appends its argv to a log and exits with the code in an exit file.
_FAKE_TAILSCALE = """#!/bin/sh
dir="$(dirname "$0")"
if [ "$1" = "status" ]; then
  state="$(cat "$dir/ts-state" 2>/dev/null || echo Running)"
  printf '{"BackendState":"%s","Self":{}}\\n' "$state"
  exit 0
fi
echo "$*" >> "$dir/ts-calls.log"
exit "$(cat "$dir/ts-exit" 2>/dev/null || echo 0)"
"""


class _FakeTailscale:
    def __init__(self, iso: IsolatedConfig) -> None:
        self.dir = iso.root / "fake-ts"
        self.dir.mkdir()
        self.bin = self.dir / "tailscale"
        self.bin.write_text(_FAKE_TAILSCALE, encoding="utf-8")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.log = self.dir / "ts-calls.log"
        self.env = {
            "PORTSKILL_TAILSCALE_BIN": str(self.bin),
            "PORTSKILL_TAILSCALE_SUDO": "0",
        }

    def set_state(self, backend: str) -> None:
        (self.dir / "ts-state").write_text(backend, encoding="utf-8")

    def set_exit(self, code: int) -> None:
        (self.dir / "ts-exit").write_text(str(code), encoding="utf-8")

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def reset_calls(self) -> None:
        if self.log.exists():
            self.log.unlink()


def _range(payload: dict) -> dict:
    return (payload.get("result") or {}).get("range") or {}


def _phase(entries: list, name: str) -> dict | None:
    for entry in entries or []:
        if entry.get("phase") == name:
            return entry
    return None


class PathRestartTailnetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.addCleanup(self._kill_leftovers)
        self.ts = _FakeTailscale(self.iso)
        self.proj = self.iso.root / "proj"
        self.proj.mkdir()

    def _kill_leftovers(self) -> None:
        try:
            data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for proj in (data.get("projects") or {}).values():
            for item in proj.get("ranges") or [] if isinstance(proj, dict) else []:
                life = (item or {}).get("lifecycle") or {}
                for key, kill in (("pgid", os.killpg), ("pid", os.kill)):
                    target = life.get(key)
                    if target:
                        try:
                            kill(int(target), signal.SIGTERM)
                        except (OSError, ValueError):
                            pass

    def _cli(self, argv: list[str], **extra: str):
        env = dict(self.ts.env)
        env.update(extra)
        return self.iso.run_cli(argv, extra=env)

    def _path(self, mode: str, *more: str, **extra: str):
        return self._cli(["path", "--mode", mode, "--project", str(self.proj), *more], **extra)

    def _start_served(self, tailnet: str = "serve") -> dict:
        self.ts.set_state("Running")
        started = self._path("start", "--command", "sleep 60", "--tailnet", tailnet)
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        rng = _range(parse_cli_json(started))
        self.assertEqual(rng["tailnet"]["mode"], tailnet)
        self.assertTrue(rng["tailnet"]["configured_at"])
        self.ts.reset_calls()
        return rng

    def _registry_range(self, range_id: str) -> dict:
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        for proj in (data.get("projects") or {}).values():
            for item in proj.get("ranges") or []:
                if item.get("id") == range_id:
                    return item
        self.fail(f"range {range_id} not in registry")

    def test_restart_reapplies_recorded_serve(self) -> None:
        before = self._start_served()
        port = before["start"]
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 0, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        tail = _phase(payload.get("ran"), "tailnet")
        self.assertIsNotNone(tail, payload)
        self.assertEqual(tail["detail"]["source"], "recorded")
        self.assertEqual(tail["detail"]["mode"], "serve")
        self.assertEqual(
            self.ts.calls(),
            [
                f"serve --bg --https={port} localhost:{port} off",
                f"serve --bg --https={port} localhost:{port}",
            ],
        )
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "active")
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertTrue(stored["tailnet"]["configured_at"])
        self.assertNotEqual(stored["tailnet"]["configured_at"], before["tailnet"]["configured_at"])

    def test_restart_logged_out_returns_needs_input_and_keeps_mode(self) -> None:
        self._restart_logged_out()

    def _restart_logged_out(self) -> dict:
        before = self._start_served()
        self.ts.set_state("NeedsLogin")
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 3, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["reason"], "tailscale_auth_required")
        self.assertEqual(payload["input"], "tailscale_login")
        self.assertEqual(payload["tailnet_restore"]["mode"], "serve")
        self.assertEqual(payload["tailnet_restore"]["port"], before["start"])
        self.assertIn("--tailnet serve", payload["prompt"])
        self.assertEqual(payload["options"], ["serve", "none"])
        self.assertEqual(payload["resume_hint"], "--tailnet")
        self.assertNotIn("source", payload["tailnet_restore"])
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertIsNone(stored["tailnet"]["configured_at"])
        self.assertIs(stored["tailnet"]["restore_pending"], True)
        return before

    def _assert_reapplied(self, proc, range_id: str, port: int) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn(f"serve --bg --https={port} localhost:{port}", self.ts.calls())
        stored = self._registry_range(range_id)
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertTrue(stored["tailnet"]["configured_at"])
        self.assertNotIn("restore_pending", stored["tailnet"])

    def test_retried_restart_after_login_reapplies(self) -> None:
        before = self._restart_logged_out()
        self.ts.set_state("Running")
        self.ts.reset_calls()
        retried = self._path("restart")
        self._assert_reapplied(retried, before["id"], before["start"])
        self.assertEqual(_phase(parse_cli_json(retried)["ran"], "tailnet")["detail"]["source"], "recorded")

    def test_advertised_resume_after_login_reapplies(self) -> None:
        before = self._restart_logged_out()
        self.ts.set_state("Running")
        self.ts.reset_calls()
        # resume_hint + option: the same operation with `--tailnet serve`.
        resumed = self._path("restart", "--tailnet", "serve")
        self._assert_reapplied(resumed, before["id"], before["start"])

    def test_start_resume_after_login_reapplies(self) -> None:
        before = self._restart_logged_out()
        self.ts.set_state("Running")
        self.ts.reset_calls()
        resumed = self._path("start", "--tailnet", "serve")
        self._assert_reapplied(resumed, before["id"], before["start"])

    def test_declining_pending_restore_drops_it(self) -> None:
        before = self._restart_logged_out()
        self.ts.set_state("Running")
        declined = self._path("restart", "--tailnet", "none")
        self.assertEqual(declined.returncode, 0, declined.stderr or declined.stdout)
        self.assertNotIn("restore_pending", self._registry_range(before["id"])["tailnet"])
        self.ts.reset_calls()
        again = self._path("restart")
        self.assertEqual(again.returncode, 0, again.stderr or again.stdout)
        self.assertNotIn("tailnet_restore", parse_cli_json(again))
        port = before["start"]
        self.assertNotIn(f"serve --bg --https={port} localhost:{port}", self.ts.calls())

    def test_restart_serve_failure_keeps_registry_and_asks(self) -> None:
        before = self._start_served()
        old_pid = self._registry_range(before["id"])["lifecycle"]["pid"]
        # Teardown (`... off`) succeeds, the re-apply exits 1.
        script = self.ts.bin.read_text(encoding="utf-8").replace(
            'echo "$*" >> "$dir/ts-calls.log"',
            'echo "$*" >> "$dir/ts-calls.log"\n'
            'case "$*" in *off) exit 0;; esac',
        )
        self.ts.bin.write_text(script, encoding="utf-8")
        self.ts.set_exit(1)
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 3, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertEqual(payload["reason"], "tailnet_restore_failed")
        self.assertEqual(payload["input"], "tailnet_restore")
        self.assertEqual(payload["error"]["reason"], "tailnet_command_failed")
        tail = _phase(payload.get("skipped"), "tailnet")
        self.assertEqual(tail["reason"], "restore_failed")
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "active")
        self.assertNotEqual(stored["lifecycle"]["pid"], old_pid)
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertIsNone(stored["tailnet"]["configured_at"])
        self.assertIs(stored["tailnet"]["restore_pending"], True)
        # Once Tailscale works again, a second plain restart re-applies it.
        self.ts.set_exit(0)
        self.ts.reset_calls()
        retried = self._path("restart")
        self._assert_reapplied(retried, before["id"], before["start"])

    def test_restart_explicit_none_skips_restore(self) -> None:
        before = self._start_served()
        restarted = self._path("restart", "--tailnet", "none")
        self.assertEqual(restarted.returncode, 0, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertNotIn("tailnet_restore", payload)
        port = before["start"]
        self.assertEqual(self.ts.calls(), [f"serve --bg --https={port} localhost:{port} off"])
        stored = self._registry_range(before["id"])
        self.assertIsNone(stored["tailnet"]["configured_at"])
        # The stop phase marks the mapping pending; declining clears it after.
        self.assertNotIn("restore_pending", stored["tailnet"])

    def test_restart_unapplied_recorded_mode_is_not_restored(self) -> None:
        # Login skipped at start: mode recorded, never configured.
        self.ts.set_state("NeedsLogin")
        started = self._path("start", "--command", "sleep 60", "--tailnet", "serve")
        self.assertEqual(started.returncode, 3, started.stderr or started.stdout)
        rid = _range(parse_cli_json(started)).get("id")
        stored = self._registry_range(rid)
        stored_tailnet = stored.get("tailnet") or {}
        self.assertFalse(stored_tailnet.get("configured_at"))
        self.ts.set_state("Running")
        self.ts.reset_calls()
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 0, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertNotIn("tailnet_restore", payload)
        self.assertEqual(_phase(payload.get("skipped"), "tailnet")["reason"], "not_requested")
        # Never configured, so the stop phase sends no `off` either.
        self.assertEqual(self.ts.calls(), [])
        self.assertNotIn("restore_pending", self._registry_range(rid)["tailnet"])

    def test_plain_stop_default_releases(self) -> None:
        before = self._start_served()
        stopped = self._path("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "released")
        self.assertIsNone(stored["tailnet"]["configured_at"])
        port = before["start"]
        self.assertEqual(self.ts.calls(), [f"serve --bg --https={port} localhost:{port} off"])

    def test_plain_stop_without_release_keeps_mode(self) -> None:
        self._cli(["settings", "set", "--stop-also-release", "off"])
        before = self._start_served()
        stopped = self._path("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        payload = parse_cli_json(stopped)
        self.assertNotIn("tailnet_restore", payload)
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "reserved")
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertIsNone(stored["tailnet"]["configured_at"])
        self.assertIs(stored["tailnet"]["restore_pending"], True)
        port = before["start"]
        self.assertEqual(self.ts.calls(), [f"serve --bg --https={port} localhost:{port} off"])

    def test_stop_without_release_then_restart_restores(self) -> None:
        self._cli(["settings", "set", "--stop-also-release", "off"])
        before = self._start_served()
        stopped = self._path("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        self.ts.reset_calls()
        restarted = self._path("restart")
        self._assert_reapplied(restarted, before["id"], before["start"])
        self.assertEqual(self._registry_range(before["id"])["state"], "active")
        # Already torn down by the stop: the restart's stop phase sends no `off`.
        port = before["start"]
        self.assertNotIn(f"serve --bg --https={port} localhost:{port} off", self.ts.calls())

    def test_release_drops_pending_restore(self) -> None:
        before = self._restart_logged_out()
        self.ts.set_state("Running")
        stopped = self._path("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "released")
        self.assertNotIn("restore_pending", stored["tailnet"])

    def _assert_parked_after_teardown_failure(self, before: dict, old_pid: int) -> dict:
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "reserved")
        self.assertIsNone(stored["lifecycle"]["pid"])
        self.assertTrue(stored["lifecycle"]["stopped_at"])
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        # The mapping may still be live: it stays recorded.
        self.assertEqual(stored["tailnet"]["configured_at"], before["tailnet"]["configured_at"])
        self.assertIs(stored["tailnet"]["restore_pending"], True)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(old_pid), 0)
        return stored

    def _stop_with_failing_teardown(self, *argv: str, release: bool):
        if not release:
            self._cli(["settings", "set", "--stop-also-release", "off"])
        before = self._start_served()
        old_pid = self._registry_range(before["id"])["lifecycle"]["pid"]
        self.ts.set_exit(1)
        if argv:
            proc = self._cli([*argv, before["id"], "--project", str(self.proj)])
        else:
            proc = self._path("stop")
        self.assertEqual(proc.returncode, 2, proc.stderr or proc.stdout)
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("tailnet_teardown_failed", text)
        self.assertIn("could not remove the Tailscale serve mapping", text)
        port = before["start"]
        self.assertIn(f"serve --bg --https={port} localhost:{port} off", self.ts.calls())
        return self._assert_parked_after_teardown_failure(before, old_pid)

    def test_path_stop_teardown_failure_commits_reserved(self) -> None:
        self._stop_with_failing_teardown(release=False)

    def test_path_stop_release_teardown_failure_does_not_release(self) -> None:
        self._stop_with_failing_teardown(release=True)

    def test_stop_range_id_teardown_failure_commits_reserved(self) -> None:
        self._stop_with_failing_teardown("stop", "--range-id", release=True)

    def _assert_declined_and_torn_down(self, proc, before: dict, mode: str) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        port = before["start"]
        self.assertIn(f"serve --bg --https={port} localhost:{port} off", self.ts.calls())
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["tailnet"]["mode"], mode)
        self.assertIsNone(stored["tailnet"]["configured_at"])
        self.assertNotIn("restore_pending", stored["tailnet"])
        # A later plain restart leaves the declined mapping off.
        self.ts.reset_calls()
        again = self._path("restart")
        self.assertEqual(again.returncode, 0, again.stderr or again.stdout)
        self.assertNotIn("tailnet_restore", parse_cli_json(again))
        self.assertEqual(self.ts.calls(), [])

    def test_restart_none_after_failed_stop_teardown_takes_mapping_down(self) -> None:
        self._stop_with_failing_teardown(release=False)
        before = self._registry_range(self._only_range_id())
        self.ts.set_exit(0)
        self.ts.reset_calls()
        declined = self._path("restart", "--tailnet", "none")
        # Torn down by the decline itself: the mapping record is cleared.
        self._assert_declined_and_torn_down(declined, before, "none")

    def test_restart_none_with_failing_tailscale_warns_then_retry_takes_down(self) -> None:
        before = self._start_served()
        old_pid = self._registry_range(before["id"])["lifecycle"]["pid"]
        self.ts.set_exit(1)
        declined = self._path("restart", "--tailnet", "none")
        self.assertEqual(declined.returncode, 0, declined.stderr or declined.stdout)
        payload = parse_cli_json(declined)
        self.assertEqual(
            [w["reason"] for w in payload["warnings"]],
            ["tailnet_teardown_failed", "tailnet_teardown_failed"],
        )
        self.assertIn("--tailnet none", payload["warnings"][1]["message"])
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "active")
        self.assertNotEqual(stored["lifecycle"]["pid"], old_pid)
        # Possibly still live: kept recorded, but no pending restore.
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertEqual(stored["tailnet"]["configured_at"], before["tailnet"]["configured_at"])
        self.assertNotIn("restore_pending", stored["tailnet"])
        self.ts.set_exit(0)
        self.ts.reset_calls()
        retried = self._path("restart", "--tailnet", "none")
        # Torn down by the stop phase, as in a plain `--tailnet none` restart:
        # the mode stays recorded but unapplied and nothing is pending.
        self._assert_declined_and_torn_down(retried, before, "serve")

    def test_restart_with_failed_teardown_still_served_clears_pending(self) -> None:
        before = self._start_served()
        self.ts.set_exit(1)
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 0, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertEqual(payload["warnings"][0]["reason"], "tailnet_teardown_failed")
        self.assertEqual(_phase(payload.get("skipped"), "tailnet")["reason"], "already_served")
        stored = self._registry_range(before["id"])
        self.assertEqual(stored["state"], "active")
        self.assertTrue(stored["tailnet"]["configured_at"])
        self.assertNotIn("restore_pending", stored["tailnet"])

    def _only_range_id(self) -> str:
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        ids = [
            item["id"]
            for proj in (data.get("projects") or {}).values()
            for item in proj.get("ranges") or []
        ]
        self.assertEqual(len(ids), 1, ids)
        return ids[0]

    def test_restart_funnel_of_listen_port_is_skipped_not_asked(self) -> None:
        before = self._start_served("funnel")
        self.iso.write_listen({"port": before["start"]})
        restarted = self._path("restart")
        self.assertEqual(restarted.returncode, 0, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertNotEqual(payload.get("status"), "needs_input")
        tail = _phase(payload.get("skipped"), "tailnet")
        self.assertIsNotNone(tail, payload)
        self.assertEqual(tail["reason"], "funnel_listen")
        port = before["start"]
        self.assertNotIn(f"funnel --bg --https={port} localhost:{port}", self.ts.calls())

    def test_restart_with_tailscale_missing_asks_and_keeps_new_process(self) -> None:
        before = self._start_served()
        old_pid = self._registry_range(before["id"])["lifecycle"]["pid"]
        restarted = self._path(
            "restart", PORTSKILL_TAILSCALE_BIN=str(self.iso.root / "no-tailscale"),
        )
        self.assertEqual(restarted.returncode, 3, restarted.stderr or restarted.stdout)
        payload = parse_cli_json(restarted)
        self.assertEqual(payload["reason"], "tailscale_bin_missing")
        self.assertEqual(payload["input"], "tailscale_install")
        self.assertEqual(payload["tailnet_restore"]["mode"], "serve")
        warning = payload["warnings"][0]
        self.assertEqual(warning["reason"], "tailnet_teardown_failed")
        self.assertEqual(warning["error"]["reason"], "tailscale_missing")
        stored = self._registry_range(before["id"])
        # Committed, never rolled back to the dead pid.
        self.assertEqual(stored["state"], "active")
        self.assertTrue(stored["lifecycle"]["pid"])
        self.assertNotEqual(stored["lifecycle"]["pid"], old_pid)
        self.assertEqual(stored["tailnet"]["mode"], "serve")
        self.assertIs(stored["tailnet"]["restore_pending"], True)


class TailnetRestoreNeedsInputTests(unittest.TestCase):
    """Payload builder, pinned directly for every reason."""

    RESTORE = {"mode": "serve", "port": 20000, "previous_configured_at": None}

    def _build(self, tailscale: dict, failure: dict | None = None) -> dict:
        from portskill.path import _tailnet_restore_needs_input

        return _tailnet_restore_needs_input([], [], {}, self.RESTORE, tailscale, failure)

    def test_binary_missing(self) -> None:
        payload = self._build({"state": "binary_missing"})
        self.assertEqual(payload["reason"], "tailscale_bin_missing")
        self.assertEqual(payload["input"], "tailscale_install")
        self.assertEqual(payload["options"], ["serve", "none"])
        self.assertEqual(payload["resume_hint"], "--tailnet")
        self.assertEqual(payload["tailnet_restore"], self.RESTORE)
        self.assertNotIn("error", payload)

    def test_failure_wins_over_status(self) -> None:
        failure = {"status": "error", "reason": "tailnet_command_failed", "message": "exit 1"}
        payload = self._build({"state": "binary_missing"}, failure)
        self.assertEqual(payload["reason"], "tailnet_restore_failed")
        self.assertEqual(payload["error"], failure)
        self.assertIn("exit 1", payload["prompt"])

    def test_logged_out(self) -> None:
        payload = self._build({"state": "needs_login", "logged_in": False})
        self.assertEqual(payload["reason"], "tailscale_auth_required")
        self.assertEqual(payload["input"], "tailscale_login")


if __name__ == "__main__":
    unittest.main()
