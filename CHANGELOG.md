# Changelog

All notable changes to **Portskill** are documented here.

Format inspired by [Keep a Changelog](https://keepachangelog.com/). Versioning follows the `project.version` in `pyproject.toml` (single product version for UI, MCP `initialize`, and `doctor`).

## [Unreleased]

### Changed

- Friend UI no longer renders **Coming soon** chrome for Workspaces, Remote machines, or Presets. One implicit workspace; Export/Import Workspace and locked `require_compat` stay.
- Primary Mac cold path is `scripts/install-mac.sh` (build or reuse `dist/`, copy to Applications, strip quarantine). Git/module launch is secondary.

### Added

- `scripts/install-mac.sh` — friend-grade Mac install + Gatekeeper quarantine strip; optional `--keepalive`.
- `scripts/notarize-mac.sh` — Developer ID codesign, `notarytool` via App Store Connect API key env (`APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_PATH`), staple. Fails closed without those vars. **Notarization is not claimed until that script is run with real creds.**

## [0.1.0] — 2026-09-05

### Added

- Private cut of Portskill: local port registry with HTML UI, MCP (stdio + HTTP), and stdlib CLI.
- Sticky listen port via `~/.config/port-registry/listen.json` (not hard-coded `:8765`).
- Compose-first MCP tools panel; System tools disclosure default-closed.
- macOS keepalive / Dock–menubar scripts; optional unsigned `.app` build scripts.
- `portskill doctor` / CLI doctor: version, registry path, listen path/URLs, UI/MCP reachability, fail-closed exit codes.
- GitHub Actions CI running `tests/smoke_test.py` on push to `main`.
- `SECURITY.md` (report path; unsigned app; Tailscale trust boundary; no notarization claim).

### Notes

- Remotes remain HOLD — no fake multi-machine maturity.
- Module path remains `port_registry_app` for compatibility; product name is **Portskill**.
- Runtime is stdlib-only (packaging metadata in `pyproject.toml` does not add pip deps to run).

[0.1.0]: https://github.com/bens27/Portskill/releases/tag/v0.1.0
