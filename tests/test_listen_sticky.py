"""(d) port claim / listen.json sticky behavior (temp dirs, not user home)."""
from __future__ import annotations

import json
import pathlib
import unittest

from tests.helpers import IsolatedConfig, free_loopback_port, hold_loopback_port

HOME_LISTEN = pathlib.Path.home() / ".config" / "port-registry" / "listen.json"


class ListenStickyTests(unittest.TestCase):
    def _home_snapshot(self) -> str | None:
        if not HOME_LISTEN.is_file():
            return None
        return HOME_LISTEN.read_text(encoding="utf-8")

    def test_listen_path_follows_registry_dir(self) -> None:
        from port_registry_app.cli import listen_path
        from port_registry_app.server import listen_path as server_listen_path

        with IsolatedConfig() as iso:
            self.assertEqual(listen_path(), iso.listen_path)
            self.assertEqual(server_listen_path(), iso.listen_path)
            self.assertTrue(str(listen_path()).startswith(str(iso.root)))

    def test_write_read_listen_stays_in_temp(self) -> None:
        from port_registry_app.server import read_listen_file, write_listen_file

        before = self._home_snapshot()
        with IsolatedConfig() as iso:
            write_listen_file({
                "version": 1,
                "listening": True,
                "host": "127.0.0.1",
                "port": 20042,
            })
            self.assertTrue(iso.listen_path.is_file())
            data = read_listen_file()
            self.assertIsNotNone(data)
            self.assertEqual(data.get("port"), 20042)
            self.assertEqual(self._home_snapshot(), before)
            # Path must not be the user's live listen.json
            self.assertNotEqual(iso.listen_path.resolve(), HOME_LISTEN.resolve())

    def test_select_listen_port_prefers_sticky(self) -> None:
        from port_registry_app.server import select_listen_port, write_listen_file

        before = self._home_snapshot()
        port = free_loopback_port()
        with IsolatedConfig() as iso:
            iso.write_registry()
            write_listen_file({
                "version": 1,
                "listening": False,
                "host": "127.0.0.1",
                "port": port,
            })
            chosen, source = select_listen_port("127.0.0.1")
            self.assertEqual(chosen, port)
            self.assertEqual(source, "sticky")
            self.assertEqual(self._home_snapshot(), before)
            self.assertTrue(iso.listen_path.is_file())

    def test_select_listen_port_uses_portskill_claim_when_no_sticky(self) -> None:
        from port_registry_app.server import PORTSKILL_NOTE, _portskill_project_path, select_listen_port

        before = self._home_snapshot()
        port = free_loopback_port()
        with IsolatedConfig() as iso:
            iso.write_registry({
                "projects": {
                    _portskill_project_path(): {
                        "ranges": [{
                            "id": "ps-claim-1",
                            "start": port,
                            "end": port,
                            "state": "reserved",
                            "note": PORTSKILL_NOTE,
                        }],
                    }
                }
            })
            chosen, source = select_listen_port("127.0.0.1")
            self.assertEqual(chosen, port)
            self.assertEqual(source, "claim")
            self.assertEqual(self._home_snapshot(), before)

    def test_busy_sticky_falls_through_to_claim_without_rewriting_home(self) -> None:
        from port_registry_app.server import (
            PORTSKILL_NOTE,
            _portskill_project_path,
            select_listen_port,
            write_listen_file,
        )

        before = self._home_snapshot()
        holder, busy = hold_loopback_port()
        claim_port = free_loopback_port()
        try:
            with IsolatedConfig() as iso:
                iso.write_registry({
                    "projects": {
                        _portskill_project_path(): {
                            "ranges": [{
                                "id": "ps-claim-busy",
                                "start": claim_port,
                                "end": claim_port,
                                "state": "reserved",
                                "note": PORTSKILL_NOTE,
                            }],
                        }
                    }
                })
                write_listen_file({
                    "version": 1,
                    "listening": False,
                    "host": "127.0.0.1",
                    "port": busy,
                })
                chosen, source = select_listen_port("127.0.0.1")
                self.assertNotEqual(chosen, busy)
                self.assertEqual(chosen, claim_port)
                self.assertEqual(source, "claim")
                self.assertEqual(self._home_snapshot(), before)
                self.assertTrue(iso.registry_path.is_file())
                raw = json.loads(iso.registry_path.read_text(encoding="utf-8"))
                self.assertIsInstance(raw, dict)
        finally:
            holder.close()


if __name__ == "__main__":
    unittest.main()
