# Changelog

Notable changes to Portskill. Version numbers match `pyproject.toml` and the
application's UI, MCP server, and CLI.

## Unreleased

No additional changes queued.

## [0.1.1] — release prepared

### Added

- A developer quick start, detailed usage guide, contribution guidelines, issue
  and pull request templates, and the MIT license declared by the package.
- Service defaults, workspace import/export, named presets, command composition,
  and `full` / `lean` MCP tool profiles.
- Optional passkey protection for the local HTTP listener, disabled by default.
- Source and wheel installation checks in CI, plus Python 3.10/3.12 on Linux
  and Python 3.12 on macOS.

### Changed

- Session Handoff lives under **Experimental (Beta)** and is **disabled by
  default**. Opt-in controls MCP discovery, direct and chained calls, dashboard
  ledger reads, and installer/package actions. Existing explicit opt-ins remain
  enabled. Installing or removing external agent hooks is a separate action.
- The main lifecycle MCP tool is named `portskill`; the `portskill_path` alias
  remains compatible. `activate` remains an internal primitive and is off in lean.
- **Stop also Release** controls whether stopping a service frees its reservation
  (default on). Deactivate keeps reservations unless explicitly told to release.
- Tailscale Serve for Portskill's own listener defaults off for new installs;
  existing explicit settings are preserved.
- The dashboard uses collapsible Settings, Services, agent connection, and
  experimental sections. Service rows show registered/active/Tailnet counts.
- Optional agent skills and an absent, unconfigured Handoff kit no longer make
  core installed-package health checks fail. Wheels and Mac apps need an explicit
  source-kit path to enable Handoff.

### Fixed

- Fresh-clone Mac builds now use tracked app templates; the launcher works with
  stock macOS Bash 3.2. Local app replacement is staged and serialized.
- Session Handoff's Codex SessionStart hook emits valid JSON.
- Tests isolate Tailscale discovery from the real local daemon. Live-server
  smoke checks require `--live`.
- Distributed source excludes an internal session checkpoint, personal checkout
  paths, runtime state, and built apps.

### Security

- GET and POST validate Host headers before accessing data or dispatching
  actions, protecting the local listener from DNS rebinding. Only known local
  hosts and the exact own Tailscale DNS name when Serve is enabled are accepted.
- Mutating requests reject cross-origin browser access and require JSON content
  types. Passkey bootstrap is loopback-only.
- Loopback remains the default bind. Non-loopback binds require an explicit flag;
  Funnel of Portskill's own listener is refused.

### Distribution

- This release supplies Python source and a pure-Python wheel. No signed or
  notarized Mac binary is distributed; Mac build scripts are included in source.
- Runtime requirements: Python 3.10+ on macOS or Linux, with no third-party Python
  runtime dependencies. Tailscale is optional. Remote machine management and
  multi-user access are not implemented.

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

[0.1.1]: https://github.com/bens27/Portskill/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/bens27/Portskill/tree/v0.1.0
