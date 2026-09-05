#!/usr/bin/env python3
"""Non-destructive Portskill smoke test (stdlib only). Exit 0 on pass."""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
LISTEN = pathlib.Path.home() / ".config" / "port-registry" / "listen.json"


def fail(msg: str, code: int = 1) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(code)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        fail("pyproject.toml missing version = \"…\"")
    return m.group(1)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    # 1) import + version consistency (pyproject ↔ package ↔ MCP SERVER_VERSION)
    try:
        import port_registry_app  # noqa: F401
        from port_registry_app import __version__
        from port_registry_app.mcp import SERVER_VERSION
    except Exception as exc:  # pragma: no cover
        fail(f"import port_registry_app: {exc}")
    pkg_ver = __version__
    py_ver = _pyproject_version()
    if pkg_ver != py_ver:
        fail(f"version mismatch: __version__={pkg_ver!r} pyproject={py_ver!r}")
    if SERVER_VERSION != pkg_ver:
        fail(f"version mismatch: SERVER_VERSION={SERVER_VERSION!r} __version__={pkg_ver!r}")
    ok(f"import port_registry_app version={pkg_ver} ({getattr(port_registry_app, '__file__', '?')})")

    # 2) CLI status + doctor (non-destructive; doctor must stay green offline)
    for cmd in ("status", "doctor"):
        proc = subprocess.run(
            [sys.executable, "-m", "port_registry_app.cli", cmd],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            fail(f"cli {cmd} exit {proc.returncode}: {proc.stderr or proc.stdout}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            fail(f"cli {cmd} non-JSON: {exc}: {proc.stdout[:200]}")
        if payload.get("status") != "ok":
            fail(f"cli {cmd} unexpected payload status={payload.get('status')!r}")
        if cmd == "doctor":
            if payload.get("version") != pkg_ver:
                fail(f"doctor version={payload.get('version')!r} != {pkg_ver!r}")
            if not payload.get("registry_path"):
                fail("doctor missing registry_path")
            if not payload.get("listen_path"):
                fail("doctor missing listen_path")
            names = {c.get("name") for c in (payload.get("checks") or []) if isinstance(c, dict)}
            for need in ("version", "listen", "ui_reachability", "mcp_reachability", "registry"):
                if need not in names:
                    fail(f"doctor missing check {need!r}")
        ok(f"cli {cmd} status={payload.get('status')!r}")

    # 3) If server already up, GET ui_url and mcp_url from listen.json
    if not LISTEN.is_file():
        ok("listen.json absent — skip HTTP (server not required for smoke)")
        print("PASS")
        return 0

    try:
        listen = json.loads(LISTEN.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"listen.json unreadable: {exc}")

    if not listen.get("listening"):
        ok("listen.json listening=false — skip HTTP")
        print("PASS")
        return 0

    for key in ("ui_url", "mcp_url"):
        url = listen.get(key)
        if not url:
            fail(f"listen.json missing {key}")
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                code = getattr(resp, "status", None) or resp.getcode()
                if int(code) != 200:
                    fail(f"GET {key} {url} -> HTTP {code}")
                ok(f"GET {key} -> HTTP {code} ({url})")
        except urllib.error.URLError as exc:
            fail(f"GET {key} {url}: {exc}")

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
