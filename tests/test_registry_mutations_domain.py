"""Callable allocate/activate/release domain contracts."""
from __future__ import annotations

import concurrent.futures
import argparse
import io
import json
import os
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, parse_cli_json


class RegistryMutationDomainTests(unittest.TestCase):
    def _stdio_call_then_ping(self, name, arguments):
        from portskill import mcp

        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": name, "arguments": arguments,
        }}
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        stdin = io.StringIO(json.dumps(call) + "\n" + json.dumps(ping) + "\n")
        stdout = io.StringIO()
        with mock.patch.object(mcp.sys, "stdin", stdin), mock.patch.object(mcp.sys, "stdout", stdout):
            exit_code = mcp.mcp_stdio_loop()
        return exit_code, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_cli_allocate_activate_release_and_error_contracts(self) -> None:
        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            allocated = iso.run_cli([
                "allocate", "--count", "1", "--project", str(project),
                "--tailnet", "none", "--start", "21001",
            ])
            item = parse_cli_json(allocated)["range"]
            activated = iso.run_cli([
                "activate", "--project", str(project), "--range-id", item["id"],
                "--tailnet", "none",
            ])
            released = iso.run_cli([
                "release", "--project", str(project), "--range-id", item["id"],
            ])
            invalid = iso.run_cli([
                "allocate", "--count", "1", "--project", str(project),
                "--tailnet", "none", "--scheme", "ftp",
            ])

        self.assertEqual(allocated.returncode, 0, allocated.stderr)
        self.assertEqual(parse_cli_json(allocated)["range"]["start"], 21001)
        self.assertEqual(activated.returncode, 0, activated.stderr)
        self.assertEqual(parse_cli_json(activated)["range"]["state"], "active")
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(parse_cli_json(released)["range"]["state"], "released")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(
            invalid.stdout,
            '{"message":"scheme must be http or https","reason":"invalid_scheme",'
            '"status":"error"}\n',
        )

    def test_mcp_mutations_do_not_spawn_cli(self) -> None:
        from portskill import mcp

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            allocated = mcp.call_tool("allocate", {
                "count": 1, "project": str(project), "tailnet": "none", "start": 21002,
            })
            range_id = allocated["structuredContent"]["range"]["id"]
            activated = mcp.call_tool("activate", {
                "project": str(project), "range_id": range_id, "tailnet": "none",
            })
            released = mcp.call_tool("release", {
                "project": str(project), "range_id": range_id,
            })

        self.assertFalse(allocated["isError"])
        self.assertFalse(activated["isError"])
        self.assertFalse(released["isError"])

    def test_mcp_storage_error_is_contained_then_ping_succeeds(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            iso.registry_path.with_name(iso.registry_path.name + ".lock").mkdir()
            exit_code, responses = self._stdio_call_then_ping(
                "allocate", {"count": 1, "project": ".", "tailnet": "none"}
            )
        self.assertEqual(exit_code, 0)
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_mcp_expected_callback_errors_each_preserve_following_ping(self) -> None:
        from portskill.cli import range_record

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            cases = []
            iso.write_registry_raw("{broken")
            cases.append(self._stdio_call_then_ping("allocate", {
                "count": 1, "project": str(project), "tailnet": "none",
            }))
            iso.write_registry()
            cases.append(self._stdio_call_then_ping("allocate", {
                "count": 0, "project": str(project), "tailnet": "none",
            }))
            item = range_record("r1", 21003, 21003, None, "serve")
            iso.write_registry({"projects": {str(project.resolve()): {"ranges": [item]}}})
            cases.append(self._stdio_call_then_ping("activate", {
                "project": str(project), "range_id": "r1", "tailnet": "serve",
            }))

        for exit_code, responses in cases:
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(responses), 2)
            self.assertTrue(responses[0]["result"]["isError"])
            self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_direct_expected_errors_never_emit_or_raise_system_exit(self) -> None:
        from portskill.cli import range_record
        from portskill.domain import DomainError
        from portskill.registry_mutations import activate_result, allocate_result

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                iso.write_registry_raw("{broken")
                with self.assertRaises(DomainError):
                    allocate_result({"count": 1, "project": str(project), "tailnet": "none"})
                iso.write_registry()
                with self.assertRaises(DomainError):
                    allocate_result({"count": 0, "project": str(project), "tailnet": "none"})
                item = range_record("r1", 21004, 21004, None, "serve")
                iso.write_registry({"projects": {str(project.resolve()): {"ranges": [item]}}})
                with self.assertRaises(DomainError):
                    activate_result({"project": str(project), "range_id": "r1", "tailnet": "serve"})
        self.assertEqual(stdout.getvalue(), "")

    def test_server_allocate_uses_domain_path(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            code, body = server.dispatch_ui_action({
                "action": "allocate", "count": 1, "project": str(project), "tailnet": "none",
            })
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])

    def test_server_allocate_storage_error_then_valid_request(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            lock = iso.registry_path.with_name(iso.registry_path.name + ".lock")
            lock.mkdir()
            failed_code, failed = server.dispatch_ui_action({
                "action": "allocate", "count": 1, "project": str(project), "tailnet": "none",
            })
            lock.rmdir()
            valid_code, valid = server.dispatch_ui_action({
                "action": "allocate", "count": 1, "project": str(project), "tailnet": "none",
            })
        self.assertEqual(failed_code, 1)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["result"]["reason"], "registry_unavailable")
        self.assertEqual(valid_code, 0)
        self.assertTrue(valid["ok"])

    def test_concurrent_allocations_are_lock_serialized(self) -> None:
        from portskill.registry_mutations import allocate_result

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            iso.write_registry()
            options = {"count": 1, "project": str(project), "tailnet": "none"}
            barrier = __import__("threading").Barrier(2)
            def allocate(_):
                barrier.wait()
                return allocate_result(options)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(allocate, range(2)))
        starts = {result["range"]["start"] for result in results}
        self.assertEqual(len(starts), 2)

    def test_apply_defaults_continues_after_activation_domain_error(self) -> None:
        from portskill import cli

        with IsolatedConfig() as iso:
            project = iso.root / "project"
            project.mkdir()
            first = cli.range_record("r1", 21020, 21020, "serve", "serve", default_state="on")
            second = cli.range_record("r2", 21021, 21021, "none", "none", default_state="on")
            for item in (first, second):
                item["lifecycle"]["pid"] = os.getpid()
            iso.write_registry({"projects": {str(project.resolve()): {"ranges": [first, second]}}})
            payload = cli.run_apply_defaults(record_session=False)
            registry = json.loads(iso.registry_path.read_text())

        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["started_count"], 1)
        persisted = registry["projects"][str(project.resolve())]["ranges"]
        self.assertEqual(persisted[0]["state"], "reserved")
        self.assertEqual(persisted[1]["state"], "active")

    def test_portskill_serve_disable_tolerates_domain_teardown_error(self) -> None:
        from portskill import cli
        from portskill import lifecycle as lifecycle_module
        from portskill import process_commands

        failed = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "gone"})()
        from portskill import tailscale_commands

        with mock.patch.object(tailscale_commands, "tailscale_bin_available", return_value=(True, "tailscale")), mock.patch.object(
            lifecycle_module.subprocess,
            "run", return_value=failed
        ), mock.patch.object(
            tailscale_commands, "probe_tailscale_status", return_value={"Self": None}
        ):
            payload = cli.apply_portskill_tailscale_serve(
                False, persist=False, port=21003
            )

        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["enabled"])

    def test_post_launch_activation_failure_keeps_lifecycle_metadata(self) -> None:
        from portskill import cli
        from portskill import lifecycle as lifecycle_module
        from portskill import process_commands
        from portskill.domain import DomainError
        from portskill.service import ServiceLaunchOutcome

        item = cli.range_record("r1", 21030, 21030, None, "serve")
        def launched(target, _project):
            target["lifecycle"].update({"pid": 4321, "pgid": 4321, "started_at": "now"})
            return ServiceLaunchOutcome(True, source="command", pid=4321, pgid=4321, started_at="now")
        with mock.patch.object(process_commands, "launch_service", side_effect=launched), mock.patch.object(
            lifecycle_module,
            "ensure_tailscale_authenticated",
            side_effect=DomainError("tailscale_bin_missing", "missing"),
        ):
            ok, detail = cli.start_item_for_apply(item, "/tmp/project")
        self.assertFalse(ok)
        self.assertIs(detail["range"], item)
        self.assertEqual(item["lifecycle"]["pid"], 4321)
        self.assertEqual(item["lifecycle"]["started_at"], "now")

    def test_exit_house_continues_for_reserved_and_release_policies(self) -> None:
        from portskill import cli
        from portskill import lifecycle as lifecycle_module
        from portskill.domain import DomainError

        # Both policies tear the mapping down through run_tailnet first.
        for also_release in (False, True):
            with self.subTest(also_release=also_release), IsolatedConfig() as iso:
                project = str((iso.root / "project").resolve())
                (iso.root / "project").mkdir()
                ranges = []
                for index in range(2):
                    item = cli.range_record(f"r{index}", 21040 + index, 21040 + index, None, "serve", default_state="on")
                    item["state"] = "active"
                    item["tailnet"]["configured_at"] = "configured"
                    ranges.append(item)
                iso.write_registry({"projects": {project: {"ranges": ranges}}})
                with mock.patch.object(
                    lifecycle_module, "run_tailnet", side_effect=[DomainError("tailnet_command_failed", "failed"), None]
                ):
                    payload = cli.run_exit_house(also_release=also_release, clear_session=False)
                self.assertEqual(payload["error_count"], 1)
                self.assertEqual(payload["stopped_count"], 1)

    def test_discover_import_batch_continues_after_domain_error(self) -> None:
        from portskill import cli
        from portskill.domain import DomainError

        discovery = {
            "discoveries": [
                {"port": 21050, "missing": True},
                {"port": 21051, "missing": True},
            ],
            "missing": [{"port": 21050}, {"port": 21051}],
            "serve": {"ports": {}},
        }
        second = {"status": "ok", "port": 21051}
        args = argparse.Namespace(
            tailnet="none", note=None, project=None, all_missing=True, port=None
        )
        from portskill import port_discovery

        with mock.patch.object(
            port_discovery, "locked_registry", side_effect=[discovery, DomainError("port_in_use", "busy"), second]
        ):
            payload = cli.cmd_discover_import(args)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["imported"], [second])
        self.assertEqual(payload["errors"][0]["reason"], "port_in_use")


if __name__ == "__main__":
    unittest.main()
