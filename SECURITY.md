# Security Policy — Portskill

## Reporting a vulnerability

Email **bens27** via GitHub Security Advisories on this private repository, or open a private issue for Ben if Advisories are unavailable. Please include:

- Portskill version (`portskill-cli doctor` → `version`, or `pyproject.toml`)
- Whether UI, MCP HTTP, or stdio is involved
- Steps to reproduce (local-only is fine)

Do **not** Funnel or publicly expose the Portskill listen port while testing a report.

## What this project promises today (private-grade)

- **Default bind is loopback** (`127.0.0.1`). The same unauthenticated listener serves the HTML UI, `GET /api/state`, and mutating `POST /mcp`.
- **Stdio MCP** is the preferred agent path (`--mcp-stdio`). HTTP MCP is local-trust dogfood only.
- **Tailscale trust boundary:** Serve can map *your* claimed service ports onto your tailnet when logged in. Funneling the Portskill UI/MCP listen port is out of scope unless Ben explicitly OK’s it. Treat anything on the listen port as local-trust.

## What we do **not** claim yet

- **No notarized friend build unless `scripts/notarize-mac.sh` actually succeeded** with a Developer ID and Apple credentials. `build-app.sh` / `install-mac.sh` do **not** notarize. On a Mac, `build-app.sh` **ad-hoc codesigns** (`codesign --sign -`) when no Developer ID / `PORTSKILL_SIGN_IDENTITY` is available — that is not Apple notarization. Until a given `.app` is signed + notarized + stapled, expect Gatekeeper prompts.
- **Gatekeeper workaround for trusted local builds:** `scripts/install-mac.sh` runs `xattr -dr com.apple.quarantine` on the installed app. Friends who skip the installer can right-click → Open once. This is not the same as Apple notarization.
- **Notarization path (maintainer):** `scripts/notarize-mac.sh` codesigns `dist/Portskill.app` (`PORTSKILL_SIGN_IDENTITY` or auto-detect Developer ID Application), submits a zip/dmg via `xcrun notarytool` using App Store Connect API key env (`APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_PATH` → `AuthKey_XXX.p8`), and staples. Apple ID / app-specific password is not used. The script fails closed if those vars are missing. Do not commit the `.p8`. **This file does not claim notarization succeeded.**
- **No code-signing identity guarantee** in CI receipts for this private cut.
- **No authentication / allowlist** on the HTTP UI or HTTP MCP listener. Widening `--host` (e.g. `0.0.0.0`) is an explicit footgun — `doctor` and the UI surface a warning; default bind stays loopback.
- **Remotes** (multi-machine registry) remain HOLD — not a security surface in this cut.
- **No App Store Connect / ASC notarization claim** for Portskill, the Session Handoff kit, or the Chrome extension. Ad-hoc codesign ≠ notarized.

## Session Handoff kit

- The kit at `vendor/session-handoff-kit/` is local stdlib (Python / bash). Portskill does not add a network client for it.
- Ledger writes go only through the vendored `handoff_ledger.py`. Files land in the project's `./.handoffs/` when that directory exists, otherwise `~/.claude/handoffs/<project-basename>/`.
- Codex `install.sh` writes `$CODEX_HOME` (default `~/.codex`): hook scripts, a skill copy, and a `hooks.json` merge. It does **not** enable the hooks feature flag or approve hook trust. Portskill does not edit `config.toml`.
- The Chrome extension is loaded in the user's browser (typically against `claude.ai`). Portskill only points at `chrome-extension/`; it does not inject into other sites.
- Handoff MCP tools (`handoff_*`) share the **same unauthenticated loopback listener** as the HTML UI and `POST /mcp`. Prefer stdio MCP for agents. Widening `--host` exposes these tools too.
- Remotes remain HOLD. This kit does not talk to remote Portskill instances.

## Safe defaults

1. Keep sticky listen on loopback; read `~/.config/port-registry/listen.json` for the live port (not hard-coded `8765`).
2. Prefer stdio MCP for agents.
3. Never Funnel the Portskill listen/UI port without explicit OK.
4. Corrupt `registry.json` / `listen.json` fails closed — Portskill will not wipe them silently.

## Soft-restart note

Stopping and restarting the keepalive/LaunchAgent (or module server) should reuse the sticky port from `listen.json` when still bindable. If bind fails, a new port is allocated and `listen.json` is rewritten — update any MCP clients that pinned the old URL.
