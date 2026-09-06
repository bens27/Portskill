#!/usr/bin/env python3
"""Non-destructive Portskill smoke test (stdlib only). Exit 0 on pass."""
from __future__ import annotations

import json
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
    mcp_html = html
    try:
        from port_registry_app.server import handoff_panel_html, mcp_tools_panel_html

        mcp_html = mcp_tools_panel_html(view)
        handoff_html = handoff_panel_html(view)
    except Exception as exc:  # pragma: no cover
        fail(f"import MCP/handoff panel helpers: {exc}")
    order = (
        mcp_html.find('id="pr-mcp-system-details"'),
        mcp_html.find('id="pr-mcp-connect"'),
        mcp_html.find('id="pr-mcp-user-commands-section"'),
        mcp_html.find('id="pr-mcp-user-composer"'),
    )
    if any(i < 0 for i in order) or order != tuple(sorted(order)):
        fail(f"MCP Tools section order is not System → Agent Connection → User Commands → Composer: {order}")
    if 'id="pr-mcp-connect-stdio"' not in mcp_html or 'id="pr-mcp-connect-http"' not in mcp_html:
        fail("Agent Connection options missing expandable stdio/HTTP details")
    if "session-handoff/*" in mcp_html or "not nested" in mcp_html:
        fail("Session Handoff tool-name copy still lives in MCP Tools")
    if "session-handoff/*" not in handoff_html or "handoff_status" not in handoff_html:
        fail("Session Handoff tool-name copy missing from Session Handoff")
    if "Write-a-Handoff skill" not in handoff_html or 'data-pr-action="handoff-skill-download"' not in handoff_html:
        fail("Write-a-Handoff skill download/upload controls missing")
    if "data-pr-project-counts" not in html and "registered" not in html:
        fail("collapsed project summaries missing registered/active/Tailnet counts")
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
    if "stdio preferred" in html.lower() or "preferred for agents" in html.lower():
        fail("UI MCP panel still ranks stdio above HTTP or the UI")
    if "local-trust dogfood" in html.lower() or "dogfood" in html.lower():
        fail("UI still uses retired dogfood copy")
    if "--mcp-stdio" not in html:
        fail("UI missing copyable stdio MCP command")
    if "same local listener" not in html.lower():
        fail("UI HTTP MCP panel missing shared-listener copy")
    if 'id="pr-mcp-http-hint"' not in html:
        fail("UI missing HTTP MCP hint")
    if "funnel of portskill" not in html.lower() and "funnel of portskill's own listen" not in html.lower():
        fail("UI HTTP MCP panel missing Funnel-of-listen fact")
    ok("friend UI: no Coming soon chrome; Settings + Actions + disclosures present; surface copy")

    for script in (
        "scripts/build-app.sh",
        "scripts/install-keepalive.sh",
        "scripts/mac-bundle.sh",
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
    for gone in ("scripts/install-mac.sh", "scripts/notarize-mac.sh"):
        if (ROOT / gone).exists():
            fail(f"{gone} must not be on the product surface")
    ok("personal Mac packaging scripts present; install-mac/notarize stripped")

    # 2) CLI status + doctor (isolated; leftover home listen.json must not flake offline)
    from tests.helpers import IsolatedConfig, parse_cli_json

    with IsolatedConfig() as iso:
        for cmd in ("status", "doctor"):
            proc = iso.run_cli([cmd])
            if proc.returncode != 0:
                fail(f"cli {cmd} exit {proc.returncode}: {proc.stderr or proc.stdout}")
            try:
                payload = parse_cli_json(proc)
            except json.JSONDecodeError as exc:
                fail(f"cli {cmd} non-JSON: {exc}: {proc.stdout[:200]}")
            if payload.get("status") != "ok":
                fail(f"cli {cmd} unexpected payload status={payload.get('status')!r}")
            if cmd == "doctor":
                if payload.get("reason") is not None:
                    fail(f"doctor offline reason={payload.get('reason')!r}")
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
                    "bind_host",
                    "handoff_kit",
                    "http_auth",
                ):
                    if need not in names:
                        fail(f"doctor missing check {need!r}")
                http_auth = payload.get("http_auth")
                if not isinstance(http_auth, dict) or "configured" not in http_auth:
                    fail(f"doctor missing http_auth status: {http_auth!r}")
                if "token" in http_auth:
                    fail("doctor http_auth leaked token field")
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

    ui_url = listen.get("ui_url")
    mcp_url = listen.get("mcp_url")
    if not ui_url:
        fail("listen.json missing ui_url")
    if not mcp_url:
        fail("listen.json missing mcp_url")
    try:
        with urllib.request.urlopen(ui_url, timeout=5) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if int(code) != 200:
                fail(f"GET ui_url {ui_url} -> HTTP {code}")
            ok(f"GET ui_url -> HTTP {code} ({ui_url})")
    except urllib.error.URLError as exc:
        fail(f"GET ui_url {ui_url}: {exc}")

    try:
        req = urllib.request.Request(mcp_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = int(getattr(resp, "status", None) or resp.getcode())
            if code == 200:
                ok(f"GET mcp_url -> HTTP {code} ({mcp_url})")
            else:
                fail(f"GET mcp_url {mcp_url} -> HTTP {code}")
    except urllib.error.HTTPError as exc:
        fail(f"GET mcp_url {mcp_url}: HTTP {exc.code}")
    except urllib.error.URLError as exc:
        fail(f"GET mcp_url {mcp_url}: {exc}")

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
