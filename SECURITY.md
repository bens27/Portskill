# Security Policy — Portskill

## Reporting a vulnerability

Email **bens27** via GitHub Security Advisories on this private repository, or open a private issue for Ben if Advisories are unavailable. Please include:

- Portskill version (`portskill-cli doctor` → `version`, or `pyproject.toml`)
- Whether UI, MCP HTTP, or stdio is involved
- Steps to reproduce (local-only is fine)

Do **not** Funnel or publicly expose the Portskill listen port while testing a report.

## What this project promises today (private-grade)

- **Default bind is loopback** (`127.0.0.1`). HTTP mutating and inventory surfaces (`POST /mcp`, `GET /mcp`, `GET /api/state`, UI `/api/*`) require `Authorization: Bearer` from `~/.config/port-registry/http_auth.json` — even on loopback. `GET /health` may stay open. Stdio MCP does not use this token.
- **Non-loopback `--host` is refused** at start unless `--allow-non-loopback` (documented footgun). With the override, bearer auth is still mandatory (no open LAN dogfood). `doctor` fails closed (exit 2) if `listen.json` shows a non-loopback host without that override recorded.
- **Stdio MCP** is the preferred agent path (`--mcp-stdio` / `examples/mcp.stdio.json`). HTTP MCP is local-trust dogfood only and needs the local bearer. Tailscale Serve/Funnel is **not** authentication.
- **Tailscale trust boundary:** Serve can map *your* claimed service ports onto your tailnet when logged in. Funnel of the Portskill listen/UI/MCP port is **refused in code**. Funnel on user service ports stays a deliberate user action.

## What we do **not** claim yet

- **No signed / notarized distribution.** Portskill does not ship an Apple-signed or notarized binary for other people. Developers who need a signed `.app` should use their own signing workflow. Maintainer helper scripts (`build-app.sh`, `notarize-mac.sh`, `install-mac.sh`) are optional and unsupported as a product distribution path.
- **No code-signing identity guarantee** in CI.
- **No OAuth / passkeys / friend-installer / signed-app distribution.** HTTP uses a local high-entropy bearer only (`http_auth.json`). Non-loopback `--host` (e.g. `0.0.0.0`) is **refused at start** unless `--allow-non-loopback` is passed (documented footgun). Auth stays mandatory with the override. `doctor` fails closed (exit 2) when `listen.json` shows a non-loopback bind without that override recorded. With the override, start is allowed, the UI banner/chip stays, and `doctor` warns but exits 0. Default bind stays loopback.
- **Remotes** (multi-machine registry) remain HOLD — not a security surface in this cut.
- **No App Store Connect / ASC claim** for Portskill, the Session Handoff kit, or the Chrome extension.

## Session Handoff kit

- The kit at `vendor/session-handoff-kit/` is local stdlib (Python / bash). Portskill does not add a network client for it.
- Ledger writes go only through the vendored `handoff_ledger.py`. Files land in the project's `./.handoffs/` when that directory exists, otherwise `~/.claude/handoffs/<project-basename>/`.
- Codex `install.sh` writes `$CODEX_HOME` (default `~/.codex`): hook scripts, a skill copy, and a `hooks.json` merge. It does **not** enable the hooks feature flag or approve hook trust. Portskill does not edit `config.toml`.
- The Chrome extension is loaded in the user's browser (typically against `claude.ai`). Portskill only points at `chrome-extension/`; it does not inject into other sites.
- Handoff MCP tools use **flat** names (`handoff_status`, `handoff_list`, …) on `tools/list` — not nested `session-handoff/*`. The kit’s nested SKILL folders stay as vendored layout.
- Those tools share the **same HTTP listener** as the HTML UI and `POST /mcp` and therefore require the local bearer. Prefer stdio MCP for agents. Widening `--host` still requires the token; it is not an open LAN dogfood path.
- Remotes remain HOLD. This kit does not talk to remote Portskill instances.

## Safe defaults

1. Keep sticky listen on loopback; read `~/.config/port-registry/listen.json` for the live port (not hard-coded `8765`).
2. Prefer stdio MCP for agents.
3. Never Funnel the Portskill listen/UI port — the CLI refuses Funnel of that port. Tailscale is not HTTP authentication.
4. Corrupt `registry.json` / `listen.json` fails closed — Portskill will not wipe them silently.

## Soft-restart note

Stopping and restarting the keepalive/LaunchAgent (or module server) should reuse the sticky port from `listen.json` when still bindable. If bind fails, a new port is allocated and `listen.json` is rewritten — update any MCP clients that pinned the old URL.
