"""Real HTTP checks for Host validation, without DNS or user-config access."""
from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from port_registry_app.server import Handler, http_host_name, trusted_http_host
from tests.helpers import IsolatedConfig


class HttpHostValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = IsolatedConfig()
        self.config.__enter__()
        self.addCleanup(self.config.__exit__, None, None, None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, hosts, *, method="GET", path="/api/state", origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            for host in hosts:
                connection.putheader("Host", host)
            body = b'{"action":"test"}' if method == "POST" else b""
            if body:
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(len(body)))
            if origin:
                connection.putheader("Origin", origin)
                connection.putheader("Sec-Fetch-Site", "same-origin")
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_rebinding_host_cannot_read_inventory_or_dispatch_action(self) -> None:
        evil = "attacker.example:8765"
        with patch("port_registry_app.server.dispatch_ui_action") as dispatch:
            for method, path in (("GET", "/api/state"), ("GET", "/health"), ("POST", "/port-registry/actions"), ("POST", "/mcp")):
                with self.subTest(method=method, path=path):
                    status, body = self.request([evil], method=method, path=path, origin="http://" + evil)
                    self.assertEqual(status, 403)
                    self.assertEqual(body["error"], "host_forbidden")
            dispatch.assert_not_called()

    def test_localhost_and_literal_loopback_preserve_get_and_post(self) -> None:
        for host in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST.:8765", "[::1]:8765"):
            with self.subTest(host=host):
                self.assertEqual(self.request([host])[0], 200)
                with patch("port_registry_app.server.dispatch_ui_action", return_value=(0, {"ok": True})) as dispatch:
                    status, body = self.request([host], method="POST", path="/port-registry/actions", origin="http://" + host)
                    self.assertEqual(status, 200, body)
                    dispatch.assert_called_once_with({"action": "test"})

    def test_missing_duplicate_and_malformed_hosts_are_rejected(self) -> None:
        for hosts in ([], ["localhost", "attacker.example"], ["localhost", "localhost"], ["localhost:bad"], ["localhost:99999"], ["127.attacker.example"], ["localhost@attacker.example"], ["[::1]attacker.example"]):
            with self.subTest(hosts=hosts):
                self.assertEqual(self.request(hosts)[0], 403)

    def test_explicit_bind_hostname_is_allowed_but_not_other_names(self) -> None:
        self.httpd.portskill_bind_host = "devbox.internal"
        self.assertEqual(self.request(["devbox.internal:8765"])[0], 200)
        self.assertEqual(self.request(["other.internal:8765"])[0], 403)
        self.httpd.portskill_bind_host = "0.0.0.0"
        # An explicitly broad listener accepts its own interface and OS hostname.
        self.httpd.server_name = "own-machine.internal"
        self.assertEqual(self.request(["127.0.0.1:8765"])[0], 200)
        self.assertEqual(self.request(["own-machine.internal:8765"])[0], 200)
        self.assertEqual(self.request(["other.internal:8765"])[0], 403)

    def test_tailscale_requires_opt_in_and_exact_self_dns_name(self) -> None:
        own = "devbox.example-tailnet.ts.net"
        status = {"Self": {"DNSName": own + "."}, "Peer": {"one": {"DNSName": "peer.example-tailnet.ts.net."}}}
        with patch("port_registry_app.server.probe_tailscale_status", return_value=status) as probe:
            self.assertEqual(self.request([own])[0], 403)
            probe.assert_not_called()
            self.config.registry_path.write_text(json.dumps({"settings": {"serve_portskill_on_tailscale": True}}))
            self.assertEqual(self.request([own])[0], 200)
            self.assertEqual(self.request([own.upper() + ":443"])[0], 200)
            self.assertEqual(self.request(["peer.example-tailnet.ts.net"])[0], 403)
            self.assertEqual(self.request(["attacker.other-tailnet.ts.net"])[0], 403)
            probe.assert_called_once()  # Local status is cached, not probed per asset.
            self.config.registry_path.write_text(json.dumps({"settings": {"serve_portskill_on_tailscale": False}}))
            self.assertEqual(self.request([own])[0], 403)

    def test_tailscale_probe_failure_does_not_allow_unknown_hosts(self) -> None:
        self.config.registry_path.write_text(json.dumps({"settings": {"serve_portskill_on_tailscale": True}}))
        with patch("port_registry_app.server.probe_tailscale_status", side_effect=OSError("unavailable")):
            self.assertEqual(self.request(["devbox.example-tailnet.ts.net"])[0], 403)
            self.assertEqual(self.request(["127.0.0.1"])[0], 200)


class HostParsingTests(unittest.TestCase):
    def test_ipv6_and_loopback_checks_do_not_accept_dns_prefixes(self) -> None:
        self.assertEqual(http_host_name("[0:0:0:0:0:0:0:1]:8765"), "::1")
        self.assertTrue(trusted_http_host("127.5.6.7", set()))
        self.assertFalse(trusted_http_host("127.attacker.example", set()))
        self.assertFalse(trusted_http_host("localhost.attacker.example", set()))
        self.assertTrue(trusted_http_host("192.168.1.2", {"192.168.1.2"}))
        for authority in ("localhost:0", "http://localhost", "localhost/path", "localhost?x", "localhost#x", "localhost:", "localhost\\evil", "local host"):
            with self.subTest(authority=authority):
                self.assertIsNone(http_host_name(authority))


if __name__ == "__main__":
    unittest.main()
