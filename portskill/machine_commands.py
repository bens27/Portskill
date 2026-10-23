"""Held remote-machine command surface."""
from __future__ import annotations


def machine_unavailable_result(options: dict | None = None) -> dict:
    """Keep the public machine surface inert until remote ownership ships."""
    del options
    return {"ok": False, "message": "Remote machines — Coming soon"}
