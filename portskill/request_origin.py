"""Parse an HTTP Host authority once, for both the trust check and the passkey rpId."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

DEFAULT_PORTS = {"http": 80, "https": 443}


def _split_host(authority: str | None) -> tuple[str, int | None] | None:
    """(browser hostname, port) for a single Host value, or None when it is unsafe.

    The hostname is what a browser's URL parser serializes: lowercase,
    canonical IP form, trailing DNS dot kept. Never resolves DNS.
    """
    if not isinstance(authority, str) or not authority:
        return None
    if any(c.isspace() or c in "/\\?#@%" for c in authority):
        return None
    if authority.startswith("[") and not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", authority):
        return None
    try:
        parsed = urlparse("//" + authority)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host or authority.endswith(":") or port == 0:
        return None
    host = host.lower()
    try:
        return str(ipaddress.ip_address(host)), port
    except ValueError:
        if ":" in host or not re.fullmatch(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.?", host):
            return None
        return host, port


def parse_host(authority: str | None) -> tuple[str, int | None] | None:
    """(canonical hostname, port) for trust checks: as _split_host, without the trailing dot."""
    split = _split_host(authority)
    return (split[0].rstrip("."), split[1]) if split else None


def http_host_name(authority: str | None) -> str | None:
    parsed = parse_host(authority)
    return parsed[0] if parsed else None


def origin_and_rpid(host_header: str, *, forwarded_proto: str | None = None) -> tuple[str, str]:
    """WebAuthn origin and rpId exactly as the browser sees this Host.

    Kept apart from the trust hostname: browsers keep a trailing dot in both
    (new URL('http://localhost.:1').origin === 'http://localhost.:1').
    Raises ValueError when the Host is unsafe.
    """
    split = _split_host(host_header)
    if split is None:
        raise ValueError("untrusted Host header")
    host, port = split
    proto = "https" if isinstance(forwarded_proto, str) and forwarded_proto.strip().lower() == "https" else "http"
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != DEFAULT_PORTS[proto]:
        authority += f":{port}"
    return f"{proto}://{authority}", host
