"""Regression coverage for preserving configured registry pool bounds."""
from __future__ import annotations

import unittest
from unittest import mock

from tests.helpers import IsolatedConfig


class RegistryCustomPoolTests(unittest.TestCase):
    def test_server_view_and_fallback_port_keep_stored_pool(self) -> None:
        from portskill import server

        with IsolatedConfig() as iso:
            iso.write_registry({"pool": {"start": 31000, "end": 31999}})

            registry = server.load_registry()
            self.assertEqual(registry["pool"], {"start": 31000, "end": 31999})
            self.assertEqual(
                server.build_view(registry)["pool"],
                {"start": 31000, "end": 31999},
            )
            with mock.patch("portskill.server.port_bindable", return_value=True):
                self.assertEqual(server._find_os_free_pool_port("127.0.0.1"), 31000)

    def test_explicit_mutation_bounds_override_stored_pool(self) -> None:
        from portskill.registry import mutate_registry

        with IsolatedConfig() as iso:
            iso.write_registry({"pool": {"start": 31000, "end": 31999}})

            pool = mutate_registry(
                lambda registry: (registry["pool"], False, None),
                start=22000,
                end=22999,
            )

            self.assertEqual(pool, {"start": 22000, "end": 22999})


if __name__ == "__main__":
    unittest.main()
