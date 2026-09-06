# Portskill

*Human-written pre-amble*
I made this tool after getting tired of juggling experiments and port problems on a single machine. There's also an additional Session Handoff skill bundled into this.

Portskill is a port registry tool, with some bells and whistles:
- Choose between an HTML dashboard, MCP, or stdlib CLI surfaces
- Reserve a port range (per project), and assign ports from that range automatically
- Authenticate with Tailscale to serve any registered service on your Tailnet
- Provides commands for the whole life-cycle, and allows you to make a singular command out of a chain of commands for your own flow(s)
- Allow or prohibit model invocation per-command 
- Captures Start and Stop shell commands from your agents for any service, and triggers background sessions to execute them
- Allows you to set default Start states (i.e. when you launch Portskill, turn on services X, Y, and Z)
- Fully deterministic control plane

- Additional skill included: Session Handoff. A hook views the session's context window after each turn. At a determined value (preset to 130,000 tokens), the session automatically creates a handoff document, and writes its "Open" status to a ledger. At that point, just /clear your session and ask your agent to retrieve its handoff. Keeps your agent out of the stupid zone.

Coming soon:
- Multiple workspaces - choose services you generally run for one 'mode' in your life, like UI Work Mode, Travel Advice mode, etc.
- 

*End of human-written pre-amble*


Local port registry for developers and LLM agents. Allocate and manage shared development ports with an HTML UI, MCP tools, and a stdlib CLI.

One data store: `~/.config/port-registry/registry.json`. No second store. No Node and no pip packages required to run.

> Python module path remains `port_registry_app` for compatibility. Product name is **Portskill**.

## Requirements

- macOS or Linux
- Python 3.10+

## Quick start

```bash
git clone https://github.com/bens27/Portskill.git
cd Portskill
./scripts/run.sh --no-open
```

Read the live UI URL (listen port is sticky — do not assume `:8765`):

```bash
python3 -c "import json,pathlib; print(json.load(open(pathlib.Path.home()/'.config/port-registry/listen.json'))['ui_url'])"
```

Then open that URL, claim or allocate a port range, and connect MCP (`tools/list`).

```bash
./scripts/doctor.sh       # version, listen, reachability, Session Handoff kit
./scripts/smoke_test.sh   # documented smoke + stdlib suite
```

Optional editable install:

```bash
pip install -e .
portskill --help
portskill-cli --help
```

## Surfaces

| Surface | Role | Entry |
|---------|------|-------|
| **UI** | HTML console on the sticky listen port | `./scripts/run.sh` or `python3 -m port_registry_app` |
| **MCP** | Same process: stdio or `POST /mcp` | `--mcp-stdio` or `mcp_url` from `listen.json` |
| **CLI** | Stdlib CLI (+ optional `skill/SKILL.md`) | `./scripts/cli.sh …` / `portskill-cli` |

One version string everywhere: `pyproject.toml` ↔ package `__version__` ↔ UI ↔ MCP `initialize` ↔ `doctor`.

## Sticky listen port

On launch the server chooses a bind port in this order:

1. Sticky port from `~/.config/port-registry/listen.json` (if still bindable)
2. Existing Portskill dogfood claim in the registry
3. Fresh allocate from the pool

After bind it rewrites `listen.json` with live URLs. **Always read that file** for current UI/MCP addresses.

Typical launch output shape (port varies):

- **UI:** `http://127.0.0.1:<port>/`
- **MCP:** `POST http://127.0.0.1:<port>/mcp` (JSON-RPC); `GET /mcp` discovery
- **listen.json:** `~/.config/port-registry/listen.json`
- **Registry:** `PORT_REGISTRY_PATH` or `~/.config/port-registry/registry.json`

Soft-restart (`./scripts/install-keepalive.sh stop` then `start`, or restart the module server) keeps the sticky port when bindable. If the old port cannot bind, Portskill allocates a new one and rewrites `listen.json` — refresh MCP clients that pinned the previous URL. Corrupt `listen.json` / `registry.json` fail closed (never wiped silently).

## Smoke test and CI

**Only documented entry:** `./scripts/smoke_test.sh` (sets `PYTHONPATH` and cwd). Do not run `python3 tests/smoke_test.py` alone on a cold clone.

```bash
./scripts/smoke_test.sh
```

Exit 0 on pass. GitHub Actions runs the same script on every push/PR to `main` (`.github/workflows/ci.yml`) — Linux offline.

## Doctor exit contract

`./scripts/doctor.sh` and `portskill-cli doctor` / `python3 -m port_registry_app.cli doctor` share one contract (the script `exec`s the CLI). JSON is always printed first; the process then exits.

| Exit | When |
|------|------|
| **0** | Healthy offline / default: registry absent (cold) or readable object; listen.json absent or valid; not listening, or listening and UI/MCP GET 200; skill files present; Session Handoff kit present. Non-loopback `bind_host` **with** `allow_non_loopback` recorded warns (`message` + `checks[].warning`, `ok: true`). Tailscale missing, placeholder start script, and default-state counts are informational. |
| **2** | Fail-closed: corrupt `registry.json` or `listen.json` (never wiped), missing skill files (`port_registry_app/{__init__,cli,server,mcp}.py` + `SKILL.md` + `ui/` or `static/`), `listening: true` with a missing UI/MCP URL or non-200 GET, non-loopback bind without `--allow-non-loopback`, or invalid Session Handoff kit override. Payload `status=error`, `reason=doctor_failed`. |

Doctor is **read-only and idempotent** — running it twice does not create, rewrite, or delete registry/listen files. Default bind remains loopback. Remotes remain HOLD.

## UI highlights

- **Compose** is first-class (topbar jump + MCP panel composer).
- **System tools**, **repo**, **Settings**, and **Serve URL** disclosures are **default-closed** (chevron + Show/Hide).
- **One workspace** — all services in a single implicit workspace. Export/Import Workspace stay in ⚙ Actions. Named presets remain CLI/MCP.
- **Defaults** — per-range Default On/Off; toolbar **Start Default Services** / activate via `apply-defaults`; deactivate keeps reserved unless `--also-release`.
- **⚙ Actions** — workspace bulk actions (start/stop default/all, export/import workspace).
- **require_compat** is always on (Settings checkbox locked).
- **Iterate Mode** (optional) — floating control for in-page chrome/token A/B; persist writes `port_registry_app/static/iterate-tokens.css`.

## Local trust / security

See **[SECURITY.md](SECURITY.md)** for reporting and trust boundaries.

- Default bind is `127.0.0.1`.
- HTTP mutating and inventory surfaces require `Authorization: Bearer` (even on loopback): `POST /mcp`, `GET /mcp`, `GET /api/state`, UI `/api/*` and `/port-registry/actions`. `GET /health` stays open as a liveness probe. Stdio MCP does **not** use this token.
- `--host` other than `127.0.0.1` / `::1` / `localhost` is **refused at start** unless you pass `--allow-non-loopback` (documented footgun; no allowlist). With the override, bearer auth stays mandatory (no open LAN dogfood); the UI banner/chip stays and `doctor` warns. Without the flag, `doctor` fails closed (exit 2) if `listen.json` still shows a non-loopback host.
- Prefer **stdio MCP** for agents (`--mcp-stdio` / `examples/mcp.stdio.json`). HTTP MCP is **local-trust dogfood only** and needs the local bearer. Tailscale Serve/Funnel is **not** authentication.
- Funnel of the Portskill listen/UI/MCP port is **refused in code**. Funnel on *user* claimed service ports stays a deliberate user action.
- Remote machines remain **HOLD** (not implemented).

**Distribution / code signing:** Portskill does not ship a notarized or signed binary. Personal Mac packaging is `./scripts/build-app.sh` plus `./scripts/install-keepalive.sh` (same `port_registry_app` under the app/CLI/MCP). App Store Connect / notarization remain HOLD. There is no friend installer or signed-app distribution path.

## Connect MCP

**Stdio is the preferred agent path** (Cursor / Claude / Codex). Copy `examples/mcp.stdio.json` or the stdio block in the UI MCP / Compose panel.

```json
{
  "mcpServers": {
    "portskill": {
      "command": "python3",
      "args": ["-m", "port_registry_app", "--mcp-stdio"],
      "env": { "PYTHONPATH": "/absolute/path/to/this/repo" }
    }
  }
}
```

**HTTP MCP (local-trust only):** same loopback listener as the UI — `Authorization: Bearer` required. Prefer stdio for agent install. If dogfooding HTTP: run the app, then `POST` JSON-RPC to the `mcp_url` from `listen.json` with the local token (also `GET /mcp` discovery). Do not Funnel the listen port; Tailscale is not authentication.

Tools: `allocate`, `activate`, `start`, `stop`, `release`, `status`, `doctor`, `environment_export`, `environment_import`, `set_default`, `apply_defaults`, `deactivate`, `compat_check`, `preset_save`, `preset_list`, `preset_apply`, `preset_delete`, `settings_get`, `settings_set`. (`exit_house` remains as a deactivate alias.)

Session Handoff (vendored kit, default on in `tools/list`): flat names `handoff_status`, `handoff_skill`, `handoff_template`, `handoff_list`, `handoff_resolve`, `handoff_new_path`, `handoff_resume`, `handoff_supersede`, `handoff_install_help` — not nested `session-handoff/*`. Ledger writes go only through `vendor/session-handoff-kit/codex/hooks/handoff_ledger.py`.

## CLI

Wrappers set `PYTHONPATH` (`./scripts/cli.sh`, `./scripts/doctor.sh`). After `pip install -e .`, use `portskill-cli …` the same way.

```bash
./scripts/cli.sh status
./scripts/doctor.sh
./scripts/cli.sh allocate --count 1 --tailnet none --project .
./scripts/cli.sh start --range-id <id> --project . --tailnet none
./scripts/cli.sh stop --range-id <id> --project .
./scripts/cli.sh release --range-id <id> --project .

./scripts/cli.sh set-default --range-id <id> --state on --project .
./scripts/cli.sh apply-defaults
./scripts/cli.sh deactivate
./scripts/cli.sh deactivate --also-release
./scripts/cli.sh compat check --preset ui-work --preset api-stack
./scripts/cli.sh preset list
./scripts/cli.sh settings get
./scripts/cli.sh http-auth show
./scripts/cli.sh http-auth regenerate
```

## HTTP bearer auth (developer)

Local high-entropy token at `~/.config/port-registry/http_auth.json` (minted on first HTTP serve or `http-auth show`). This is a developer-machine secret — not a friend/installer/signed-app distribution path.

```bash
./scripts/cli.sh http-auth show         # prints the token
./scripts/cli.sh http-auth regenerate   # invalidates the previous token
./scripts/doctor.sh                    # reports configured (token present) without printing the secret
```

- Required header: `Authorization: Bearer <token>`
- Fail closed: unauthenticated `POST /mcp` and protected `/api/*` return **401** with no tool side effects
- Required even on loopback; still required with `--allow-non-loopback`
- Stdio MCP (`--mcp-stdio`) is unchanged and does not read this token
- UI Settings can show/regenerate; unauthenticated `GET /` is a token prompt (no inventory)
- Passkey / OAuth is not in this cut

## Keep-alive (macOS, optional)

Personal up-to-date `Portskill.app` on this Mac: **keepalive + `build-app.sh` only**. LaunchAgent + Dock/menubar so the server survives Terminal close / login:

```bash
./scripts/build-app.sh                    # optional; keepalive install also builds when needed
./scripts/install-keepalive.sh install
./scripts/install-keepalive.sh status
./scripts/install-keepalive.sh stop
./scripts/install-keepalive.sh uninstall   # plist only; registry untouched
```

May build or reuse `dist/Portskill.app` via `build-app.sh`; writes `~/Library/LaunchAgents/local.portskill.plist`. Logs: `~/Library/Logs/Portskill/`. **Never** wipes `~/.config/port-registry/registry.json`.

## Remote machines

**HOLD** — not implemented. Do not expect add/remove machines or MCP `machine_*` in this cut. Keepalive, Dock/menubar, sticky listen, and port hyperlinks are local-only.

## Workspaces, presets, defaults

Each range stores additive `default_state` (`"off"` | `"on"`, missing ⇒ off).

- **Activate** = `apply-defaults` (start Default On; optional `--also-stop-off`)
- **Deactivate** = `deactivate` (aliases `exit-house` / `leave`) — safe stop; **keeps reserved** unless `--also-release`

**Named presets** live in `registry.json` under `presets`. File packs: `examples/environments/`.

Compatibility: `compat check` / `preset check` / `environment check` — exit 2 + JSON `conflicts` when not compatible. `require_compat` is always **on** (UI locked).

**⚙ Actions:** Start Default / Start All / Stop non-Default / Stop All / Export / Import Workspace. Import backs up to `~/.config/port-registry/backups/workspace-YYYYMMDD-HHMMSS.json` first.

**Session Handoff** (collapsed, next to MCP tools): enable/add, vendored kit status, install helpers per surface, and Write-a-Handoff skill download/upload. Custom skill persists as `settings.handoff_skill` pointing at `handoff-skill.md` beside the registry (`~/.config/port-registry/`). Kit lives at `vendor/session-handoff-kit/` (optional `PORTSKILL_HANDOFF_KIT` / `settings.handoff_kit` override). Flat tool-name copy lives in this section (`handoff_status`, `handoff_list`, … — not nested `session-handoff/*`).

**MCP tools** panel order: System tools → Agent connection (each setup instruction starts collapsed) → User commands → Command composer.

```bash
portskill-cli tailscale login
portskill-cli start --all
portskill-cli stop --all
portskill-cli stop --non-default
```

## Package layout

```text
port_registry_app/           # UI + MCP + CLI (compat module name)
scripts/                     # run, cli, doctor, smoke_test, keepalive, build helpers
macos/                       # Swift menu/Dock sources + launchd plist
examples/                    # MCP stdio + environment samples
ui/                          # portable preview + WIRING
skill/SKILL.md               # optional agent sidecar
tests/                       # smoke_test.py + test_*.py (via ./scripts/smoke_test.sh only)
vendor/session-handoff-kit/  # Session Handoff (tracked source, no submodule)
.github/workflows/ci.yml
SECURITY.md / CHANGELOG.md
pyproject.toml               # name: portskill
```

## Legacy entrypoints

Kept for transition; prefer `./scripts/run.sh` and `./scripts/cli.sh`.

| Path | Role |
|------|------|
| `install.sh` | Forwards to optional `scripts/install-skill.sh` only |
| `serve_ui.py` / `app.py` | Thin wrappers → `port_registry_app.server.main` |
| `port_registry.py` | Thin CLI wrapper |
| `port-registry` / `port-registry-app` | Console script aliases after pip install |
| `scripts/install-skill.sh` | Optional agent `SKILL.md` sidecar installer |

## Notes

- Stdlib-only **runtime** (pyproject metadata is fine for packaging).
- Live registry + sticky listen live under `~/.config/port-registry/` — never commit `.port-registry*` from a project tree.
- Corrupt registry JSON fails closed (`corrupt_registry`) — never silently wiped.
- `release` refuses with `process_still_running` if pid/pgid is live; use `stop` or `deactivate`.
- Exit-code-3 needs-input protocol + Tailnet modes: `skill/SKILL.md`.
- Does **not** rewrite book-port Roster console; no Electron.
- Changelog: [CHANGELOG.md](CHANGELOG.md).
