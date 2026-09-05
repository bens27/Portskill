# Portskill

**Portskill** — local port registry for humans and LLMs. Allocate and manage shared development ports with a light HTML UI, MCP tools, and a stdlib CLI. Same registry data store (`~/.config/port-registry/registry.json`); no second store; no Node / no pip packages required to *run*.

> Module path remains `port_registry_app` (compat). Product name is **Portskill**.

## Run (one entrypoint story)

Pick **one** of these — all start the same UI + MCP HTTP server:

1. **Double-click** `dist/Portskill.app` (primary Mac install; build with `./scripts/build-app.sh`)
2. **Module launch** from this package directory:
   ```bash
   PYTHONPATH=. python3 -m port_registry_app
   # --no-open skips browser; --no-auto-apply skips launch preset apply
   ```
3. **Keepalive** (LaunchAgent + Dock/menubar; survives Terminal close / login):
   ```bash
   ./scripts/install-keepalive.sh install
   ./scripts/install-keepalive.sh status
   ```

Optional after `pip install -e .`: console scripts `portskill` / `portskill-cli` (stdlib at runtime).

### Sticky listen port (not hard-coded 8765)

On launch the server chooses a bind port in this order:

1. Sticky port from `~/.config/port-registry/listen.json` (if still bindable)
2. Existing Portskill dogfood claim in the registry
3. Fresh allocate from the pool

After bind it rewrites `listen.json` with live URLs. **Read that file** for the current UI/MCP addresses — do not assume `:8765`. Historical default was `127.0.0.1:8765` only when nothing sticky/claimed.

Printed on launch (example shape; port varies):

- **Portskill UI:** `http://127.0.0.1:<port>/`
- **Portskill MCP:** `POST http://127.0.0.1:<port>/mcp` (JSON-RPC); `GET /mcp` discovery
- **listen.json:** `~/.config/port-registry/listen.json`
- **Registry:** `PORT_REGISTRY_PATH` or `~/.config/port-registry/registry.json`

```bash
# Discover current URLs
python3 -c "import json,pathlib; print(json.load(open(pathlib.Path.home()/'.config/port-registry/listen.json')))"
```

## Smoke test

Non-destructive check (import + CLI + optional HTTP if server already up):

```bash
./scripts/smoke_test.sh
# or: PYTHONPATH=. python3 tests/smoke_test.py
```

Exit 0 on pass.

## UI highlights

- **Workspaces · Coming soon** — runtime is one workspace (all services). Multi-workspace switching held; CLI/MCP presets still work.
- **Defaults** — per-range Default On/Off; toolbar **Start Default Services** / activate via `apply-defaults`; deactivate keeps reserved unless `--also-release`.
- **⚙ Actions** panel — workspace bulk actions (start/stop default/all, export/import workspace).
- **Remotes · Coming soon** — Settings stub only; HOLD (no machine add/remove this cut).


## Local trust / security

- Default bind is `127.0.0.1`.
- The same **unauthenticated** listener serves the UI, `GET /api/state`, and mutating `POST /mcp` (including `set_tailnet` with `funnel`).
- `--host` can widen exposure with **no allowlist** — do not use `0.0.0.0` casually.
- Prefer **stdio MCP** for agents (`--mcp-stdio` / `examples/mcp.stdio.json`). HTTP MCP is **local-trust dogfood only**.
- Never Tailscale Funnel the Portskill listen/UI port without explicit Ben OK. Funnel on *user* services is a separate deliberate choice.
- **Remotes · Coming soon** — HOLD (no remote machine registry this cut).

## Connect MCP

**Stdio (preferred for agents)** — Cursor / Claude / Codex. See `examples/mcp.stdio.json`:

```json
{
  "mcpServers": {
    "portskill": {
      "command": "python3",
      "args": ["-m", "port_registry_app", "--mcp-stdio"],
      "env": { "PYTHONPATH": "/absolute/path/to/this/package" }
    }
  }
}
```

**HTTP MCP (local-trust only):** same loopback listener as the UI — unauthenticated. Prefer stdio for agent install. If dogfooding HTTP: run the app, then `POST` JSON-RPC to the `mcp_url` from `listen.json` (also `GET /mcp` discovery). Do not expose this port on LAN/`0.0.0.0` or Funnel it.

Tools: `allocate`, `activate`, `start`, `stop`, `release`, `status`, `doctor`, `environment_export`, `environment_import`, `set_default`, `apply_defaults`, `deactivate`, `compat_check`, `preset_save`, `preset_list`, `preset_apply`, `preset_delete`, `settings_get`, `settings_set`. (`exit_house` remains as a deactivate alias.)

## CLI

```bash
PYTHONPATH=. python3 -m port_registry_app.cli status
PYTHONPATH=. python3 -m port_registry_app.cli doctor
PYTHONPATH=. python3 -m port_registry_app.cli allocate --count 1 --tailnet none --project .
PYTHONPATH=. python3 -m port_registry_app.cli start --range-id <id> --project . --tailnet none
PYTHONPATH=. python3 -m port_registry_app.cli stop --range-id <id> --project .
PYTHONPATH=. python3 -m port_registry_app.cli release --range-id <id> --project .

# Defaults / environments
PYTHONPATH=. python3 -m port_registry_app.cli set-default --range-id <id> --state on --project .
PYTHONPATH=. python3 -m port_registry_app.cli apply-defaults
PYTHONPATH=. python3 -m port_registry_app.cli deactivate
PYTHONPATH=. python3 -m port_registry_app.cli deactivate --also-release
PYTHONPATH=. python3 -m port_registry_app.cli compat check --preset ui-work --preset api-stack
PYTHONPATH=. python3 -m port_registry_app.cli preset list
PYTHONPATH=. python3 -m port_registry_app.cli settings get
```

After `pip install -e .`, use `portskill-cli …` the same way.

## Light UI

Default app launch serves the Portskill console (sidebar **PORTSKILL**, range cards, **Start / Stop / Release**, **Default On/Off**, **⚙ Actions**, compatibility warnings, export/import, presets + settings). Static sample: `ui/port-registry-preview.html`. Host wiring: `ui/WIRING.md`.

### Iterate Mode (optional)

Floating **Iterate** button (bottom-right) for in-page chrome/token A/B. Persist writes `port_registry_app/static/iterate-tokens.css`. Collab inbox: `~/.config/port-registry/collab/inbox.jsonl`.

## Keep-alive (macOS LaunchAgent + Dock/menubar)

```bash
./scripts/install-keepalive.sh install
./scripts/install-keepalive.sh status
./scripts/install-keepalive.sh stop
./scripts/install-keepalive.sh uninstall   # plist only; registry untouched
```

Builds/uses `dist/Portskill.app` when possible; writes `~/Library/LaunchAgents/local.portskill.plist`. Logs: `~/Library/Logs/Portskill/`. **Never** wipes `~/.config/port-registry/registry.json`. No `kill -9` of the LaunchAgent from tidy scripts.

## Remote machines

**Coming soon.** Remote machine registry (add/remove, MCP `machine_*`) is held. This cut: keepalive, Dock/menubar, sticky listen, port hyperlinks only.

## Workspaces, presets, defaults, bulk start/stop

Each range stores additive `default_state` (`"off"` | `"on"`, missing ⇒ off).

- **Activate** = `apply-defaults` (start Default On; optional `--also-stop-off`)
- **Deactivate** = `deactivate` (aliases `exit-house` / `leave`) — safe stop; **keeps reserved** unless `--also-release`

**Named presets** live in `registry.json` under `presets`. File packs: `examples/environments/`.

### Compatibility

- `compat check` / `preset check` / `environment check` — exit 2 + JSON `conflicts` when not compatible.
- `require_compat` is always **on** (UI locked).

### UI details

- Sidebar **Workspaces · Coming soon** (no tabs / New / Saved list).
- **⚙ Actions**: Start Default / Start All / Stop non-Default / Stop All / Export / Import Workspace.
- Import backs up to `~/.config/port-registry/backups/workspace-YYYYMMDD-HHMMSS.json` first.
- Per-range **Default** and **Tailscale Serve** switches; Browser Login required before Serve on.
- **Remote machines · Coming soon** stub in Settings. Remotes HOLD.

```bash
portskill-cli tailscale login
portskill-cli start --all
portskill-cli stop --all
portskill-cli stop --non-default
```

## Package layout

```text
port_registry_app/     # UI + MCP + CLI (compat module name)
dist/Portskill.app/    # primary double-click bundle (after build-app.sh)
scripts/               # build-app, install-keepalive, smoke_test, …
macos/                 # Swift menu/Dock sources + launchd plist
examples/              # MCP stdio + environment samples
ui/                    # portable preview + WIRING
skill/SKILL.md         # optional agent sidecar
tests/smoke_test.py    # stdlib smoke (also scripts/smoke_test.sh)
pyproject.toml         # name: portskill
```

## Legacy (optional — kept, not primary)

These still work during transition; prefer the Run section above.

| Path | Role |
|------|------|
| `install.sh` | Forwards to optional `scripts/install-skill.sh` only |
| `serve_ui.py` | Thin wrapper → `port_registry_app.server.main` |
| `app.py` | Thin wrapper → same |
| `port_registry.py` | Thin CLI wrapper |
| `port-registry` / `port-registry-app` | Console script aliases after pip install |
| `scripts/install-skill.sh` | Optional agent `SKILL.md` sidecar installer |
| `macos/Portskill.app` | Dev/fallback bundle if `dist/` not built |

## Notes

- Stdlib-only **runtime** (pyproject metadata is fine for packaging).
- Live registry + sticky listen live under `~/.config/port-registry/` — never commit `.port-registry*` from a project tree.
- Corrupt registry JSON fails closed (`corrupt_registry`) — never silently wiped.
- `release` refuses with `process_still_running` if pid/pgid is live; use `stop` or `deactivate`.
- Exit-code-3 needs-input protocol + Tailnet modes: `skill/SKILL.md`.
- Does **not** rewrite book-port Roster console; no Home/Fly; no Electron.
