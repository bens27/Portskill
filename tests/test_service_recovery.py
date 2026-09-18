"""Exercise stopped-service recovery and observed listener status."""
import socket
import unittest

from tests.helpers import IsolatedConfig, free_loopback_port, parse_cli_json


class ServiceRecoveryTests(unittest.TestCase):
    def test_released_start_offers_working_recovery(self):
        from port_registry_app.server import dispatch_ui_action
        with IsolatedConfig() as iso:
            project = (iso.root / 'service').resolve()
            project.mkdir()
            port = free_loopback_port()
            first = iso.run_cli(['path', '--mode', 'start', '--project', str(project),
                                 '--start', str(port), '--command', 'sleep 60'])
            self.assertEqual(first.returncode, 0, first.stdout)
            rid = parse_cli_json(first)['result']['range']['id']
            try:
                code, _ = dispatch_ui_action({'action': 'stop', 'project': str(project), 'rangeId': rid})
                self.assertEqual(code, 0)
                code, error = dispatch_ui_action({'action': 'start', 'project': str(project), 'rangeId': rid})
                self.assertNotEqual(code, 0)
                self.assertIn('recovery', error)
                code, result = dispatch_ui_action(error['recovery']['payload'])
                self.assertEqual(code, 0, result)
                restored = result['result']['range']
                self.assertEqual(restored['command'], 'sleep 60')
                self.assertEqual(restored['start'], port)
                self.assertIsNotNone(restored['lifecycle']['pid'])
            finally:
                iso.run_cli(['stop', '--project', str(project), '--range-id', rid])

    def test_reclaim_refuses_occupied_ports_without_mutating(self):
        from port_registry_app.server import dispatch_ui_action
        with IsolatedConfig() as iso, socket.socket() as listener:
            project = (iso.root / 'service').resolve()
            project.mkdir()
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            iso.write_registry({'projects': {str(project): {'ranges': [{
                'id': 'old', 'start': port, 'end': port, 'state': 'released',
                'command': 'sleep 60', 'tailnet': {'mode': 'none'},
            }]}}})
            before = iso.registry_path.read_text()
            code, result = dispatch_ui_action({'action': 'reclaim-start', 'project': str(project), 'rangeId': 'old'})
            self.assertNotEqual(code, 0)
            self.assertEqual(result.get('result', {}).get('reason'), 'port_in_use')
            self.assertEqual(iso.registry_path.read_text(), before)

    def test_sharing_https_does_not_leak_into_local_fallback_link(self):
        from port_registry_app.cli import resolve_range_url
        from port_registry_app.server import build_view
        item = {'id': 'roster', 'start': 20003, 'end': 20003,
                'state': 'active', 'scheme': 'https',
                'tailnet': {'mode': 'serve'}}
        raw = {'projects': {'/tmp/book-port': {'ranges': [item]}}}
        self.assertEqual(resolve_range_url(raw, item, allow_probe=False),
                         'http://127.0.0.1:20003/')
        self.assertEqual(build_view(raw)['projects'][0]['ranges'][0]['url'],
                         'http://127.0.0.1:20003/')
        self.assertEqual(resolve_range_url(raw, item,
                         tailscale_self={'DNSName': 'test.example.ts.net'}, allow_probe=False),
                         'https://test.example.ts.net:20003/')
        direct_tls = {**item, 'tailnet': {'mode': 'none'}}
        self.assertEqual(resolve_range_url(raw, direct_tls, allow_probe=False),
                         'https://127.0.0.1:20003/')

    def test_active_tracks_listener_without_changing_saved_allocation(self):
        from port_registry_app.cli import enrich_range_for_status
        from port_registry_app.server import build_view
        with IsolatedConfig() as iso, socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            item = {'id': 'service', 'start': port, 'end': port, 'state': 'active', 'tailnet': {'mode': 'none'}}
            raw = {'projects': {'/tmp/service': {'ranges': [item]}}}
            self.assertEqual(enrich_range_for_status(raw, item)['state'], 'inactive')
            self.assertEqual(build_view(raw)['stats']['activeRanges'], 0)
            listener.listen()
            self.assertEqual(enrich_range_for_status(raw, item)['state'], 'active')
            view = build_view(raw)
            self.assertEqual(view['stats']['activeRanges'], 1)
            listener.close()
            self.assertEqual(build_view(raw)['projects'][0]['ranges'][0]['state'], 'inactive')
            self.assertEqual(item['state'], 'active')
