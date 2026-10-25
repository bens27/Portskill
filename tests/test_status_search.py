"""status --all covers every project; status --find searches ranges by note,
name or a port inside a range. Both are read-only and add no bind tests."""
from __future__ import annotations

import json
import os
import pathlib
import unittest
from unittest import mock

from tests.helpers import IsolatedConfig, parse_cli_json


class StatusAllAndSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iso = IsolatedConfig().__enter__()
        self.addCleanup(self.iso.__exit__, None, None, None)
        self.web = self._project("web-shop")
        self.api = self._project("billing-api")
        self.docs = self._project("docs-site")
        self.tailscale = mock.patch(
            "portskill.status.probe_tailscale_status", return_value={"Self": None}
        )
        self.tailscale.start()
        self.addCleanup(self.tailscale.stop)
        # Saved "reserved": never drift-checked, so the tests touch no port.
        self.iso.write_registry({
            "projects": {
                self.web: {"ranges": [self._range("r-web", 20100, note="Storefront dev server")]},
                self.api: {"ranges": [
                    self._range("r-api", 20200, end=20209, note="Billing API + worker"),
                    self._range("r-admin", 20300, note="Admin console"),
                ]},
                self.docs: {"ranges": [self._range("r-docs", 20400, note=None)]},
            }
        })
        from portskill.status import status_result

        status_result(None)  # settle the helper's non-canonical file
        self.before = self.iso.registry_path.read_bytes()

    def tearDown(self) -> None:
        self.assertEqual(
            self.iso.registry_path.read_bytes(), self.before,
            "status --all / --find must not write the registry",
        )

    def _project(self, name: str) -> str:
        path = (self.iso.root / name).resolve()
        path.mkdir()
        return str(path)

    @staticmethod
    def _range(range_id: str, start: int, *, end: int | None = None, note: str | None) -> dict:
        from portskill.registry_operations import range_record

        return range_record(range_id, start, end or start, note, "none", default_state="off")

    def _add_range(self, project: str, item: dict) -> None:
        """Add a saved range, re-settle, and re-baseline the bytes check."""
        from portskill.status import status_result

        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        data["projects"].setdefault(project, {"ranges": []})["ranges"].append(item)
        self.iso.registry_path.write_text(json.dumps(data), encoding="utf-8")
        status_result(None)
        self.before = self.iso.registry_path.read_bytes()

    def _status(self, **kwargs) -> dict:
        from portskill.status import status_result

        return status_result(kwargs.pop("project", None), **kwargs)

    def _ids(self, result: dict) -> list[str]:
        return sorted(item["id"] for item in result["ranges"])

    # -- --all ---------------------------------------------------------------

    def test_all_ignores_directory_context_and_covers_every_project(self) -> None:
        context = pathlib.Path(self.web)
        (context / ".portskill.json").write_text(
            json.dumps({"project_path": self.web}), encoding="utf-8"
        )
        cwd = os.getcwd()
        os.chdir(context)
        self.addCleanup(os.chdir, cwd)

        scoped = self._status()
        everything = self._status(all_projects=True)

        self.assertEqual(self._ids(scoped), ["r-web"])
        self.assertEqual(self._ids(everything), ["r-admin", "r-api", "r-docs", "r-web"])
        self.assertEqual(sorted(everything["projects"]), sorted([self.web, self.api, self.docs]))
        self.assertNotIn("query", everything)

    def test_all_with_project_is_rejected(self) -> None:
        from portskill.domain import DomainError

        with self.assertRaises(DomainError) as caught:
            self._status(project=self.web, all_projects=True)
        self.assertEqual(caught.exception.reason, "invalid_args")

    # -- --find --------------------------------------------------------------

    def test_find_by_note_is_case_insensitive_substring(self) -> None:
        result = self._status(find="  STOREFRONT ")
        self.assertEqual(self._ids(result), ["r-web"])
        self.assertEqual(result["query"], {"text": "STOREFRONT", "kind": "text"})
        self.assertEqual(result["matches"], 1)
        [item] = result["ranges"]
        self.assertEqual(item["matched"], ["note"])
        self.assertEqual(item["project"], self.web)
        self.assertEqual(list(result["projects"]), [self.web])
        # Matches are full status entries (observation included).
        self.assertIn("allocation_state", item)

    def test_find_by_project_name_and_range_id(self) -> None:
        by_project = self._status(find="billing")
        self.assertEqual(self._ids(by_project), ["r-admin", "r-api"])
        matched = {item["id"]: item["matched"] for item in by_project["ranges"]}
        self.assertEqual(matched, {"r-api": ["note", "name"], "r-admin": ["name"]})

        by_id = self._status(find="r-docs")
        self.assertEqual(self._ids(by_id), ["r-docs"])
        self.assertEqual(by_id["ranges"][0]["matched"], ["name"])

    def test_find_port_matches_any_port_inside_a_range(self) -> None:
        for port, expected in ((20200, ["r-api"]), (20205, ["r-api"]), (20209, ["r-api"]),
                               (20400, ["r-docs"]), (20210, [])):
            with self.subTest(port=port):
                result = self._status(find=str(port))
                self.assertEqual(self._ids(result), expected)
                self.assertEqual(result["query"]["kind"], "port")
                for item in result["ranges"]:
                    self.assertEqual(item["matched"], ["port"])

    def test_find_without_match_returns_an_empty_result(self) -> None:
        result = self._status(find="no-such-service")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["matches"], 0)
        self.assertEqual(result["ranges"], [])
        self.assertEqual(result["projects"], {})
        self.assertNotIn("drift", result)

    def test_find_scoped_to_explicit_project(self) -> None:
        self.assertEqual(self._ids(self._status(find="console", project=self.api)), ["r-admin"])
        self.assertEqual(self._ids(self._status(find="console", project=self.web)), [])

    def test_find_rejects_blank_query(self) -> None:
        from portskill.domain import DomainError

        for bad in ("", "   "):
            with self.subTest(find=bad), self.assertRaises(DomainError) as caught:
                self._status(find=bad)
            self.assertEqual(caught.exception.reason, "invalid_args")

    def test_out_of_range_digits_are_a_text_search(self) -> None:
        self._add_range(self.docs, self._range("r-old", 20500, note="Build 99999 archive"))
        for text, expected in (("0", []), ("70000", []), ("99999", ["r-old"])):
            with self.subTest(find=text):
                result = self._status(find=text)
                self.assertEqual(result["query"], {"text": text, "kind": "text"})
                self.assertEqual(self._ids(result), expected)

    def test_huge_digit_query_is_a_text_search_and_never_raises(self) -> None:
        # int() refuses strings past sys.get_int_max_str_digits() (4300).
        huge = "9" * 5000
        result = self._status(find=huge)
        self.assertEqual(result["query"], {"text": huge, "kind": "text"})
        self.assertEqual(result["matches"], 0)
        padded = self._status(find="0" * 5000 + "20100")
        self.assertEqual(padded["query"]["kind"], "port")
        self.assertEqual(self._ids(padded), ["r-web"])
        cli = self.iso.run_cli(["status", "--find", huge])
        self.assertEqual(cli.returncode, 0, cli.stderr[-500:])
        self.assertNotIn("Traceback", cli.stderr)

    def test_non_ascii_digits_search_as_text_and_never_raise(self) -> None:
        # str.isdigit() accepts these; int() rejects "²" and reads the others
        # as ASCII ports (3500, 8080).
        self._add_range(self.docs, self._range("r-sq", 3500, note="Area m²"))
        self._add_range(self.docs, self._range("r-wide", 8080, note="Proxy"))
        for text, expected in (("²", ["r-sq"]), ("٣٥٠٠", []), ("８０８０", [])):
            with self.subTest(find=text):
                result = self._status(find=text)
                self.assertEqual(result["query"]["kind"], "text")
                self.assertEqual(self._ids(result), expected)
        cli = self.iso.run_cli(["status", "--find", "²"])
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertNotIn("Traceback", cli.stderr)

    def test_digit_query_also_matches_note_and_project_name(self) -> None:
        self._add_range(self.docs, self._range("r-2024", 20600, note="Release 2024 preview"))
        site = self._project("site2024")
        self._add_range(site, self._range("r-site", 20700, note=None))

        result = self._status(find="2024")
        self.assertEqual(result["query"]["kind"], "port")
        matched = {item["id"]: item["matched"] for item in result["ranges"]}
        # "r-2024" also contains the digits: range ids are not searched by digits.
        self.assertEqual(matched, {"r-2024": ["note"], "r-site": ["name"]})

    def test_port_and_note_both_listed_when_both_match(self) -> None:
        self._add_range(self.docs, self._range("r-both", 20800, end=20810, note="Mock on 20805"))
        result = self._status(find="20805")
        self.assertEqual(self._ids(result), ["r-both"])
        self.assertEqual(result["ranges"][0]["matched"], ["note", "port"])

    def test_digit_query_ignores_timestamp_range_ids(self) -> None:
        from portskill.registry_operations import next_range_id

        range_id = next_range_id({"ranges": []})
        self._add_range(self.docs, self._range(range_id, 20900, note="Timestamped"))
        year = range_id[1:5]
        self.assertEqual(self._ids(self._status(find=year)), [])
        self.assertEqual(self._ids(self._status(find=f"r{year}")), [range_id])

    def test_find_only_observes_matching_ranges(self) -> None:
        from portskill import status

        with mock.patch.object(status, "observed_range_status", wraps=status.observed_range_status) as observed:
            self._status(find="20100")
        self.assertEqual([call.args[1]["id"] for call in observed.call_args_list], ["r-web"])

    def test_live_matches_are_never_bind_tested(self) -> None:
        # e1adc118: search reuses status' observation, so a live range only
        # gets a loopback connect, never a bind test.
        data = json.loads(self.iso.registry_path.read_text(encoding="utf-8"))
        item = data["projects"][self.web]["ranges"][0]
        item["state"] = "active"
        item["lifecycle"]["pid"] = os.getpid()
        self.iso.registry_path.write_text(json.dumps(data), encoding="utf-8")
        from portskill.status import status_result

        status_result(None)
        self.before = self.iso.registry_path.read_bytes()

        with mock.patch("portskill.drift.port_held") as port_held:
            result = self._status(find="storefront")
        port_held.assert_not_called()
        self.assertEqual(self._ids(result), ["r-web"])

    # -- adapters ------------------------------------------------------------

    def test_cli_flags(self) -> None:
        everything = parse_cli_json(self.iso.run_cli(["status", "--all"]))
        self.assertEqual(self._ids(everything), ["r-admin", "r-api", "r-docs", "r-web"])

        found = self.iso.run_cli(["status", "--find", "20203"])
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertEqual(self._ids(parse_cli_json(found)), ["r-api"])

        clash = self.iso.run_cli(["status", "--all", "--project", self.web])
        self.assertNotEqual(clash.returncode, 0)
        self.assertIn("invalid_args", clash.stdout + clash.stderr)

    def test_mcp_status_tool_accepts_all_and_find(self) -> None:
        from portskill import mcp

        response = mcp._call_tool("status", {"all": True})
        self.assertFalse(response["isError"])
        self.assertEqual(self._ids(response["structuredContent"]), ["r-admin", "r-api", "r-docs", "r-web"])

        response = mcp._call_tool("status", {"find": "admin"})
        self.assertEqual(self._ids(response["structuredContent"]), ["r-admin"])

        from portskill.mcp_catalog import TOOL_DEFS

        [tool] = [tool for tool in TOOL_DEFS if tool["name"] == "status"]
        properties = tool["inputSchema"]["properties"]
        self.assertEqual(properties["all"]["type"], "boolean")
        self.assertEqual(properties["find"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
