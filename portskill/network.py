"""Shared listen-host safety rules for CLI, doctor, and HTTP adapters."""
from __future__ import annotations

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ALLOW_NON_LOOPBACK_FLAG = "--allow-non-loopback"


def is_loopback_host(host):
    """True for 127.0.0.1 / ::1 / localhost (and 127.0.0.0/8). Missing host is loopback."""
    if not isinstance(host, str):
        return True
    value = host.strip().lower()
    if not value:
        return True
    if value in LOOPBACK_HOSTS:
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if value == "::1":
        return True
    return value.startswith("127.")


def bind_host_warning(host):
    """Clear warning when --host is not loopback. None when loopback / unset."""
    if host is None or is_loopback_host(host):
        return None
    return (
        f"Bound to {host} (not loopback). "
        "The HTTP UI and MCP listener are reachable beyond this machine "
        "(Tailscale is not authentication). "
        "Default bind remains 127.0.0.1. "
        f"This requires {ALLOW_NON_LOOPBACK_FLAG} (documented footgun). Remotes HOLD."
    )


def non_loopback_refuse_message(host):
    """Error when bind host is not loopback and the override flag is absent."""
    return (
        f"Refusing to bind {host} (not loopback). "
        "The HTTP UI and MCP listener would be reachable beyond this machine. "
        f"Default bind is 127.0.0.1. Pass {ALLOW_NON_LOOPBACK_FLAG} only if you "
        "intentionally accept that exposure (documented footgun). "
        "Sharing that listener with Tailscale Serve or Funnel is not authentication."
    )


def listen_allows_non_loopback(listen_payload):
    """True when listen.json records an explicit non-loopback override."""
    if not isinstance(listen_payload, dict):
        return False
    return bool(listen_payload.get("allow_non_loopback"))
