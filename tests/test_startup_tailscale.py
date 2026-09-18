"""Optional sharing failures must not prevent the local app from launching."""
import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

from tests.helpers import IsolatedConfig, ROOT


class StartupTailscaleTests(unittest.TestCase):
    def test_http_server_bind_and_serving_do_not_require_reverse_dns(self):
        from port_registry_app import server as server_mod

        with IsolatedConfig():
            httpd = None
            thread = None
            with mock.patch.object(
                server_mod.socket,
                "getfqdn",
                side_effect=RuntimeError("reverse dns unavailable"),
            ):
                httpd = server_mod.PortskillThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    server_mod.Handler,
                )
                httpd.portskill_bind_host = "127.0.0.1"
                host, port = httpd.server_address[:2]
                self.assertEqual(host, "127.0.0.1")
                self.assertIsInstance(port, int)
                self.assertGreater(port, 0)
                self.assertEqual(httpd.server_name, "127.0.0.1")
                self.assertEqual(httpd.server_port, port)

                thread = threading.Thread(target=httpd.serve_forever)
                thread.start()
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health",
                        timeout=2,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(
                            json.loads(response.read().decode("utf-8")),
                            {"ok": True, "service": "portskill"},
                        )
                finally:
                    httpd.shutdown()
                    thread.join(timeout=5)
                    httpd.server_close()

    def test_local_ui_and_mcp_survive_unavailable_tailscale(self):
        for mode in ('missing', 'stopped'):
            with self.subTest(mode=mode), IsolatedConfig() as iso:
                iso.write_registry({'settings': {'serve_portskill_on_tailscale': True}})
                extra = {}
                if mode == 'stopped':
                    binary = iso.root / 'tailscale'
                    binary.write_text('#!/bin/sh\necho "Tailscale is stopped." >&2\nexit 1\n')
                    binary.chmod(0o755)
                    extra['PORT_REGISTRY_TAILSCALE_BIN'] = str(binary)
                with (iso.root / 'server.log').open('w+') as log:
                    process = subprocess.Popen(
                        [sys.executable, '-m', 'port_registry_app', '--no-open',
                         '--no-auto-apply'],
                        cwd=ROOT, env=iso.subprocess_env(extra), stdout=log, stderr=log,
                    )
                    try:
                        ready = False
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline and process.poll() is None:
                            try:
                                listen = json.loads(iso.listen_path.read_text())
                                for key in ('ui_url', 'mcp_url'):
                                    with urllib.request.urlopen(listen[key], timeout=0.3) as response:
                                        self.assertEqual(response.status, 200)
                                ready = True
                                break
                            except (OSError, ValueError):
                                time.sleep(0.05)
                        log.flush()
                        log.seek(0)
                        self.assertTrue(ready, log.read())
                        registry = json.loads(iso.registry_path.read_text())
                        self.assertTrue(registry['settings']['serve_portskill_on_tailscale'])
                    finally:
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=5)
