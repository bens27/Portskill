"""Optional sharing failures must not prevent the local app from launching."""
import json
import subprocess
import sys
import time
import unittest
import urllib.request

from tests.helpers import IsolatedConfig, ROOT


class StartupTailscaleTests(unittest.TestCase):
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
