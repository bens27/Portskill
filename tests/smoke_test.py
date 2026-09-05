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

    # 1b) Friend UI must not advertise unfinished Workspaces / Remotes / Presets.
    try:
        from port_registry_app.server import (
            build_view,
            env_rail_html,
            environment_rail_html,
            presets_panel_html,
            render_page,
            settings_panel_html,
        )
    except Exception as exc:  # pragma: no cover
        fail(f"import UI render helpers: {exc}")
    view = build_view({})
    html = render_page(view, tailscale={"chip": "Needs login", "state": "needs_login", "logged_in": False})
    if "Coming soon" in html:
        fail("rendered UI still contains 'Coming soon' (Workspaces/Remotes/Presets chrome)")
    for name, blob in (
        ("env_rail_html", env_rail_html(view)),
        ("environment_rail_html", environment_rail_html(view)),
        ("presets_panel_html", presets_panel_html(view)),
        ("settings_panel_html", settings_panel_html(view)),
    ):
        if "Coming soon" in blob:
            fail(f"{name} still contains 'Coming soon'")
    body = html.split("</style>", 1)[-1]
    if "pr-env-soon" in body or "pr-remote-soon" in body or "pr-presets-soon" in body:
        fail("friend UI still renders Workspaces/Remotes/Presets Coming soon markup")
    if "System tools" not in html:
        fail("System tools disclosure missing from rendered UI")
    if "Session Handoff" not in html or 'id="pr-handoff-details"' not in html:
        fail("Session Handoff section missing from rendered UI")
    if "Coming soon" in html.split("Session Handoff", 1)[-1][:800]:
        fail("Session Handoff section advertises Coming soon")
    if "Add / manage" not in html or 'data-pr-action="handoff-copy"' not in html:
        fail("Session Handoff install matrix missing add/manage actions")
    if 'data-pr-action="handoff-codex-install"' not in html:
        fail("Session Handoff matrix missing Codex install.sh action")
    if "Export Workspace" not in html:
        fail("Export Workspace missing from rendered UI")
    if "Require compatibility" not in html or 'id="pr-require-compat"' not in html:
        fail("require_compat lock missing from Settings")
    if 'class="pr-disclose"' not in html and "pr-mcp-system-details" not in html:
        fail("disclosure chevron markup missing")
    if "stdio preferred" not in html.lower() and "preferred for agents" not in html.lower():
        fail("UI MCP panel missing stdio preference")
    if "--mcp-stdio" not in html or "local-trust dogfood" not in html.lower():
        fail("UI missing copyable stdio MCP / HTTP dogfood label")
    ok("friend UI: no Coming soon chrome; Settings + Actions + disclosures present; stdio preferred")

    for script in (
        "scripts/install-mac.sh",
        "scripts/notarize-mac.sh",
        "scripts/build-app.sh",
        "scripts/smoke_test.sh",
        "scripts/doctor.sh",
        "scripts/cli.sh",
        "scripts/run.sh",
    ):
        path = ROOT / script
        if not path.is_file():
            fail(f"missing {script}")
        syn = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        if syn.returncode != 0:
            fail(f"bash -n {script}: {syn.stderr or syn.stdout}")
    creds = subprocess.run(
        ["bash", str(ROOT / "scripts/notarize-mac.sh")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if creds.returncode == 0:
        fail("notarize-mac.sh succeeded without credentials (must fail closed)")
    hint = (creds.stderr or "") + (creds.stdout or "")
    for need in (
        "APP_STORE_CONNECT_API_KEY_PATH",
        "APP_STORE_CONNECT_ISSUER_ID",
        "APP_STORE_CONNECT_KEY_ID",
    ):
        if need not in hint:
            fail(f"notarize-mac.sh missing-creds hint omitted {need}")
    if "APPLE_ID" in hint or "APPLE_APP_SPECIFIC_PASSWORD" in hint:
        fail("notarize-mac.sh still advertises Apple ID password auth")
    help_proc = subprocess.run(
        ["bash", str(ROOT / "scripts/notarize-mac.sh"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    help_text = (help_proc.stdout or "") + (help_proc.stderr or "")
    for need in (
        "APP_STORE_CONNECT_KEY_ID",
        "APP_STORE_CONNECT_ISSUER_ID",
        "APP_STORE_CONNECT_API_KEY_PATH",
        "PORTSKILL_SIGN_IDENTITY",
    ):
        if need not in help_text:
            fail(f"notarize-mac.sh --help omitted {need}")
    ok("install-mac.sh + notarize-mac.sh present; notarize fails closed without API key env")

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
            for need in (
                "version",
                "listen",
                "ui_reachability",
                "mcp_reachability",
                "registry",
                "handoff_kit",
            ):
                if need not in names:
                    fail(f"doctor missing check {need!r}")
            hk = payload.get("handoff_kit")
            if not isinstance(hk, dict) or not hk.get("present"):
                fail(f"doctor handoff_kit not present: {hk!r}")
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
