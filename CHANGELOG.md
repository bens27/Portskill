# Changelog

All notable changes to **Portskill** are documented here.

Format inspired by [Keep a Changelog](https://keepachangelog.com/). Versioning follows the `project.version` in `pyproject.toml` (single product version for UI, MCP `initialize`, and `doctor`).

## [Unreleased]

### Changed

- Friend UI no longer renders **Coming soon** chrome for Workspaces, Remote machines, or Presets. One implicit workspace; Export/Import Workspace and locked `require_compat` stay.
- Primary Mac cold path is `scripts/install-mac.sh` (build or reuse `dist/`, copy to Applications, strip quarantine). Git/module launch is secondary.
- Cold-path smoke/doctor no longer require remembering `PYTHONPATH=.` — use `./scripts/smoke_test.sh` and `./scripts/doctor.sh` only.
- CI runs `./scripts/smoke_test.sh` (friend smoke + `tests/test_*.py`).

### Added

- Session Handoff as a first-class Workspace section + MCP tools. The full kit is tracked at `vendor/session-handoff-kit/` (Claude Code plugin, Cowork/chat artifacts via `package.sh`, Codex installer, Chrome extension, ledger CLI; pin `7587834` in `VENDORED.md`). UI Add / manage per README surface (copy `/plugin` commands, `package.sh`, Codex `install.sh`, Chrome path). MCP: flat names `handoff_status`, `handoff_skill`, `handoff_template`, `handoff_list`, `handoff_resolve`, `handoff_new_path`, `handoff_resume`, `handoff_supersede`, `handoff_install_help` (not nested `session-handoff/*`). Ledger writes only via the vendored `handoff_ledger.py`. `doctor` reports kit present/configured (fail-closed on a bad override). Optional override: `settings.handoff_kit` / `PORTSKILL_HANDOFF_KIT`.
- `scripts/install-mac.sh` — friend-grade Mac install + Gatekeeper quarantine strip; optional `--keepalive`.
- `scripts/notarize-mac.sh` — Developer ID codesign, `notarytool` via App Store Connect API key env (`APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_PATH`), staple. Fails closed without those vars. **Notarization is not claimed until that script is run with real creds.**
- `scripts/build-app.sh` ad-hoc codesigns on Darwin when no Developer ID / `PORTSKILL_SIGN_IDENTITY` is present. Ad-hoc ≠ notarized. Signing is skipped (not failed) on Linux CI.
- Loopback footgun warning: `doctor` `message` + UI banner/chip when `--host` is not `127.0.0.1` / `::1` / `localhost`. Default bind unchanged.
- `scripts/cli.sh`, `scripts/doctor.sh`, `scripts/run.sh` — PYTHONPATH wrappers for a cold clone.
- Expanded stdlib tests: version identity, doctor offline, MCP tool toggles, listen.json sticky, collapsed Compose/System Tools markup.
- Friend-share **[PLAYBOOK.md](PLAYBOOK.md)** (AirDrop/zip right-click Open vs clone + `install-mac.sh`). Linked from the README cold path. Remotes HOLD; no ASC/notarize claim.
- Disclosure chevrons: Settings is a start-collapsed details (same cobalt arrowhead + Show/Hide as System tools / repo). Serve URL gets the same Show hint. JS forces all of those closed on load.

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
