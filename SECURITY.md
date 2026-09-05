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

- **No Apple notarization / Gatekeeper blessing.** The macOS `.app` may be **unsigned** or ad-hoc signed depending on build machine. Expect Gatekeeper prompts; do not treat double-click install as “App Store trusted.”
- **No code-signing identity guarantee** in CI receipts for this private cut.
- **No authentication / allowlist** on the HTTP UI or HTTP MCP listener. Widening `--host` (e.g. `0.0.0.0`) is an explicit footgun.
- **Remotes** (multi-machine registry) are **Coming soon** / HOLD — not a security surface in this cut.

## Safe defaults

1. Keep sticky listen on loopback; read `~/.config/port-registry/listen.json` for the live port (not hard-coded `8765`).
2. Prefer stdio MCP for agents.
3. Never Funnel the Portskill listen/UI port without explicit OK.
4. Corrupt `registry.json` / `listen.json` fails closed — Portskill will not wipe them silently.

## Soft-restart note

Stopping and restarting the keepalive/LaunchAgent (or module server) should reuse the sticky port from `listen.json` when still bindable. If bind fails, a new port is allocated and `listen.json` is rewritten — update any MCP clients that pinned the old URL.
