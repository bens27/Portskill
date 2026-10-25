"""POST /portskill/actions: an unexpected exception answers 500 JSON, not a dropped connection."""
from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from tests.helpers import IsolatedConfig, free_loopback_port

SECRET = "boom-secret-detail"


class ActionsCatchAllTests(unittest.TestCase):
    def setUp(self) -> None:
        from portskill.server import Handler

        iso = IsolatedConfig()
        iso.__enter__()
        self.addCleanup(iso.__exit__, None, None, None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", free_loopback_port()), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        self.stderr = StringIO()
        stderr_patch = patch("sys.stderr", self.stderr)
        stderr_patch.start()
        self.addCleanup(stderr_patch.stop)

    def post(self, payload: dict) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/portskill/actions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_unexpected_exception_returns_500_json_and_server_keeps_serving(self) -> None:
        with patch("portskill.server.dispatch_ui_action", side_effect=RuntimeError(SECRET)):
            status, raw = self.post({"action": "status"})
        self.assertEqual(status, 500)
        body = json.loads(raw.decode("utf-8"))
        self.assertIs(body["ok"], False)
        self.assertIsInstance(body["message"], str)
        self.assertTrue(body["message"])
        text = raw.decode("utf-8")
        self.assertNotIn("Traceback", text)
        self.assertNotIn(SECRET, text)
        # The traceback is logged server-side.
        log = self.stderr.getvalue()
        self.assertIn("Traceback", log)
        self.assertIn(SECRET, log)

        # The same server still answers the next request normally.
        with patch("portskill.server.dispatch_ui_action", return_value=(0, {"ok": True, "n": 1})):
            self.assertEqual(self.post({"action": "status"}), (200, b'{"n":1,"ok":true}'))

    def test_expected_errors_are_unchanged(self) -> None:
        self.assertEqual(self.post({"action": " "}), (400, b'{"message":"expected action","ok":false}'))
        with patch("portskill.server.dispatch_ui_action", return_value=(2, {"ok": False, "message": "m"})):
            self.assertEqual(self.post({"action": "x"}), (422, b'{"message":"m","ok":false}'))
        self.assertNotIn("Traceback", self.stderr.getvalue())

    def test_failure_after_response_started_writes_no_second_response(self) -> None:
        from portskill.server import Handler

        def broken_send(handler, status, body, content_type, extra_headers=None):
            handler._response_started = True
            handler.send_response(status)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            raise OSError("client went away mid-body")

        payload = json.dumps({"action": "status"}).encode("utf-8")
        request = (
            b"POST /portskill/actions HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode() + b"\r\n\r\n" + payload
        )
        with patch.object(Handler, "_send", broken_send), \
                patch("portskill.server.dispatch_ui_action", return_value=(0, {"ok": True})):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                sock.sendall(request)
                chunks = []
                while chunk := sock.recv(4096):
                    chunks.append(chunk)
        raw = b"".join(chunks)
        self.assertEqual(raw.count(b"HTTP/1.0 "), 1)
        self.assertTrue(raw.startswith(b"HTTP/1.0 200"))
        self.assertNotIn(b"500", raw)
        self.assertIn("client went away mid-body", self.stderr.getvalue())
        # Still serving afterwards.
        with patch("portskill.server.dispatch_ui_action", return_value=(0, {"ok": True})):
            self.assertEqual(self.post({"action": "status"}), (200, b'{"ok":true}'))


if __name__ == "__main__":
    unittest.main()
