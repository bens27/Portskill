"""Regression coverage for truthful stop, status, and restart verification."""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager

from tests.helpers import IsolatedConfig, free_loopback_port


LISTENER_CODE = r"""
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen()
print("READY", flush=True)
while True:
    conn, _ = s.accept()
    conn.close()
"""


def can_connect(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


@contextmanager
def listener(project: pathlib.Path, port: int | None = None):
    port = port or free_loopback_port()
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", LISTENER_CODE, str(port)],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise AssertionError(f"listener exited early: {stdout!r} {stderr!r}")
            if can_connect(port):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"listener did not bind {port}")
        yield proc, port
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


def range_record(port: int, *, state: str = "active", end: int | None = None,
                 pid: int | None = None, machine_id: str = "local") -> dict:
    return {
        "id": "service",
        "start": port,
        "end": end if end is not None else port,
        "state": state,
        "machine_id": machine_id,
        "command": None,
        "tailnet": {"mode": "none"},
        "lifecycle": {
            "pid": pid,
            "pgid": None,
            "started_at": None,
            "stopped_at": None,
        },
    }


class StopVerificationTests(unittest.TestCase):
    def _write(self, iso: IsolatedConfig, project: pathlib.Path, item: dict,
               *, stop_also_release: bool = True, machines: dict | None = None) -> None:
        iso.write_registry({
            "settings": {"stop_also_release": stop_also_release},
            "machines": machines or {},
            "projects": {str(project): {"ranges": [item]}},
        })

    def _stored(self, iso: IsolatedConfig, project: pathlib.Path) -> dict:
        raw = json.loads(iso.registry_path.read_text(encoding="utf-8"))
        return raw["projects"][str(project)]["ranges"][0]

    def test_polling_disables_stop_without_stoppable_local_evidence(self) -> None:
        from portskill.server import build_view, render_page

        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is not installed")

        page = render_page(build_view({}))
        assignment = re.search(r"if\(stop\)stop\.disabled=[^;]+;", page)
        self.assertIsNotNone(assignment, "rendered Stop polling assignment missing")
        cases = [
            ({"state": "active", "reachable": True, "process_alive": None}, False),
            ({"state": "occupied", "reachable": True, "process_alive": None}, False),
            ({"state": "released", "reachable": False, "process_alive": None}, True),
            ({"state": "inactive", "reachable": False, "process_alive": False}, True),
            ({"state": "unknown", "reachable": None, "process_alive": None}, True),
            ({"state": "inactive", "reachable": False, "process_alive": True}, False),
        ]
        script = (
            "const cases=" + json.dumps(cases) + ";"
            "for(const [service,expected] of cases){"
            "const local=service.state!=='unknown';const stop={};"
            + assignment.group(0)
            + "if(stop.disabled!==expected){"
            "console.error(JSON.stringify({service,expected,actual:stop.disabled}));"
            "process.exitCode=1;}}"
        )
        result = subprocess.run([node, "-e", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stop_does_not_release_untracked_external_listener(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (proc, port):
                self._write(iso, project, range_record(port))
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertTrue(can_connect(port))
                self.assertIsNone(proc.poll())
                self.assertEqual(self._stored(iso, project)["state"], "active")
                self.assertIn("original terminal", json.dumps(body).lower())

    def test_stop_without_release_keeps_active_when_listener_persists(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (_, port):
                self._write(iso, project, range_record(port), stop_also_release=False)
                code, _ = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0)
                self.assertEqual(self._stored(iso, project)["state"], "active")

    def test_already_released_stop_detects_live_listener(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (_, port):
                self._write(iso, project, range_record(port, state="released"))
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertEqual(self._stored(iso, project)["state"], "released")

    def test_stop_checks_secondary_port_in_range(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            first = free_loopback_port()
            with listener(project) as (_, second):
                start, end = sorted((first, second))
                self._write(iso, project, range_record(start, end=end))
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertIn(str(second), json.dumps(body))
                self.assertEqual(self._stored(iso, project)["state"], "active")

    def test_released_occupied_status_and_start_are_truthful(self) -> None:
        from portskill.cli import observed_range_status
        from portskill.server import build_view, dispatch_ui_action, render_page

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (_, port):
                item = range_record(port, state="released")
                self._write(iso, project, item)
                raw = json.loads(iso.registry_path.read_text(encoding="utf-8"))
                observed = observed_range_status(raw, item)
                self.assertEqual(observed["allocation_state"], "released")
                self.assertEqual(observed["state"], "occupied")
                self.assertTrue(observed["reachable"])
                page = render_page(build_view(raw))
                self.assertIn("occupied", page)

                before = iso.registry_path.read_text(encoding="utf-8")
                code, body = dispatch_ui_action({
                    "action": "start", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertIn("occupied", json.dumps(body).lower())
                self.assertNotIn("recovery", body)
                self.assertEqual(iso.registry_path.read_text(encoding="utf-8"), before)

    def test_stale_pid_does_not_hide_reachable_listener(self) -> None:
        from portskill.cli import observed_range_status

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (_, port):
                item = range_record(port, pid=99999999)
                raw = {"projects": {str(project): {"ranges": [item]}}}
                observed = observed_range_status(raw, item)
                self.assertEqual(observed["state"], "active")
                self.assertTrue(observed["reachable"])
                self.assertFalse(observed["process_alive"])

    def test_ready_stop_hook_can_stop_untracked_listener(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            scripts = project / ".portskill"
            scripts.mkdir()
            with listener(project) as (proc, port):
                (project / "listener.pid").write_text(str(proc.pid), encoding="utf-8")
                stop_script = scripts / "stop.sh"
                stop_script.write_text(
                    "#!/bin/sh\nkill \"$(cat listener.pid)\"\n", encoding="utf-8"
                )
                stop_script.chmod(0o755)
                self._write(iso, project, range_record(port))
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertEqual(code, 0, body)
                proc.wait(timeout=2)
                self.assertFalse(can_connect(port))
                self.assertEqual(self._stored(iso, project)["state"], "released")

    def test_failed_stop_hook_with_listener_preserves_allocation(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            scripts = project / ".portskill"
            scripts.mkdir()
            stop_script = scripts / "stop.sh"
            stop_script.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            stop_script.chmod(0o755)
            with listener(project) as (_, port):
                self._write(iso, project, range_record(port))
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertIn("7", json.dumps(body))
                self.assertTrue(can_connect(port))
                self.assertEqual(self._stored(iso, project)["state"], "active")

    def test_remote_stop_does_not_probe_or_act_on_local_listener(self) -> None:
        from portskill.server import dispatch_ui_action

        with IsolatedConfig() as iso:
            project = (iso.root / "service").resolve()
            project.mkdir()
            with listener(project) as (proc, port):
                item = range_record(port, machine_id="other-mac")
                machines = {
                    "other-mac": {"id": "other-mac", "kind": "remote", "host": "other.test"},
                }
                self._write(iso, project, item, machines=machines)
                code, body = dispatch_ui_action({
                    "action": "stop", "project": str(project), "rangeId": "service",
                })
                self.assertNotEqual(code, 0, body)
                self.assertIn("remote", json.dumps(body).lower())
                self.assertIsNone(proc.poll())
                self.assertEqual(self._stored(iso, project)["state"], "active")


if __name__ == "__main__":
    unittest.main()
