# Changelog

All notable changes to **Portskill** are documented here.

Format inspired by [Keep a Changelog](https://keepachangelog.com/). Versioning follows the `project.version` in `pyproject.toml` (single product version for UI, MCP `initialize`, and `doctor`).

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

- Remotes remain **Coming soon** (HOLD) — no fake multi-machine maturity.
- Module path remains `port_registry_app` for compatibility; product name is **Portskill**.
- Runtime is stdlib-only (packaging metadata in `pyproject.toml` does not add pip deps to run).

[0.1.0]: https://github.com/bens27/Portskill/releases/tag/v0.1.0
