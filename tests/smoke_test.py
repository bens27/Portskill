#!/usr/bin/env python3
"""Non-destructive Portskill smoke test (stdlib only). Exit 0 on pass."""
from __future__ import annotations

import json
import os
import pathlib
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


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    # 1) import
    try:
        import port_registry_app  # noqa: F401
    except Exception as exc:  # pragma: no cover
        fail(f"import port_registry_app: {exc}")
    ok(f"import port_registry_app ({getattr(port_registry_app, '__file__', '?')})")

    # 2) CLI status + doctor (non-destructive)
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
        if payload.get("status") not in ("ok", "success", True) and payload.get("reason") not in (None, "ok"):
            # doctor/status historically use status: "ok"
            if payload.get("status") != "ok":
                fail(f"cli {cmd} unexpected payload status={payload.get('status')!r}")
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
