"""Opt-in WebAuthn/passkey store + verify for the personal HTTP listen path.

Credentials and the gate flag live under ~/.config/port-registry/ (not registry.json).
Default gate is OFF. No PyPI dependency — ES256/RS256 verify is stdlib-only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import secrets
import threading
import time
from typing import Any

from . import cbor_lite
from . import p256
from .cli import registry_path, utc_now

HTTP_PASSKEY_FILENAME = "http_passkey.json"
HTTP_SESSIONS_FILENAME = "http_sessions.json"
HTTP_SESSION_COOKIE_NAME = "portskill_session"
SESSION_TTL_SEC = 12 * 60 * 60
CHALLENGE_TTL_SEC = 5 * 60
RP_NAME = "Portskill"
OPERATOR_NAME = "operator"
OPERATOR_DISPLAY = "Portskill operator"

_CHALLENGES: dict[str, dict[str, Any]] = {}
_CHALLENGE_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()


def passkey_path() -> pathlib.Path:
    configured = os.environ.get("PORTSKILL_HTTP_PASSKEY_PATH")
    if configured and str(configured).strip():
        return pathlib.Path(configured).expanduser()
    return registry_path().parent / HTTP_PASSKEY_FILENAME


def sessions_path() -> pathlib.Path:
    configured = os.environ.get("PORTSKILL_HTTP_SESSIONS_PATH")
    if configured and str(configured).strip():
        return pathlib.Path(configured).expanduser()
    return registry_path().parent / HTTP_SESSIONS_FILENAME


def _atomic_secret_json(path: pathlib.Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(raw, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return payload


def _read_json(path: pathlib.Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def b64url_encode(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    import base64

    if not isinstance(text, str) or not text:
        raise ValueError("expected base64url string")
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _empty_passkey_doc() -> dict:
    return {
        "version": 1,
        "gate": False,
        "user_id": b64url_encode(secrets.token_bytes(16)),
        "credentials": [],
        "updated_at": utc_now(),
    }


def read_passkey_doc() -> dict:
    data = _read_json(passkey_path())
    if not data:
        return _empty_passkey_doc()
    creds = data.get("credentials")
    if not isinstance(creds, list):
        creds = []
    clean = []
    for item in creds:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id"):
            clean.append(item)
    user_id = data.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        user_id = b64url_encode(secrets.token_bytes(16))
    return {
        "version": 1,
        "gate": bool(data.get("gate")),
        "user_id": user_id.strip(),
        "credentials": clean,
        "updated_at": data.get("updated_at") or utc_now(),
    }


def write_passkey_doc(doc: dict) -> dict:
    payload = {
        "version": 1,
        "gate": bool(doc.get("gate")),
        "user_id": doc.get("user_id") or b64url_encode(secrets.token_bytes(16)),
        "credentials": list(doc.get("credentials") or []),
        "updated_at": utc_now(),
    }
    with _FILE_LOCK:
        return _atomic_secret_json(passkey_path(), payload)


def http_passkey_gate_enabled() -> bool:
    """Default OFF when the operator file is absent."""
    data = _read_json(passkey_path())
    if not data:
        return False
    return bool(data.get("gate"))


def set_http_passkey_gate(enabled: bool) -> dict:
    doc = read_passkey_doc()
    doc["gate"] = bool(enabled)
    return write_passkey_doc(doc)


def list_passkey_credentials() -> list[dict]:
    out = []
    for cred in read_passkey_doc().get("credentials") or []:
        out.append({
            "id": cred.get("id"),
            "name": cred.get("name") or "Passkey",
            "alg": cred.get("alg") or "ES256",
            "created_at": cred.get("created_at"),
            "sign_count": int(cred.get("sign_count") or 0),
        })
    return out


def passkey_public_status() -> dict:
    doc = read_passkey_doc()
    creds = list_passkey_credentials()
    return {
        "gate": bool(doc.get("gate")),
        "enabled": bool(doc.get("gate")),
        "path": str(passkey_path()),
        "count": len(creds),
        "credentials": creds,
    }


def delete_passkey_credential(cred_id: str) -> bool:
    if not isinstance(cred_id, str) or not cred_id.strip():
        return False
    want = cred_id.strip()
    doc = read_passkey_doc()
    before = list(doc.get("credentials") or [])
    after = [c for c in before if c.get("id") != want]
    if len(after) == len(before):
        return False
    doc["credentials"] = after
    write_passkey_doc(doc)
    return True


def _parse_host(host_header: str) -> tuple[str, str]:
    raw = (host_header or "").strip() or "127.0.0.1"
    if raw.startswith("["):
        end = raw.find("]")
        hostname = raw[1:end] if end > 0 else raw.strip("[]")
    elif raw.count(":") == 1:
        hostname = raw.rsplit(":", 1)[0]
    else:
        hostname = raw
    return raw, hostname


def origin_and_rpid(host_header: str, *, forwarded_proto: str | None = None) -> tuple[str, str]:
    host, hostname = _parse_host(host_header)
    proto = "http"
    if isinstance(forwarded_proto, str) and forwarded_proto.strip().lower() == "https":
        proto = "https"
    origin = f"{proto}://{host}"
    return origin, hostname


def _challenge_key(challenge_b64: str) -> str:
    return hashlib.sha256(challenge_b64.encode("utf-8")).hexdigest()


def mint_challenge(purpose: str, *, rp_id: str, origin: str) -> dict:
    raw = secrets.token_bytes(32)
    challenge = b64url_encode(raw)
    rec = {
        "challenge": challenge,
        "purpose": purpose,
        "rp_id": rp_id,
        "origin": origin,
        "expires_at": time.time() + CHALLENGE_TTL_SEC,
    }
    with _CHALLENGE_LOCK:
        _purge_challenges_locked()
        _CHALLENGES[_challenge_key(challenge)] = rec
    return rec


def consume_challenge(challenge_b64: str, purpose: str, *, rp_id: str, origin: str) -> bool:
    if not isinstance(challenge_b64, str) or not challenge_b64:
        return False
    key = _challenge_key(challenge_b64)
    with _CHALLENGE_LOCK:
        _purge_challenges_locked()
        rec = _CHALLENGES.pop(key, None)
    if not rec:
        return False
    if rec.get("purpose") != purpose:
        return False
    if rec.get("rp_id") != rp_id or rec.get("origin") != origin:
        return False
    return True


def _purge_challenges_locked() -> None:
    now = time.time()
    dead = [k for k, v in _CHALLENGES.items() if float(v.get("expires_at") or 0) <= now]
    for key in dead:
        _CHALLENGES.pop(key, None)


def registration_options(host_header: str) -> dict:
    origin, rp_id = origin_and_rpid(host_header)
    doc = read_passkey_doc()
    rec = mint_challenge("register", rp_id=rp_id, origin=origin)
    exclude = []
    for cred in doc.get("credentials") or []:
        cid = cred.get("id")
        if isinstance(cid, str) and cid:
            exclude.append({"type": "public-key", "id": cid})
    return {
        "ok": True,
        "rp": {"name": RP_NAME, "id": rp_id},
        "user": {
            "id": doc.get("user_id"),
            "name": OPERATOR_NAME,
            "displayName": OPERATOR_DISPLAY,
        },
        "challenge": rec["challenge"],
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},
            {"type": "public-key", "alg": -257},
        ],
        "timeout": 60000,
        "attestation": "none",
        "excludeCredentials": exclude,
        "authenticatorSelection": {
            "residentKey": "preferred",
            "userVerification": "preferred",
        },
        "origin": origin,
        "rpId": rp_id,
    }


def authentication_options(host_header: str) -> dict:
    origin, rp_id = origin_and_rpid(host_header)
    rec = mint_challenge("authenticate", rp_id=rp_id, origin=origin)
    allow = []
    for cred in list_passkey_credentials():
        cid = cred.get("id")
        if isinstance(cid, str) and cid:
            allow.append({"type": "public-key", "id": cid})
    return {
        "ok": True,
        "challenge": rec["challenge"],
        "timeout": 60000,
        "rpId": rp_id,
        "userVerification": "preferred",
        "allowCredentials": allow,
        "origin": origin,
    }


def _parse_client_data(client_data_b64: str) -> dict:
    raw = b64url_decode(client_data_b64)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("clientDataJSON must be an object")
    return data


def _verify_client_data(data: dict, *, expect_type: str, challenge: str, origin: str) -> None:
    if data.get("type") != expect_type:
        raise ValueError("unexpected clientData type")
    got_ch = data.get("challenge")
    if not isinstance(got_ch, str) or got_ch != challenge:
        raise ValueError("challenge mismatch")
    got_origin = data.get("origin")
    if not isinstance(got_origin, str) or got_origin != origin:
        raise ValueError("origin mismatch")


def _parse_auth_data(auth_data: bytes) -> dict:
    if len(auth_data) < 37:
        raise ValueError("authenticatorData too short")
    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    sign_count = int.from_bytes(auth_data[33:37], "big")
    rest = auth_data[37:]
    cred_id = None
    cose_key = None
    if flags & 0x40:  # AT
        if len(rest) < 18:
            raise ValueError("attested credential data truncated")
        cred_len = int.from_bytes(rest[16:18], "big")
        cred_id = rest[18 : 18 + cred_len]
        if len(cred_id) != cred_len:
            raise ValueError("credential id truncated")
        cose_raw = rest[18 + cred_len :]
        cose_key = cbor_lite.loads(cose_raw)
    return {
        "rp_id_hash": rp_id_hash,
        "flags": flags,
        "sign_count": sign_count,
        "credential_id": cred_id,
        "cose_key": cose_key,
        "raw": auth_data,
    }


def _cose_to_stored(cose_key: dict) -> dict:
    if not isinstance(cose_key, dict):
        raise ValueError("COSE key must be a map")
    kty = cose_key.get(1)
    alg = cose_key.get(3)
    if kty == 2 and alg in (-7, None):
        if cose_key.get(-1) not in (1, None):
            raise ValueError("unsupported EC curve")
        x = cose_key.get(-2)
        y = cose_key.get(-3)
        if not isinstance(x, (bytes, bytearray)) or not isinstance(y, (bytes, bytearray)):
            raise ValueError("EC point missing")
        p256.decode_raw_public(bytes(x), bytes(y))
        return {
            "alg": "ES256",
            "kty": "EC",
            "crv": "P-256",
            "x": b64url_encode(bytes(x)),
            "y": b64url_encode(bytes(y)),
        }
    if kty == 3 and alg in (-257, None):
        n = cose_key.get(-1)
        e = cose_key.get(-2)
        if not isinstance(n, (bytes, bytearray)) or not isinstance(e, (bytes, bytearray)):
            raise ValueError("RSA key missing")
        if len(n) < 256:
            raise ValueError("RSA modulus too small")
        return {
            "alg": "RS256",
            "kty": "RSA",
            "n": b64url_encode(bytes(n)),
            "e": b64url_encode(bytes(e)),
        }
    raise ValueError("unsupported COSE key (need ES256 or RS256)")


def _der_ints(sig: bytes) -> tuple[int, int]:
    if len(sig) < 8 or sig[0] != 0x30:
        raise ValueError("signature is not DER SEQUENCE")
    seq_len = sig[1]
    if seq_len & 0x80:
        raise ValueError("unsupported DER length")
    if 2 + seq_len != len(sig):
        raise ValueError("DER length mismatch")
    if sig[2] != 0x02:
        raise ValueError("DER r is not INTEGER")
    r_len = sig[3]
    r_bytes = sig[4 : 4 + r_len]
    if len(r_bytes) != r_len or r_len == 0:
        raise ValueError("DER r truncated")
    s_off = 4 + r_len
    if s_off + 2 > len(sig) or sig[s_off] != 0x02:
        raise ValueError("DER s is not INTEGER")
    s_len = sig[s_off + 1]
    s_bytes = sig[s_off + 2 : s_off + 2 + s_len]
    if len(s_bytes) != s_len or s_len == 0:
        raise ValueError("DER s truncated")
    if s_off + 2 + s_len != len(sig):
        raise ValueError("DER trailing bytes")
    if len(r_bytes) > 1 and r_bytes[0] == 0x00 and r_bytes[1] < 0x80:
        raise ValueError("non-minimal DER r")
    if len(s_bytes) > 1 and s_bytes[0] == 0x00 and s_bytes[1] < 0x80:
        raise ValueError("non-minimal DER s")
    return int.from_bytes(r_bytes, "big"), int.from_bytes(s_bytes, "big")


def encode_der_signature(r: int, s: int) -> bytes:
    def _int(n: int) -> bytes:
        raw = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
        if raw[0] & 0x80:
            raw = b"\x00" + raw
        return b"\x02" + bytes([len(raw)]) + raw

    body = _int(r) + _int(s)
    return b"\x30" + bytes([len(body)]) + body


def _verify_es256(cred: dict, signed: bytes, signature: bytes) -> bool:
    x = b64url_decode(cred["x"])
    y = b64url_decode(cred["y"])
    pub = p256.decode_raw_public(x, y)
    r, s = _der_ints(signature)
    return p256.verify(pub, signed, r, s)


def _verify_rs256(cred: dict, signed: bytes, signature: bytes) -> bool:
    n = int.from_bytes(b64url_decode(cred["n"]), "big")
    e = int.from_bytes(b64url_decode(cred["e"]), "big")
    if e < 3 or n.bit_length() < 2048:
        return False
    if len(signature) != (n.bit_length() + 7) // 8:
        return False
    k = (n.bit_length() + 7) // 8
    m = pow(int.from_bytes(signature, "big"), e, n)
    em = m.to_bytes(k, "big")
    digest = hashlib.sha256(signed).digest()
    digest_info = (
        b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20"
        + digest
    )
    if len(em) < len(digest_info) + 11:
        return False
    if not em.startswith(b"\x00\x01"):
        return False
    rest = em[2:]
    sep = rest.find(b"\x00")
    if sep < 8:
        return False
    if rest[:sep] != b"\xff" * sep:
        return False
    return rest[sep + 1 :] == digest_info


def verify_stored_signature(cred: dict, authenticator_data: bytes, client_data_json: bytes, signature: bytes) -> bool:
    signed = authenticator_data + hashlib.sha256(client_data_json).digest()
    alg = cred.get("alg") or "ES256"
    try:
        if alg == "ES256":
            return _verify_es256(cred, signed, signature)
        if alg == "RS256":
            return _verify_rs256(cred, signed, signature)
    except (ValueError, KeyError):
        return False
    return False


def complete_registration(body: dict, host_header: str, *, name: str | None = None) -> dict:
    origin, rp_id = origin_and_rpid(host_header)
    if not isinstance(body, dict):
        raise ValueError("expected JSON object")
    client_b64 = body.get("clientDataJSON")
    att_b64 = body.get("attestationObject")
    raw_id = body.get("id") or body.get("rawId")
    if not isinstance(client_b64, str) or not isinstance(att_b64, str):
        raise ValueError("clientDataJSON and attestationObject required")
    client_raw = b64url_decode(client_b64)
    client = json.loads(client_raw.decode("utf-8"))
    if not isinstance(client, dict):
        raise ValueError("clientDataJSON must be an object")
    challenge = client.get("challenge")
    if not isinstance(challenge, str) or not consume_challenge(
        challenge, "register", rp_id=rp_id, origin=origin
    ):
        raise ValueError("registration challenge invalid or expired")
    _verify_client_data(client, expect_type="webauthn.create", challenge=challenge, origin=origin)
    att = cbor_lite.loads(b64url_decode(att_b64))
    if not isinstance(att, dict):
        raise ValueError("attestationObject must be a map")
    auth_raw = att.get("authData")
    if not isinstance(auth_raw, (bytes, bytearray)):
        raise ValueError("authData missing")
    parsed = _parse_auth_data(bytes(auth_raw))
    if parsed["rp_id_hash"] != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise ValueError("rpIdHash mismatch")
    if not (parsed["flags"] & 0x01):
        raise ValueError("user-present flag required")
    if parsed["credential_id"] is None or parsed["cose_key"] is None:
        raise ValueError("attested credential data required")
    cred_id = b64url_encode(parsed["credential_id"])
    if isinstance(raw_id, str) and raw_id and raw_id != cred_id:
        # Browser id is base64url of the same credential id
        try:
            if b64url_encode(b64url_decode(raw_id)) != cred_id:
                raise ValueError("credential id mismatch")
        except ValueError as exc:
            raise ValueError("credential id mismatch") from exc
    stored = _cose_to_stored(parsed["cose_key"])
    label = name.strip() if isinstance(name, str) and name.strip() else "Passkey"
    if isinstance(body.get("name"), str) and body.get("name").strip():
        label = body["name"].strip()
    record = {
        "id": cred_id,
        "name": label[:80],
        "created_at": utc_now(),
        "sign_count": parsed["sign_count"],
        **stored,
    }
    doc = read_passkey_doc()
    existing = [c for c in (doc.get("credentials") or []) if c.get("id") != cred_id]
    existing.append(record)
    doc["credentials"] = existing
    write_passkey_doc(doc)
    return {"ok": True, "id": cred_id, "name": record["name"], **passkey_public_status()}


def complete_authentication(body: dict, host_header: str) -> dict:
    origin, rp_id = origin_and_rpid(host_header)
    if not isinstance(body, dict):
        raise ValueError("expected JSON object")
    client_b64 = body.get("clientDataJSON")
    auth_b64 = body.get("authenticatorData")
    sig_b64 = body.get("signature")
    cred_id = body.get("id") or body.get("rawId")
    if not all(isinstance(x, str) and x for x in (client_b64, auth_b64, sig_b64, cred_id)):
        raise ValueError("id, clientDataJSON, authenticatorData, signature required")
    client_raw = b64url_decode(client_b64)
    client = json.loads(client_raw.decode("utf-8"))
    if not isinstance(client, dict):
        raise ValueError("clientDataJSON must be an object")
    challenge = client.get("challenge")
    if not isinstance(challenge, str) or not consume_challenge(
        challenge, "authenticate", rp_id=rp_id, origin=origin
    ):
        raise ValueError("authentication challenge invalid or expired")
    _verify_client_data(client, expect_type="webauthn.get", challenge=challenge, origin=origin)
    auth_raw = b64url_decode(auth_b64)
    parsed = _parse_auth_data(auth_raw)
    if parsed["rp_id_hash"] != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise ValueError("rpIdHash mismatch")
    if not (parsed["flags"] & 0x01):
        raise ValueError("user-present flag required")
    try:
        want = b64url_encode(b64url_decode(cred_id))
    except ValueError as exc:
        raise ValueError("invalid credential id") from exc
    doc = read_passkey_doc()
    match = None
    for cred in doc.get("credentials") or []:
        if cred.get("id") == want:
            match = cred
            break
    if match is None:
        raise ValueError("unknown credential")
    if not verify_stored_signature(match, auth_raw, client_raw, b64url_decode(sig_b64)):
        raise ValueError("assertion signature invalid")
    prev = int(match.get("sign_count") or 0)
    new = parsed["sign_count"]
    if prev > 0 and new > 0 and new <= prev:
        raise ValueError("sign count did not advance")
    match["sign_count"] = new
    write_passkey_doc(doc)
    session = mint_http_session()
    return {"ok": True, "session": session, **passkey_public_status()}


def _read_sessions() -> dict:
    data = _read_json(sessions_path())
    if not data:
        return {"version": 1, "sessions": {}}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    return {"version": 1, "sessions": sessions}


def _write_sessions(doc: dict) -> dict:
    now = time.time()
    clean = {}
    for token, rec in (doc.get("sessions") or {}).items():
        if not isinstance(token, str) or not token or not isinstance(rec, dict):
            continue
        exp = rec.get("expires_at")
        try:
            if float(exp) <= now:
                continue
        except (TypeError, ValueError):
            continue
        clean[token] = rec
    payload = {"version": 1, "sessions": clean, "updated_at": utc_now()}
    with _FILE_LOCK:
        return _atomic_secret_json(sessions_path(), payload)


def mint_http_session(*, ttl_sec: int = SESSION_TTL_SEC) -> dict:
    token = secrets.token_urlsafe(32)
    now = time.time()
    rec = {
        "created_at": utc_now(),
        "expires_at": now + int(ttl_sec),
    }
    doc = _read_sessions()
    sessions = dict(doc.get("sessions") or {})
    sessions[token] = rec
    doc["sessions"] = sessions
    _write_sessions(doc)
    return {"token": token, "expires_at": rec["expires_at"], "ttl_sec": int(ttl_sec)}


def http_session_valid(token: str | None) -> bool:
    if not isinstance(token, str) or not token:
        return False
    doc = _read_sessions()
    rec = (doc.get("sessions") or {}).get(token)
    if not isinstance(rec, dict):
        return False
    try:
        return float(rec.get("expires_at")) > time.time()
    except (TypeError, ValueError):
        return False


def revoke_http_session(token: str | None) -> None:
    if not isinstance(token, str) or not token:
        return
    doc = _read_sessions()
    sessions = dict(doc.get("sessions") or {})
    if token in sessions:
        sessions.pop(token, None)
        doc["sessions"] = sessions
        _write_sessions(doc)


def session_cookie_header(token: str, *, ttl_sec: int = SESSION_TTL_SEC, clear: bool = False) -> tuple[str, str]:
    if clear:
        return (
            "Set-Cookie",
            f"{HTTP_SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )
    return (
        "Set-Cookie",
        (
            f"{HTTP_SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={int(ttl_sec)}"
        ),
    )


def clear_bearer_cookie_header() -> tuple[str, str]:
    from .cli import HTTP_AUTH_COOKIE_NAME

    return ("Set-Cookie", f"{HTTP_AUTH_COOKIE_NAME}=; Path=/; SameSite=Lax; Max-Age=0")


def hmac_token_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )
