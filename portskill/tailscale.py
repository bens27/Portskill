"""Shared non-interactive Tailscale discovery helpers."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess

DEFAULT_TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def resolve_tailscale_bin():
    configured = os.environ.get("PORTSKILL_TAILSCALE_BIN")
    if configured:
        return configured
    which = shutil.which("tailscale")
    if which:
        return which
    mac_default = pathlib.Path(DEFAULT_TAILSCALE_BIN)
    if mac_default.exists():
        return str(mac_default)
    return DEFAULT_TAILSCALE_BIN


def tailscale_base_command():
    return shlex.split(resolve_tailscale_bin())


def tailscale_bin_available():
    binary = resolve_tailscale_bin()
    if not binary:
        return False, binary
    first = shlex.split(binary)[0]
    path = pathlib.Path(first)
    if path.exists() or shutil.which(first):
        return True, binary
    return False, binary


def extract_tailscale_auth_url(text):
    if not text:
        return None
    match = re.search(r"https://login\.tailscale\.com/\S+", text)
    if match:
        return match.group(0).rstrip(").,;'\"")
    match = re.search(r"https://[^\s]+tailscale[^\s]*", text)
    if match:
        return match.group(0).rstrip(").,;'\"")
    return None


def probe_tailscale_status():
    """Return a non-interactive Tailscale status summary."""
    available, binary = tailscale_bin_available()
    if not available:
        return {
            "ok": False,
            "chip": "Binary missing",
            "state": "binary_missing",
            "reason": "tailscale_bin_missing",
            "message": f"Tailscale binary not found: {binary}",
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": binary,
            "logged_in": False,
        }
    try:
        result = subprocess.run(
            tailscale_base_command() + ["status", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "chip": "Needs login",
            "state": "needs_login",
            "reason": "tailscale_status_timeout",
            "message": "tailscale status timed out (is the daemon running / logged in?)",
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": binary,
            "logged_in": False,
        }
    except OSError as exc:
        return {
            "ok": False,
            "chip": "Binary missing",
            "state": "binary_missing",
            "reason": "tailscale_bin_exec_failed",
            "message": str(exc),
            "BackendState": None,
            "Self": None,
            "AuthURL": None,
            "binary": binary,
            "logged_in": False,
        }

    raw_out = (result.stdout or "").strip()
    raw_err = (result.stderr or "").strip()
    data = {}
    if raw_out:
        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    backend = data.get("BackendState")
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else None
    auth_url = (
        data.get("AuthURL")
        or extract_tailscale_auth_url(raw_out)
        or extract_tailscale_auth_url(raw_err)
    )
    logged_in = bool(
        backend == "Running"
        or (self_node and backend not in ("NeedsLogin", "NoState"))
    )

    if logged_in:
        chip, state, reason = "Connected", "connected", None
        message = f"BackendState={backend}"
    elif backend == "NeedsLogin" or auth_url:
        chip, state, reason = (
            "Needs login",
            "needs_login",
            "tailscale_auth_required",
        )
        message = "Tailscale is installed but not logged in."
    elif result.returncode != 0 and not data:
        chip, state, reason = (
            "Needs login",
            "needs_login",
            "tailscale_auth_required",
        )
        message = raw_err or raw_out or f"tailscale status exited {result.returncode}"
    else:
        chip, state, reason = (
            "Needs login",
            "needs_login",
            "tailscale_auth_required",
        )
        message = f"Tailscale BackendState={backend!r}; login required for Serve."

    return {
        "ok": logged_in,
        "chip": chip,
        "state": state,
        "reason": reason,
        "message": message,
        "BackendState": backend,
        "Self": self_node,
        "AuthURL": auth_url,
        "binary": binary,
        "logged_in": logged_in,
        "raw_exit": result.returncode,
    }
