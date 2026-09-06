<!-- Moved to skill/SKILL.md — optional agent sidecar. Product is Portskill / Port Registry app. -->
---
name: port-registry
description: Coordinate shared local development ports and managed project services with Portskill (`portskill` / `python3 -m port_registry_app` / `portskill-cli`) — light UI + MCP + CLI. The MCP `portskill` tool is one MCP tool for your agent to handle all port management functions. Use when an agent needs to allocate, start, stop, release, set-default, apply-defaults, preset save/list/apply/delete, settings get/set, environment export/import, inspect, doctor, or troubleshoot ports; open the Portskill console UI; avoid collisions between agent sessions; run a project's `.port-registry/start.sh` or `stop.sh`; expose a service with Tailscale Serve or Funnel; offer or launch Roster's seeded prototype frontend as a private Tailscale preview; answer requests such as "get me a port", "show port status", "show port registry UI", "open ports console", "connect MCP to port registry", "start my project service", "show me the Roster frontend", or "make this reachable on my tailnet"; or handle the CLI's exit-code-3 human-input protocol.
---

# Portskill

> Product name: **Portskill**. Skill id remains `port-registry` for install-path stability.

Use the `port_registry.py` CLI from this skill when coordinating local development ports. It maintains a shared registry for durable allocation decisions and a project-local mirror so future agents can rediscover the ports associated with the current repo.

This skill is runtime-agnostic: any LLM agent (Claude Code, Codex, Cursor/Grok Bot workflows, or plain CLI) can follow the same contract below. Prefer the runnable app (not an installer drop):

```bash
# App (UI + MCP HTTP)
python3 -m port_registry_app
# or: portskill  (legacy: port-registry-app)

# CLI
port-registry <subcommand> [flags]
# or: python3 -m port_registry_app.cli <subcommand> [flags]
```

If this skill sidecar is installed under `~/.agents/skills/port-registry/`, you can also use the thin wrappers there (`port_registry.py`, `app.py`) which call the same package.


## Light UI + MCP app (stdlib)

Usual entrypoint: `python3 -m port_registry_app` — one small stdlib process that serves the HTML Portskill UI **and** an MCP server wrapping the same registry schema/CLI (no second registry). On macOS you can also double-click `macos/Portskill.app`. Humans use the HTML UI for maintenance and defaults. Agents auto-invoke registry lifecycle tools.

When the user asks to "show port registry UI", "open ports console", or to attach an MCP-capable agent to the registry:

```bash
# Default: HTML UI + HTTP MCP; port from sticky listen.json or a registry allocate (historical :8765)
python3 -m port_registry_app
# optional: python3 -m port_registry_app --port 8765 --no-open

# Available: MCP over stdio (Cursor / Claude / Codex attach)
python3 -m port_registry_app --mcp-stdio
# or: portskill  (legacy: port-registry-app) --mcp-stdio
```

- UI: open the printed URL (default `http://127.0.0.1:8765/`). Mirrors the Roster Port Registry view (sidebar, stats, range cards, Start/Stop/Release, Default On/Off, Export/Import environment, Apply defaults). Actions POST to `/port-registry/actions`.
- MCP HTTP: uses the same local listener as the UI — `POST http://127.0.0.1:<port>/mcp` JSON-RPC; `GET /mcp` discovery. Access does not require a token unless the optional passkey gate is enabled. Sharing Portskill’s listen port with Tailscale Serve or Funnel is not a substitute for authentication, and Funnel of Portskill’s own listen port is blocked. Do not bind `0.0.0.0` casually.
- MCP stdio (available agent install option): newline-delimited JSON-RPC on stdin/stdout — `python3 -m port_registry_app --mcp-stdio` (see `examples/mcp.stdio.json`).

`portskill` is one MCP tool for your agent to handle all port management functions (`mode` start|stop|release|restart|status). Happy-path peers are `start`, `stop`, `release`, and `status`. `allocate` remains available. `activate` is an internal primitive (off in lean; `settings set --mcp-tool activate=on`). Other tools map to CLI subcommands: `doctor`, `environment_export`, `environment_import`, `set_default`, `apply_defaults` (Start Default Services), `deactivate` (safe stop, keep reserved; `exit_house` alias), `compat_check`, presets/settings tools. The orchestrator returns `{ran, skipped, result, needs_input?}`; skips are inspectable. Exit-code-3 needs_input (Tailnet) is returned as a tool error with structured `needs_input` / `prompt` / `options` / `resume_hint` so the agent can resume. `stop` honors `settings.stop_also_release` (default true).

Named `settings.mcp_tools` profiles (stored as `settings.mcp_tools_profile`, applied into the existing `settings.mcp_tools` enable map; missing key = enabled):

- **`full`** (default) — every system tool and `handoff_*` tool is listed. Existing installs stay here until you opt in.
- **`lean`** — enables `portskill`, `status`, `settings_get`, plus escape hatches `allocate` / `stop` / `release`. Rarely used CRUD (`activate`, `start`, `doctor`, environment/preset/history/ports/tailscale helpers, `settings_set`, …) and flat `handoff_*` tools stay off until toggled (`settings set --mcp-tool NAME=on`).

Switch: `settings set --mcp-tools-profile lean|full` (CLI) or MCP `settings_set` with `mcp_tools_profile`. After `lean`, `settings_set` itself is hidden until you re-enable it or apply `full` from the CLI. Individual `--mcp-tool` / HTML toggles still work; no UI reorder required.

Thin wrappers `app.py` / `serve_ui.py` / `port_registry.py` remain for transition; prefer `portskill` / `python3 -m port_registry_app` and `portskill-cli` (legacy: `port-registry`).

For a static sample only (no live registry):

```bash
open ui/port-registry-preview.html
# (or ~/.agents/skills/port-registry/ui/… if using the optional skill sidecar install)
```

Portable TypeScript modules for hosts wiring into a larger console live under `ui/` — see `ui/WIRING.md`. CLI contract and registry schema are unchanged.


## Local trust / security

- Default bind is `127.0.0.1`.
- Local HTTP UI and ordinary HTTP APIs (`GET /`, `GET /api/state`, `POST /mcp`) open without `Authorization: Bearer` unless the **opt-in** passkey gate is on (`http-auth gate on`). Default is off. When on, those routes need a passkey session cookie or the optional bearer. `http-auth` / `http_auth.json` remain optional helpers. Stdio MCP does not use a token or passkey.
- `--host` other than loopback is **refused** unless `--allow-non-loopback` (documented footgun; no allowlist). Do not use `0.0.0.0` casually.
- Agents auto-invoke registry lifecycle tools. Humans use the HTML UI for maintenance and defaults. HTTP MCP uses the same local listener as the UI. Sharing Portskill’s listen port with Tailscale Serve or Funnel is not authentication. Stdio MCP (`--mcp-stdio` / `examples/mcp.stdio.json`) is an available agent install option.
- Funnel of the Portskill listen/UI port is refused in code. Funnel on user services is a separate deliberate choice.
- **Remotes** — HOLD (not implemented).

## Registry Files

The global registry lives at:

```text
~/.config/port-registry/registry.json
```

Each project may also have a local mirror:

```text
.port-registry.json
```

Treat the global registry as the source of truth. Treat the local mirror as a convenience file for project context, status checks, and handoff between agent sessions.

Additive root keys (normalize if missing; never wipe `projects`): `presets` (named environments) and `settings` (`auto_apply_preset`, `auto_apply_on_launch`, optional `auto_exit_on_shutdown`, `stop_also_release`, `mcp_tools`, `mcp_tools_profile`).

## CLI Commands

Invoke the CLI with Python 3 (stdlib only; no Node required for the registry itself):

```bash
python3 path/to/port_registry.py <subcommand> [flags]
```

### `allocate`

Reserve one or more free ports for a project. Use this before starting new local services so separate agents do not choose the same port. Returns a new range (with its own `id`) on success.

Key flags:

- `--count N` (required): Number of ports to allocate.
- `--project PATH`: Project directory (defaults to the current directory, resolved to an absolute path — this is what the registry keys reservations by, not a free-text name).
- `--note TEXT`: Optional free-text note stored on the range (e.g. "web server").
- `--tailnet serve|funnel|none`: Desired Tailnet exposure mode. Omit it to be asked (see the Exit-3 Needs-Input Protocol below).

### `activate` (internal primitive)

Mark an allocated range as actively serving a process without running `start.sh`. This is not the happy path. Prefer `start` or the `portskill` orchestrator. The orchestrator may call this internally. MCP `activate` is off in the lean profile; enable it with `settings set --mcp-tool activate=on`.

Key flags:

- `--range-id ID` (required): The range returned by `allocate`.
- `--project PATH`: Project directory (defaults to the current directory).
- `--tailnet serve|funnel|none`: Only needed if no Tailnet mode was already recorded at `allocate` time — otherwise the recorded mode is honored automatically without asking again. Omit it (when nothing was recorded) to be asked.

### `start`

Run the project's `.port-registry/start.sh` script as a detached background process for an allocated range, then do everything `activate` does. Use this when the project has a prepared start hook and you want the registry to track the launched service lifecycle.

`start` runs the script in its own process group so the launched process and any child processes it spawns can be cleaned up reliably by `stop`. It logs script output to `.port-registry/start.log`, marks the range active, and handles the Tailnet exposure question exactly as `activate` does. Calling `start` again on an already-running range is a safe no-op, not an error.

If `start` exits with code `3` to ask for Tailnet exposure, follow the Exit-3 Needs-Input Protocol below and re-invoke `start` with the reported resume flag.

Key flags:

- `--range-id ID` (required): The range returned by `allocate`.
- `--project PATH`: Project directory (defaults to the current directory).

### `release`

Free a range when the service is already stopped (or never started) and the project no longer needs the reservation. Tears down any live Tailnet `serve`/`funnel` mapping for that range first. Use this during cleanup so future work can reuse the ports.

**Do not use `release` to stop a running service.** If the range still has a live `pid`/`pgid`, `release` refuses with reason `process_still_running` and tells you to use `stop` instead (safer than orphaning a process). Prefer `stop` whenever the service may still be running; it terminates the process group, then releases.

Key flags:

- `--range-id ID` (required): The range to release.
- `--project PATH`: Project directory (defaults to the current directory).

### `stop`

Run the project's `.port-registry/stop.sh` cleanup hook if it exists and forcibly terminate the tracked process group for the range. Whether the range is then released follows `settings.stop_also_release` (default true, matching historic stop). When that setting is false, the range stays reserved — same idea as `deactivate` without `--also-release` — and `release` is the explicit free.

`stop` treats the cleanup hook as best effort and logs it to `.port-registry/stop.log`. After the hook, it terminates the tracked process and anything it spawned via its process group, and tears down any live Tailnet `serve`/`funnel` mapping. Optional `--also-release on|off` overrides the setting for one call. Calling `stop` again on an already-stopped or already-released range is a safe no-op.

Key flags:

- `--range-id ID` (required): The range to stop and release.
- `--project PATH`: Project directory (defaults to the current directory).

### `status`

Inspect the current registry state. Use this to answer which ports are allocated, active, attached to a project, or configured for Tailnet exposure.

Key flags:

- `--project PATH`: Show ranges for one project directory. Omit to show every project's ranges.

### `path` / MCP `portskill`

`portskill` is one MCP tool for your agent to handle all port management functions. CLI remains `path --mode …` (`portskill-path` / `portskill_path` aliases). Modes: `start` | `stop` | `release` | `restart` | `status`.

`start` phases (server-side): allocate → wire (defaults/commands if needed) → activate → start → optional Tailnet Serve of **user** service ports. Never Funnels the Portskill listen port. Returns `{ran, skipped, result, needs_input?}`. Skips are deterministic (already reserved/active/running, Tailnet not requested / not logged in, Funnel-of-listen). `stop` honors `settings.stop_also_release`. `settings.mcp_tools.portskill` can hide the tool; an older `portskill_path: false` key still hides it.

If Tailnet Serve is requested and login is required, the path completes earlier phases, skips Tailnet, and exits 3 with `needs_input` so you can resume after `tailscale login`.

### `doctor`

Run environment checks and report JSON. Prefer `./scripts/doctor.sh` (same exit codes as this CLI). Use this when install looks wrong, Tailscale cannot be found, the registry file may be corrupt, or start scripts are still placeholders.

**Exit contract** (idempotent, never wipes files):

- **0** — healthy offline / default bind. Absent registry or listen is OK (cold). Non-loopback `bind_host` with `allow_non_loopback` recorded warns only (`message` + `warning`, still exit 0). Tailscale / start-script / `default_state` are informational.
- **2** — fail-closed: corrupt `registry.json` or `listen.json`, missing skill files, `listening: true` but UI/MCP URL missing or GET ≠ 200, non-loopback bind without `--allow-non-loopback`, or invalid Session Handoff kit override. `status=error`, `reason=doctor_failed`.

Checks:

- Registry path exists and parses as a JSON object (fail-closed: corrupt files are reported; never wiped)
- Listen path / sticky `listen.json` (fail-closed if corrupt)
- Bind host: loopback default; off-loopback fails closed unless `--allow-non-loopback` is recorded (then warns)
- Session Handoff kit present/configured (fail-closed on a bad override)
- UI / MCP reachability (skipped when not listening; fail-closed when listening but unreachable)
- Tailscale binary resolution: `PORT_REGISTRY_TAILSCALE_BIN` → `tailscale` on `PATH` → Mac app bundle default (informational)
- Skill files present beside this CLI (`port_registry_app/{__init__,cli,server,mcp}.py`, `skill/SKILL.md` or `SKILL.md`, `ui/` or `port_registry_app/static/`)
- Placeholder detection for `.port-registry/start.sh` under `--project` (defaults to cwd)
- `default_state` counts for non-released ranges (`on` / `off`)

Key flags:

- `--project PATH`: Project directory for start-script check (defaults to the current directory).


### History (Modified / Reset)

Per focused environment, Portskill keeps an undo stack (`registry.environment_history`, not `sessions.history`):

- UI: amber **Modified** + **Reset**, collapsible **History** (Original + steps)
- CLI: `history list|restore --index N|reset|clear [--environment NAME]`
- MCP: `history_list`, `history_restore`, `history_reset`
- Pushed after allocate/release/set-default/set-tailnet/start/stop/environment import/preset save (when focused or `--environment`)

### `set-default` / `apply-defaults` / `environment` / `preset` / `settings`

- `set-default --range-id ID --state on|off [--project PATH]` — per-range `default_state` (missing key ⇒ off).
- `apply-defaults [--project PATH]...` — Start Default Services: start non-released ranges with `default_state=on`. Add `--also-stop-off` to stop Off ones that are running.
- `settings set --stop-also-release on|off` — when off, `stop` keeps the range reserved.
- `environment export --name NAME [--out PATH] [--project PATH]... [--from-preset NAME]` — portable JSON (`kind: port-registry-environment`); `--from-preset` exports a saved in-registry preset as a file pack.
- `environment import --file PATH [--apply-defaults]` — create/reuse allocations by note+project, set defaults; does not auto-start unless flagged.
- `preset save --name NAME [--description TEXT] [--project PATH]... [--from-file PATH]` — snapshot live non-released ranges (or load from environment file) into `registry.presets[NAME]`.
- `preset list` / `preset show --name NAME` / `preset delete --name NAME`
- `preset apply --name NAME [--also-stop-off]` — import preset services into live ranges + apply-defaults.
- `settings get` — read auto-apply (and related) settings, including `mcp_tools` and `mcp_tools_profile`.
- `settings set --auto-apply-preset NAME|none` / `--auto-apply-on-launch on|off` / shortcut `--auto-apply NAME|off`.
- `settings set --mcp-tools-profile lean|full` — apply the named MCP tools profile into `settings.mcp_tools` (opt-in; default remains `full`). `--mcp-tool NAME=on|off` still toggles one tool after a profile apply.
- App launch auto-applies when enabled; `--no-auto-apply` skips once. Placeholder start failures are warnings — the server keeps running.

Sample: `examples/environments/ui-work.sample.json`.


## Project Start/Stop Scripts

`allocate` scaffolds `.port-registry/start.sh` and `.port-registry/stop.sh` the first time it allocates a range for a project. These are ordinary shell scripts for the user or agent to edit; they may be multi-line and multi-step, and should contain whatever commands actually launch or clean up the project's dev server or service.

The scaffolded `start.sh` is only a placeholder and intentionally refuses to run loudly until edited. Do not expect `start` to launch anything useful until `.port-registry/start.sh` has been customized for the project. The scaffolded `stop.sh` is likewise a placeholder hook for project-specific cleanup before the registry forcibly terminates the tracked process group.

## Optional Roster Frontend Preview

When the target is a Roster checkout containing `server.ts`, `seed-data.ts`, and a customized `.port-registry/start.sh`, offer the user this choice before launching anything:

```text
Launch the Roster frontend with its current seeded prototype data and expose it privately with Tailscale Serve?
```

Offer only `launch` and `skip`. Continue the user's original task after `skip`; do not keep prompting. On `launch`:

1. Run `status --project <roster-path>` and reuse a suitable unreleased range when possible; otherwise allocate one port with `--tailnet serve` and a note identifying it as the Roster seeded frontend preview.
2. Inspect `.port-registry/start.sh`. Preserve a customized hook. If it is still the scaffolded placeholder, replace it with a project-local command that sets `PORT_REGISTRY_SERVER_PORT` to the allocated range's first port and executes `node --import tsx server.ts` from the Roster root.
3. Set `ROSTER_EVENT_LOG_PATH` in the hook to `.port-registry/roster-preview-event-log.json`. Before starting a requested preview, remove only that preview-specific file so the server rebuilds it from the current `seed-data.ts`. Never delete the default `~/.config/roster-console/event-log.json` as part of this preview flow.
4. Run `start --range-id <id> --project <roster-path> --tailnet serve`. Use `serve`, never `funnel`, because this preview is private by default.
5. Verify the local HTTP endpoint responds, inspect Tailscale Serve status, and report both the local URL and the private Tailnet URL. If either check fails, report the failure and leave the other working endpoint accurately described.

Describe the preview as seeded prototype data: some Roster sections may already be event-backed while unfinished sections still render static mockup content. Do not imply that the whole console is production-backed.

## Tailnet Modes

When asked for Tailnet exposure, use exactly one of these answers:

- `serve`: Private, tailnet-only exposure through Tailscale Serve.
- `funnel`: Public internet exposure through Tailscale Funnel.
- `none`: No Tailnet exposure.

Do not substitute alternate words such as "private", "public", "tailscale", or "no".

## Exit-3 Needs-Input Protocol

Some commands may need an explicit user choice before the CLI can continue. When a `port_registry.py` command exits with exit code `3`, follow these steps exactly:

1. Read the JSON object on stdout.
2. Relay the JSON object's `prompt` field to the user verbatim as the question text.
3. Present the JSON object's `options` field as the complete set of allowed choices.
4. Do not invent, summarize, rewrite, or embellish the prompt or options.
5. After the user answers, re-invoke the identical original command and append the user's answer using the flag named in the JSON object's `resume_hint` field.

For example, if stdout contains:

```json
{
  "prompt": "Expose this service on the Tailnet?",
  "options": ["serve", "funnel", "none"],
  "resume_hint": "--tailnet"
}
```

Ask the user exactly:

```text
Expose this service on the Tailnet?
```

Offer `serve`, `funnel`, and `none` as the choices. Then re-invoke the same command with the selected value appended as `--tailnet <answer>`.

The `resume_hint` field is the flag name only (for Tailnet prompts it is exactly `--tailnet`); append the user's answer as `--tailnet <answer>`.

Never guess the answer and never create your own HITL prompt text.
