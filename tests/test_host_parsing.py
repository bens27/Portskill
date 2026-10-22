"""One table of tricky Host values for the trust check and the passkey rpId (ticket 2cab65e3).

Before request_origin.py, webauthn parsed Host on its own and disagreed with the
trust check on uppercase and expanded IPv6 (both trusted), so a passkey could be
bound to an rpId the browser never uses. The WebAuthn origin/rpId follow the
browser's URL serialization (lowercase, canonical IP, trailing dot KEPT, default
port omitted); the trust check uses the same parse with the dot stripped.
"""
import base64
import json
import types
import unittest
from unittest import mock

from portskill import server
from portskill.request_origin import origin_and_rpid
from portskill.server import http_host_name, own_tailscale_http_host
from portskill.webauthn import origin_and_rpid as webauthn_origin_and_rpid
from tests.helpers import IsolatedConfig

# (Host header, trust-check hostname, expected WebAuthn (origin, rpId)) ; None = refused
CASES = [
    ("127.0.0.1", "127.0.0.1", ("http://127.0.0.1", "127.0.0.1")),
    ("127.0.0.1:20013", "127.0.0.1", ("http://127.0.0.1:20013", "127.0.0.1")),
    ("127.0.0.1:80", "127.0.0.1", ("http://127.0.0.1", "127.0.0.1")),
    ("localhost:20013", "localhost", ("http://localhost:20013", "localhost")),
    ("LOCALHOST:20013", "localhost", ("http://localhost:20013", "localhost")),
    ("localhost.:20013", "localhost", ("http://localhost.:20013", "localhost.")),
    ("[::1]:20013", "::1", ("http://[::1]:20013", "::1")),
    ("[::1]", "::1", ("http://[::1]", "::1")),
    ("[0:0:0:0:0:0:0:1]:20013", "::1", ("http://[::1]:20013", "::1")),
    ("mac.tail1234.ts.net", "mac.tail1234.ts.net", ("http://mac.tail1234.ts.net", "mac.tail1234.ts.net")),
    ("Mac.Tail1234.TS.net.", "mac.tail1234.ts.net", ("http://mac.tail1234.ts.net.", "mac.tail1234.ts.net.")),
    ("localhost..", None, None),
    ("::1", None, None),
    ("", None, None),
    ("   ", None, None),
    ("localhost:", None, None),
    ("localhost:0", None, None),
    ("localhost:abc", None, None),
    ("a:b:c", None, None),
    ("[::1", None, None),
    ("evil.com@localhost", None, None),
    ("localhost/evil", None, None),
]


class HostParserTableTests(unittest.TestCase):
    def test_trust_check_and_passkey_agree(self) -> None:
        self.assertIs(webauthn_origin_and_rpid, origin_and_rpid)
        for header, hostname, webauthn in CASES:
            with self.subTest(header=header):
                self.assertEqual(http_host_name(header), hostname)
                if webauthn is None:
                    with self.assertRaises(ValueError):
                        origin_and_rpid(header)
                else:
                    self.assertEqual(origin_and_rpid(header), webauthn)

    def test_https_forwarded_proto(self) -> None:
        self.assertEqual(origin_and_rpid("Mac.ts.net:443", forwarded_proto="HTTPS"),
                         ("https://mac.ts.net", "mac.ts.net"))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ClientDataOriginTests(unittest.TestCase):
    """Through real client-data verification: what the browser sends must be accepted."""

    def _register(self, host_header: str, browser_origin: str) -> str:
        from portskill.webauthn import complete_registration, registration_options

        challenge = registration_options(host_header)["challenge"]
        client = {"type": "webauthn.create", "challenge": challenge, "origin": browser_origin}
        body = {
            "id": "x",
            "clientDataJSON": _b64url(json.dumps(client).encode("utf-8")),
            "attestationObject": _b64url(b"\xa0"),  # empty CBOR map: fails AFTER client data
        }
        with self.assertRaises(ValueError) as caught:
            complete_registration(body, host_header)
        return str(caught.exception)

    def test_browser_origins_pass_client_data_check(self) -> None:
        # browser_origin = new URL("http://" + Host).origin
        cases = [
            ("localhost.:20013", "http://localhost.:20013"),
            ("LOCALHOST:20013", "http://localhost:20013"),
            ("[0:0:0:0:0:0:0:1]:20013", "http://[::1]:20013"),
            ("Mac.Tail1234.TS.net.", "http://mac.tail1234.ts.net."),
        ]
        with IsolatedConfig() as iso:
            iso.write_registry()
            for host, origin in cases:
                with self.subTest(host=host):
                    error = self._register(host, origin)
                    self.assertNotIn("origin mismatch", error)
                    self.assertNotIn("challenge", error)

    def test_dot_stripped_origin_is_rejected(self) -> None:
        with IsolatedConfig() as iso:
            iso.write_registry()
            self.assertEqual(self._register("localhost.:20013", "http://localhost:20013"), "origin mismatch")


class OwnHostCacheTests(unittest.TestCase):
    def test_registry_read_only_when_file_changes(self) -> None:
        srv = types.SimpleNamespace()
        status = {"Self": {"DNSName": "mac.tail1234.ts.net."}}
        with IsolatedConfig() as iso:
            iso.write_registry({"settings": {"serve_portskill_on_tailscale": True}})
            with mock.patch.object(server, "load_registry", wraps=server.load_registry) as load, \
                    mock.patch.object(server, "probe_tailscale_status", return_value=status):
                for _ in range(5):
                    self.assertEqual(own_tailscale_http_host(srv), "mac.tail1234.ts.net")
                self.assertEqual(load.call_count, 1)
                iso.write_registry({"settings": {"serve_portskill_on_tailscale": False}})
                path = server.registry_path()
                st = path.stat()
                server.os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
                self.assertIsNone(own_tailscale_http_host(srv))
                self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
