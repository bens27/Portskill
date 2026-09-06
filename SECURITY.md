# Security Policy — Portskill

## Reporting a vulnerability

Email **bens27** via GitHub Security Advisories on this private repository, or open a private issue for Ben if Advisories are unavailable. Please include:

- Portskill version (`portskill-cli doctor` → `version`, or `pyproject.toml`)
- Whether UI, MCP HTTP, or stdio is involved
- Steps to reproduce (local-only is fine)

Do **not** Funnel or publicly expose the Portskill listen port while testing a report.

## What this project promises today (private-grade)

- **Default bind is loopback** (`127.0.0.1`). Local HTTP UI and ordinary HTTP APIs (`GET /`, `GET /api/state`, UI `/api/*`, `POST /mcp`) open without `Authorization: Bearer` **unless** the opt-in passkey gate is enabled (`http-auth gate on` / Settings). Default is **off** (post-#14 open listen). When on, those routes need a short-lived httpOnly passkey session cookie **or** the optional bearer. `http_auth.json` remains an optional helper and does not gate the default listen path. `GET /health` stays open. Stdio MCP does not use a token or passkey.
- **Non-loopback `--host` is refused** at start unless `--allow-non-loopback` (documented footgun). `doctor` fails closed (exit 2) if `listen.json` shows a non-loopback host without that override recorded.
- **Stdio MCP** is the preferred agent path (`--mcp-stdio` / `examples/mcp.stdio.json`). HTTP MCP is local-trust dogfood only. Tailscale Serve/Funnel is **not** authentication.
- **Tailscale trust boundary:** Serve can map *your* claimed service ports onto your tailnet when logged in. Funnel of the Portskill listen/UI/MCP port is **refused in code**. Funnel on user service ports stays a deliberate user action.

## What we do **not** claim yet

- **No signed / notarized distribution.** Portskill does not ship an Apple-signed or notarized binary. Personal Mac `.app` builds use `build-app.sh` (ad-hoc on Darwin) plus keepalive. App Store Connect / notarization remain HOLD. There is no friend installer or signed-app distribution path.
- **No code-signing identity guarantee** in CI.
- **No OAuth / multi-user / friend-installer / signed-app distribution.** An **opt-in** WebAuthn/passkey gate may be enabled for the personal HTTP listen path; it is **off by default** and must not be confused with a mandatory loopback login wall. Operator credentials are stored under `~/.config/port-registry/` (`http_passkey.json`), not in project `registry.json`. `http_auth.json` may exist as an optional bearer helper; it does not gate default UI/API routes. Non-loopback `--host` (e.g. `0.0.0.0`) is **refused at start** unless `--allow-non-loopback` is passed (documented footgun). `doctor` fails closed (exit 2) when `listen.json` shows a non-loopback bind without that override recorded. With the override, start is allowed, the UI banner/chip stays, and `doctor` warns but exits 0. Default bind stays loopback.
- **Remotes** (multi-machine registry) remain HOLD — not a security surface in this cut.
- **No App Store Connect / ASC claim** for Portskill, the Session Handoff kit, or the Chrome extension.

## Session Handoff kit

- The kit at `vendor/session-handoff-kit/` is local stdlib (Python / bash). Portskill does not add a network client for it.
- Ledger writes go only through the vendored `handoff_ledger.py`. Files land in the project's `./.handoffs/` when that directory exists, otherwise `~/.claude/handoffs/<project-basename>/`.
- Codex `install.sh` writes `$CODEX_HOME` (default `~/.codex`): hook scripts, a skill copy, and a `hooks.json` merge. It does **not** enable the hooks feature flag or approve hook trust. Portskill does not edit `config.toml`.
- The Chrome extension is loaded in the user's browser (typically against `claude.ai`). Portskill only points at `chrome-extension/`; it does not inject into other sites.
- Handoff MCP tools use **flat** names (`handoff_status`, `handoff_list`, …) on `tools/list` — not nested `session-handoff/*`. The kit’s nested SKILL folders stay as vendored layout.
- Those tools share the **same HTTP listener** as the HTML UI and `POST /mcp`. Prefer stdio MCP for agents. Widening `--host` is still a documented footgun, not an open LAN dogfood path.
- Remotes remain HOLD. This kit does not talk to remote Portskill instances.

## Safe defaults

1. Keep sticky listen on loopback; read `~/.config/port-registry/listen.json` for the live port (not hard-coded `8765`).
2. Prefer stdio MCP for agents.
3. Never Funnel the Portskill listen/UI port — the CLI refuses Funnel of that port. Tailscale is not HTTP authentication. Leave the passkey gate **off** unless you want a local UI lock; it is opt-in.
4. Corrupt `registry.json` / `listen.json` fails closed — Portskill will not wipe them silently.

## Soft-restart note

Stopping and restarting the keepalive/LaunchAgent (or module server) should reuse the sticky port from `listen.json` when still bindable. If bind fails, a new port is allocated and `listen.json` is rewritten — update any MCP clients that pinned the old URL.
