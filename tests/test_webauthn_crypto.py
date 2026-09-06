"""Stdlib P-256 / CBOR vectors for the opt-in WebAuthn verifier."""
from __future__ import annotations

import unittest

from tests.helpers import IsolatedConfig


class P256VectorTests(unittest.TestCase):
    def test_rfc6979_p256_sha256_sample(self) -> None:
        from port_registry_app import p256

        # RFC 6979 A.2.5 — NIST P-256, SHA-256, message "sample"
        d = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
        ux = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
        uy = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299
        k = 0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60
        r = 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
        s = 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
        pub = (ux, uy)
        got_r, got_s = p256.sign(d, b"sample", k=k)
        self.assertEqual(got_r, r)
        self.assertEqual(got_s, s)
        self.assertTrue(p256.verify(pub, b"sample", r, s))
        self.assertFalse(p256.verify(pub, b"other", r, s))

    def test_roundtrip_keygen(self) -> None:
        from port_registry_app import p256

        d, pub = p256.generate_keypair()
        r, s = p256.sign(d, b"portskill")
        self.assertTrue(p256.verify(pub, b"portskill", r, s))
        self.assertFalse(p256.verify(pub, b"nope", r, s))


class CborLiteTests(unittest.TestCase):
    def test_map_bytes_roundtrip(self) -> None:
        from port_registry_app import cbor_lite

        value = {"fmt": "none", "authData": b"\x00\x01\x02", 1: 2, 3: -7}
        raw = cbor_lite.dumps(value)
        self.assertEqual(cbor_lite.loads(raw), value)


class PasskeyStoreIsolationTests(unittest.TestCase):
    def test_credentials_stay_out_of_registry_json(self) -> None:
        from port_registry_app.webauthn import (
            passkey_path,
            set_http_passkey_gate,
            write_passkey_doc,
            read_passkey_doc,
        )

        with IsolatedConfig() as iso:
            iso.write_registry()
            set_http_passkey_gate(True)
            doc = read_passkey_doc()
            doc["credentials"] = [{
                "id": "abc",
                "name": "isolated",
                "alg": "ES256",
                "kty": "EC",
                "crv": "P-256",
                "x": "x",
                "y": "y",
                "sign_count": 0,
            }]
            write_passkey_doc(doc)
            registry = iso.registry_path.read_text(encoding="utf-8")
            self.assertNotIn("isolated", registry)
            self.assertNotIn("abc", registry)
            self.assertTrue(passkey_path().is_file())
            self.assertIn("http_passkey.json", str(passkey_path()))


if __name__ == "__main__":
    unittest.main()
