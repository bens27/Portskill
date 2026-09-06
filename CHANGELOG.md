# Changelog

## Unreleased

### Fixed
- Session Handoff Codex `SessionStart` hook: always emit valid SessionStart JSON (and use `context-watch:` prefix) so newer Codex no longer rejects stdout that looked like JSON (`[context-watch]…`).

### Changed
- MCP tools/list name is **`portskill`** (was `portskill_path`). Description: “One MCP tool for your agent to handle all port management functions.” Compat: tools/call still accepts `portskill_path`; CLI keeps `path` / `portskill-path` / `portskill_path`. Lean enable list uses `portskill`. An older `settings.mcp_tools.portskill_path: false` key still hides the renamed tool.
- `activate` is no longer presented as a happy-path peer. It stays off in lean, remains implemented, and can be re-enabled with `settings set --mcp-tool activate=on`. `apply-defaults` / **Start Default Services** are not rebranded as Activate.
- `settings.stop_also_release` (bool, default **true**) controls whether `stop` also frees the range. When false, Stop keeps the range reserved and Release is the explicit free. Honored by CLI stop (single + bulk), UI Stop, MCP `stop`, and orchestrator mode `stop`. Restart’s stop phase still keeps the range so allocate can reuse it. Settings checkbox **Stop also Release**; CLI/MCP `settings get` / `settings set --stop-also-release on|off`. Optional per-call `--also-release on|off` / MCP `also_release`.

### Added
- Optional WebAuthn/passkey HTTP gate (default **off**). When on, the personal listen UI and mutating HTTP APIs accept a short-lived httpOnly passkey session cookie **or** the optional bearer. Register/authenticate are stdlib-only (no new pip runtime dependency). Operator credentials live in `~/.config/port-registry/http_passkey.json`, not `registry.json`. Settings UI can enable the gate and register/manage passkeys. CLI: `http-auth gate on|off` and `http-auth passkeys`.

### Changed
- HTML UI section containers no longer sit flush: stacked MCP Tools boxes (System tools ↔ Agent connection) and main-panel boxes (Settings ↔ Services, Session Handoff) share a 14px vertical gap.
- Project/service list lives under a **Services** disclosure (Settings-style all-caps header, chevron, rounded bordered container; start-open).
- Session Handoff uses the same Settings header/container/disclose chrome (`pr-subpanel`) instead of a nested panel + System-tools wrapper.
- Services records are full-bleed inside the Services frame (no row border-radius; expand/collapse hit target is the entire row, flush to container edges / row-to-row). Collapsed row padding and line-height are tighter so more services fit without clipping labels.
- Local HTTP UI and ordinary HTTP APIs on the personal listen path no longer require `Authorization: Bearer`. `GET /` serves the registry UI (no login wall). Funnel of Portskill’s own listen port remains refused. Stdio MCP is unchanged. `http-auth` CLI / `http_auth.json` remain optional helpers and do not gate default UI routes.
- MCP Tools panel order is System tools → Agent connection (each setup instruction is a start-collapsed disclosure) → User commands → Command composer.
- Session Handoff flat tool-name copy (`handoff_status`, `handoff_list`, … — not nested `session-handoff/*`) lives in the Session Handoff section. The user-command enable-map sentence stays next to User commands.
- Settings → Services list is denser (tighter padding/gap between service entries). Collapsed project disclosures show registered / active / Tailnet-served counts.
- Personal Mac `.app` path is keepalive + `build-app.sh` only. `scripts/install-mac.sh` and `scripts/notarize-mac.sh` removed from the product surface. Remotes/ASC remain HOLD.

### Added
- MCP tool `portskill_path` (CLI `path` / `portskill-path`) with `mode` start|stop|release|restart|status. Happy-path `start` is allocate → wire → activate → start → optional Tailnet Serve of **user** service ports. Returns `{ran, skipped, result, needs_input?}`. Skip predicates are deterministic Python rules. Fine primitives stay callable. `mcp_tools` can hide the path tool. Never Funnels Portskill listen. Remotes/ASC HOLD. Passkey not in this cut.
- Named `settings.mcp_tools` profiles `lean` and `full`. Default stays `full` (empty enable map; existing installs unchanged). Opt-in `lean` enables `portskill_path`, `status`, `settings_get`, plus escape hatches `allocate` / `stop` / `release`; rarely used CRUD and flat `handoff_*` tools stay off until toggled. Apply via `settings set --mcp-tools-profile lean|full` or MCP `settings_set` `{mcp_tools_profile}`. Writes the existing `mcp_tools` map; no second store. HTML UI reorder not required.
- Write-a-Handoff skill file control in Session Handoff: download the bundled skill, upload a replacement, persist `settings.handoff_skill` under `~/.config/port-registry/`. Reload keeps the choice. Stdio MCP is unchanged.

### Security
- Local HTTP UI and APIs on the personal listen path are open without a bearer token unless the **opt-in** passkey gate is enabled. Funnel of Portskill’s own listen/UI/MCP port is refused. Tailscale is not HTTP authentication. `http-auth` / `http_auth.json` remain optional helpers (not a default gate). Passkeys are opt-in only; OAuth/SSO/multi-user are not in this cut.

### Docs
- README rewritten for developers: clone then ./scripts/run.sh; removed Mac-friends cold path, installer-first narrative, and notarization-as-distribution sections.
- PLAYBOOK.md retired (friend-share / installer path unsupported).
- PLAYBOOK.md removed from the tracked tree. Local retired copies may live under gitignored `_retired/` (never published).
- SECURITY.md: no signed/notarized distribution claim; personal packaging is keepalive + `build-app.sh`; ASC/notarize HOLD.
- HTTP `http-auth` CLI / `http_auth.json` documented as optional helpers (not a default UI/API gate). Opt-in passkey gate documented (default off). Public copy no longer ranks stdio over the HTML UI / HTTP MCP or uses “dogfood”. Human-written README preamble left verbatim.

All notable changes to **Portskill** are documented here.

Format inspired by [Keep a Changelog](https://keepachangelog.com/). Versioning follows the `project.version` in `pyproject.toml` (single product version for UI, MCP `initialize`, and `doctor`).

## [Unreleased]

### Changed

- Mac rebuild replace stages beside dest (never inside the `.app`), flock-serializes overlapping `install-mac.sh` / keepalive replaces, and purges leftover `.*.new.*` / non-Contents bundle-root junk before codesign. Finder junk (`.DS_Store`, `._*`) is still stripped so ad-hoc signing does not warn about an unsealed bundle root.
- Friend UI no longer renders **Coming soon** chrome for Workspaces, Remote machines, or Presets. One implicit workspace; Export/Import Workspace and locked `require_compat` stay.
- Primary Mac cold path is `scripts/install-mac.sh` (build or reuse `dist/`, copy to Applications, strip quarantine). Git/module launch is secondary.
- Cold-path smoke/doctor no longer require remembering `PYTHONPATH=.` — use `./scripts/smoke_test.sh` and `./scripts/doctor.sh` only.
- CI runs `./scripts/smoke_test.sh` (friend smoke + `tests/test_*.py`).

### Added

- Session Handoff as a first-class Workspace section + MCP tools. The full kit is tracked at `vendor/session-handoff-kit/` (Claude Code plugin, Cowork/chat artifacts via `package.sh`, Codex installer, Chrome extension, ledger CLI; pin `7587834` in `VENDORED.md`). UI Add / manage per README surface (copy `/plugin` commands, `package.sh`, Codex `install.sh`, Chrome path). MCP: flat names `handoff_status`, `handoff_skill`, `handoff_template`, `handoff_list`, `handoff_resolve`, `handoff_new_path`, `handoff_resume`, `handoff_supersede`, `handoff_install_help` (not nested `session-handoff/*`). Ledger writes only via the vendored `handoff_ledger.py`. `doctor` reports kit present/configured (fail-closed on a bad override). Optional override: `settings.handoff_kit` / `PORTSKILL_HANDOFF_KIT`.
- Doctor exit contract documented and tested: exit 0 healthy offline / informational warnings; exit 2 fail-closed (corrupt registry/listen, missing skill files, listening-but-unreachable, non-loopback bind without `--allow-non-loopback`, invalid handoff kit). `scripts/doctor.sh` matches CLI codes. Test isolation pins `PORTSKILL_LISTEN_PATH` + temp `HOME` so leftover listen.json / sticky-port races do not flake Linux CI.
- `scripts/install-mac.sh` — friend-grade Mac install + Gatekeeper quarantine strip; optional `--keepalive`.
- `scripts/notarize-mac.sh` — Developer ID codesign, `notarytool` via App Store Connect API key env (`APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_PATH`), staple. Fails closed without those vars. **Notarization is not claimed until that script is run with real creds.**
- `scripts/build-app.sh` ad-hoc codesigns on Darwin when no Developer ID / `PORTSKILL_SIGN_IDENTITY` is present. Ad-hoc ≠ notarized. Signing is skipped (not failed) on Linux CI.
- Loopback harden: non-loopback `--host` is refused at start unless `--allow-non-loopback` (footgun). `doctor` fails closed on a non-loopback `listen.json` host unless that override is recorded. UI banner/chip remains when bound off loopback. Default bind unchanged.
- MCP / Compose UI and README cold path prefer **stdio** for agents; HTTP MCP is labeled local-trust dogfood only.
- `scripts/cli.sh`, `scripts/doctor.sh`, `scripts/run.sh` — PYTHONPATH wrappers for a cold clone.
- Expanded stdlib tests: version identity, doctor offline, MCP tool toggles, listen.json sticky, collapsed Compose/System Tools markup.
- Friend-share PLAYBOOK.md (AirDrop/zip right-click Open vs clone + `install-mac.sh`). Linked from the README cold path. Remotes HOLD; no ASC/notarize claim.
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
