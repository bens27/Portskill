"""HTTP bearer-token persistence shared by CLI, doctor, and server."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from .domain import utc_now
from .registry import atomic_write_json, http_auth_path


def mint_http_auth_secret():
    return secrets.token_urlsafe(32)


def read_http_auth_file():
    """Return http_auth.json object or None. Read-only; never creates a file."""
    path = http_auth_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    return data


def http_auth_token():
    data = read_http_auth_file()
    if not data:
        return None
    token = data.get("token")
    return token.strip() if isinstance(token, str) else None


def http_auth_configured():
    return bool(http_auth_token())


def http_auth_public_status():
    """Doctor/UI-safe: path + configured. Never includes the secret."""
    from .webauthn import passkey_public_status

    passkeys = passkey_public_status()
    return {
        "configured": http_auth_configured(),
        "present": http_auth_configured(),
        "path": str(http_auth_path()),
        "passkey_gate": bool(passkeys.get("gate")),
        "passkey_credentials": int(passkeys.get("count") or 0),
        "passkey_path": passkeys.get("path"),
    }


def write_http_auth_file(token, *, created_at=None):
    path = http_auth_path()
    now = utc_now()
    payload = {
        "version": 1,
        "token": token,
        "created_at": created_at or now,
        "updated_at": now,
    }
    atomic_write_json(path, payload, mode=0o600)
    return payload


def ensure_http_auth_token():
    """Load existing token or mint one. Returns (payload, minted)."""
    existing = read_http_auth_file()
    if existing:
        return existing, False
    return write_http_auth_file(mint_http_auth_secret()), True


def regenerate_http_auth_token():
    """Mint a new token; the previous value is invalidated."""
    previous = read_http_auth_file()
    created_at = previous.get("created_at") if previous else None
    return write_http_auth_file(mint_http_auth_secret(), created_at=created_at)


def http_bearer_matches(provided):
    expected = http_auth_token()
    if not expected or not isinstance(provided, str) or not provided:
        return False
    received = provided.strip()
    if not received:
        return False
    return hmac.compare_digest(
        hashlib.sha256(received.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )
