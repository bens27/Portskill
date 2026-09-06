"""HTTP auth plumbing (optional) + Funnel-of-listen refuse."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

from tests.helpers import IsolatedConfig, free_loopback_port, parse_cli_json


def _start_handler(host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, int]:
    from port_registry_app.server import Handler

    port = free_loopback_port()
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def _http_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    cookie: str | None = None,
    body=None,
    timeout: float = 5,
):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw else None
            return int(getattr(resp, "status", None) or resp.getcode()), payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", "replace")}
        return int(exc.code), payload


class HttpAuthTokenTests(unittest.TestCase):
    def test_mint_show_regenerate_and_doctor_hides_secret(self) -> None:
        from port_registry_app.cli import (
            ensure_http_auth_token,
            http_auth_public_status,
            http_auth_token,
            http_bearer_matches,
        )

        with IsolatedConfig() as iso:
            first, minted = ensure_http_auth_token()
            self.assertTrue(minted)
            token = first.get("token")
            self.assertTrue(isinstance(token, str) and len(token) >= 32)
            self.assertTrue(http_bearer_matches(token))
            self.assertFalse(http_bearer_matches("nope"))
            self.assertFalse(http_bearer_matches(""))
            again, minted_again = ensure_http_auth_token()
            self.assertFalse(minted_again)
            self.assertEqual(again.get("token"), token)

            show = iso.run_cli(["http-auth", "show"])
            self.assertEqual(show.returncode, 0, show.stderr)
            shown = parse_cli_json(show)
            self.assertEqual(shown.get("token"), token)
            self.assertTrue(shown.get("configured"))
            self.assertIn("http_auth.json", shown.get("path") or "")

            regen = iso.run_cli(["http-auth", "regenerate"])
            self.assertEqual(regen.returncode, 0, regen.stderr)
            new = parse_cli_json(regen)
            self.assertTrue(new.get("regenerated"))
            self.assertNotEqual(new.get("token"), token)
            self.assertTrue(http_bearer_matches(new.get("token")))
            self.assertFalse(http_bearer_matches(token))
            self.assertEqual(http_auth_token(), new.get("token"))

            public = http_auth_public_status()
            self.assertTrue(public.get("configured"))
            self.assertNotIn("token", public)
            blob = json.dumps(public)
            self.assertNotIn(new.get("token"), blob)

            doctor = iso.run_cli(["doctor"])
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            payload = parse_cli_json(doctor)
            self.assertNotIn(new.get("token"), doctor.stdout)
            self.assertNotIn(new.get("token"), json.dumps(payload))
            names = {c.get("name"): c for c in payload.get("checks") or [] if isinstance(c, dict)}
            self.assertIn("http_auth", names)
            self.assertTrue(names["http_auth"].get("ok"))
            self.assertIn("token present", names["http_auth"].get("detail") or "")
            self.assertNotIn(new.get("token"), names["http_auth"].get("detail") or "")
            status = payload.get("http_auth") or {}
            self.assertTrue(status.get("configured"))
            self.assertNotIn("token", status)


class HttpAuthGateTests(unittest.TestCase):
    def test_personal_listen_ui_and_apis_open_without_bearer(self) -> None:
        from port_registry_app.cli import ensure_http_auth_token

        with IsolatedConfig() as iso:
            iso.write_registry({
                "projects": {
                    "/tmp/portskill-demo": {
                        "ranges": [{
                            "id": "r-demo",
                            "start": 20001,
                            "end": 20001,
                            "state": "reserved",
                            "note": "demo range",
                            "tailnet": {"mode": "none"},
                            "default_state": "off",
                        }],
                    }
                }
            })
            ensure_http_auth_token()
            httpd, port = _start_handler()
            try:
                mcp = f"http://127.0.0.1:{port}/mcp"
                init = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "http-auth-test", "version": "0"},
                    },
                }
                code, payload = _http_json(mcp, method="POST", body=init)
                self.assertEqual(code, 200)
                self.assertEqual((payload or {}).get("jsonrpc"), "2.0")
                self.assertIn("result", payload or {})

                code, payload = _http_json(mcp)
                self.assertEqual(code, 200)
                self.assertTrue((payload or {}).get("ok"))

                code, payload = _http_json(f"http://127.0.0.1:{port}/api/state")
                self.assertEqual(code, 200)
                self.assertIsInstance(payload, dict)

                code, payload = _http_json(f"http://127.0.0.1:{port}/health")
                self.assertEqual(code, 200)
                self.assertTrue((payload or {}).get("ok"))

                req = urllib.request.Request(f"http://127.0.0.1:{port}/")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8")
                    self.assertEqual(int(getattr(resp, "status", None) or resp.getcode()), 200)
                self.assertNotIn("This listener requires a local bearer token", html)
                self.assertIn("r-demo", html)
                self.assertIn("demo range", html)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_stdio_initialize_does_not_need_http_token(self) -> None:
        from port_registry_app.mcp import mcp_handle

        with IsolatedConfig() as iso:
            iso.write_registry()
            resp = mcp_handle({
                "jsonrpc": "2.0",
                "id": 7,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-test", "version": "0"},
                },
            })
        self.assertIsInstance(resp, dict)
        self.assertEqual(resp.get("id"), 7)
        self.assertIn("result", resp)


class PasskeyGateTests(unittest.TestCase):
    def test_gate_default_off_and_cli_toggle(self) -> None:
        from port_registry_app.webauthn import http_passkey_gate_enabled

        with IsolatedConfig() as iso:
            self.assertFalse(http_passkey_gate_enabled())
            shown = iso.run_cli(["http-auth", "gate", "show"])
            self.assertEqual(shown.returncode, 0, shown.stderr)
            payload = parse_cli_json(shown)
            self.assertFalse(payload.get("passkey_gate"))
            on = iso.run_cli(["http-auth", "gate", "on"])
            self.assertEqual(on.returncode, 0, on.stderr)
            self.assertTrue(parse_cli_json(on).get("passkey_gate"))
            self.assertTrue(http_passkey_gate_enabled())
            off = iso.run_cli(["http-auth", "gate", "off"])
            self.assertEqual(off.returncode, 0, off.stderr)
            self.assertFalse(parse_cli_json(off).get("passkey_gate"))
            self.assertFalse(http_passkey_gate_enabled())

    def test_gate_on_fails_closed_without_session_or_bearer(self) -> None:
        from port_registry_app.webauthn import set_http_passkey_gate

        with IsolatedConfig() as iso:
            iso.write_registry({
                "projects": {
                    "/tmp/portskill-demo": {
                        "ranges": [{
                            "id": "r-demo",
                            "start": 20001,
                            "end": 20001,
                            "state": "reserved",
                            "note": "demo range",
                            "tailnet": {"mode": "none"},
                            "default_state": "off",
                        }],
                    }
                }
            })
            set_http_passkey_gate(True)
            httpd, port = _start_handler()
            try:
                init = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "passkey-gate-test", "version": "0"},
                    },
                }
                code, payload = _http_json(f"http://127.0.0.1:{port}/mcp", method="POST", body=init)
                self.assertEqual(code, 401)
                self.assertEqual((payload or {}).get("error"), "unauthorized")

                code, payload = _http_json(f"http://127.0.0.1:{port}/api/state")
                self.assertEqual(code, 401)

                code, payload = _http_json(
                    f"http://127.0.0.1:{port}/port-registry/actions",
                    method="POST",
                    body={"action": "status"},
                )
                self.assertEqual(code, 401)

                code, payload = _http_json(f"http://127.0.0.1:{port}/health")
                self.assertEqual(code, 200)
                self.assertTrue((payload or {}).get("ok"))

                req = urllib.request.Request(f"http://127.0.0.1:{port}/")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8")
                    self.assertEqual(int(getattr(resp, "status", None) or resp.getcode()), 200)
                self.assertIn("Passkey required", html)
                self.assertNotIn("r-demo", html)
                self.assertNotIn("demo range", html)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_gate_on_accepts_bearer_or_session(self) -> None:
        from port_registry_app.cli import ensure_http_auth_token
        from port_registry_app.webauthn import (
            HTTP_SESSION_COOKIE_NAME,
            mint_http_session,
            set_http_passkey_gate,
        )

        with IsolatedConfig() as iso:
            iso.write_registry()
            auth, _minted = ensure_http_auth_token()
            token = auth.get("token")
            set_http_passkey_gate(True)
            session = mint_http_session()
            httpd, port = _start_handler()
            try:
                code, payload = _http_json(f"http://127.0.0.1:{port}/api/state")
                self.assertEqual(code, 401)

                code, payload = _http_json(
                    f"http://127.0.0.1:{port}/api/state",
                    token=token,
                )
                self.assertEqual(code, 200)
                self.assertIsInstance(payload, dict)

                cookie = f"{HTTP_SESSION_COOKIE_NAME}={session['token']}"
                code, payload = _http_json(
                    f"http://127.0.0.1:{port}/api/state",
                    cookie=cookie,
                )
                self.assertEqual(code, 200)
                self.assertIsInstance(payload, dict)

                init = {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "passkey-session-test", "version": "0"},
                    },
                }
                code, payload = _http_json(
                    f"http://127.0.0.1:{port}/mcp",
                    method="POST",
                    body=init,
                    cookie=cookie,
                )
                self.assertEqual(code, 200)
                self.assertIn("result", payload or {})
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_webauthn_register_and_assert_sets_session_cookie(self) -> None:
        import hashlib

        from port_registry_app import cbor_lite, p256
        from port_registry_app.webauthn import (
            HTTP_SESSION_COOKIE_NAME,
            b64url_encode,
            encode_der_signature,
            set_http_passkey_gate,
        )

        with IsolatedConfig() as iso:
            iso.write_registry()
            httpd, port = _start_handler()
            host = f"127.0.0.1:{port}"
            origin = f"http://{host}"
            rp_id = "127.0.0.1"
            try:
                code, opt = _http_json(
                    f"http://{host}/api/webauthn/register/options",
                    method="POST",
                    body={},
                )
                self.assertEqual(code, 200, opt)
                challenge = (opt or {}).get("challenge")
                self.assertTrue(challenge)
                priv, pub = p256.generate_keypair()
                cred_id = b"test-cred-001"
                x, y = pub[0].to_bytes(32, "big"), pub[1].to_bytes(32, "big")
                cose = cbor_lite.dumps({1: 2, 3: -7, -1: 1, -2: x, -3: y})
                flags = 0x41
                auth = (
                    hashlib.sha256(rp_id.encode("utf-8")).digest()
                    + bytes([flags])
                    + (0).to_bytes(4, "big")
                    + (b"\x00" * 16)
                    + len(cred_id).to_bytes(2, "big")
                    + cred_id
                    + cose
                )
                att = cbor_lite.dumps({"fmt": "none", "authData": auth, "attStmt": {}})
                client = json.dumps({
                    "type": "webauthn.create",
                    "challenge": challenge,
                    "origin": origin,
                    "crossOrigin": False,
                }, separators=(",", ":")).encode("utf-8")
                body = {
                    "id": b64url_encode(cred_id),
                    "rawId": b64url_encode(cred_id),
                    "clientDataJSON": b64url_encode(client),
                    "attestationObject": b64url_encode(att),
                    "name": "Test key",
                }
                code, payload = _http_json(
                    f"http://{host}/api/webauthn/register",
                    method="POST",
                    body=body,
                )
                self.assertEqual(code, 200, payload)
                self.assertEqual((payload or {}).get("count"), 1)

                set_http_passkey_gate(True)
                code, _closed = _http_json(f"http://{host}/api/state")
                self.assertEqual(code, 401)

                code, aopt = _http_json(
                    f"http://{host}/api/webauthn/authenticate/options",
                    method="POST",
                    body={},
                )
                self.assertEqual(code, 200, aopt)
                achallenge = (aopt or {}).get("challenge")
                aclient = json.dumps({
                    "type": "webauthn.get",
                    "challenge": achallenge,
                    "origin": origin,
                    "crossOrigin": False,
                }, separators=(",", ":")).encode("utf-8")
                aflags = 0x01
                aauth = (
                    hashlib.sha256(rp_id.encode("utf-8")).digest()
                    + bytes([aflags])
                    + (1).to_bytes(4, "big")
                )
                signed = aauth + hashlib.sha256(aclient).digest()
                r, s = p256.sign(priv, signed)
                assertion = {
                    "id": b64url_encode(cred_id),
                    "rawId": b64url_encode(cred_id),
                    "clientDataJSON": b64url_encode(aclient),
                    "authenticatorData": b64url_encode(aauth),
                    "signature": b64url_encode(encode_der_signature(r, s)),
                }
                data = json.dumps(assertion).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}/api/webauthn/authenticate",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(int(getattr(resp, "status", None) or resp.getcode()), 200)
                    set_cookie = resp.headers.get("Set-Cookie") or ""
                    self.assertIn(HTTP_SESSION_COOKIE_NAME, set_cookie)
                    self.assertIn("HttpOnly", set_cookie)
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
                cookie_token = ""
                for part in set_cookie.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == HTTP_SESSION_COOKIE_NAME:
                        cookie_token = value
                self.assertTrue(cookie_token)
                code, state = _http_json(
                    f"http://{host}/api/state",
                    cookie=f"{HTTP_SESSION_COOKIE_NAME}={cookie_token}",
                )
                self.assertEqual(code, 200)
                self.assertIsInstance(state, dict)
            finally:
                httpd.shutdown()
                httpd.server_close()


class FunnelListenRefuseTests(unittest.TestCase):
    def test_funnel_of_listen_port_refused_serve_allowed(self) -> None:
        from port_registry_app import cli

        with IsolatedConfig() as iso:
            iso.write_listen({
                "version": 1,
                "listening": True,
                "host": "127.0.0.1",
                "port": 20050,
                "ui_url": "http://127.0.0.1:20050/",
                "mcp_url": "http://127.0.0.1:20050/mcp",
            })
            buf = StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as ctx:
                    cli.run_tailnet("funnel", 20050, off=False)
            self.assertEqual(ctx.exception.code, 2)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("reason"), "funnel_listen_refused")
            self.assertIn("listen", (payload.get("message") or "").lower())

            with patch("port_registry_app.cli.subprocess.run") as run:
                run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                cli.run_tailnet("serve", 20050, off=False)
                self.assertTrue(run.called)
                cli.run_tailnet("funnel", 21000, off=False)
                self.assertEqual(run.call_count, 2)
                cli.run_tailnet("funnel", 20050, off=True)
                self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
