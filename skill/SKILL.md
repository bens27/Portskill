---
name: port-registry
description: Manage shared local development ports and project services with Portskill. Use to prevent port collisions, start or stop services, inspect the registry, connect its UI or MCP, or configure Tailscale exposure.
---

# Portskill

The skill ID remains `port-registry` for install compatibility. Use the connected
`portskill` MCP tool for lifecycle operations, or the CLI below.

## Entrypoints

```bash
python3 -m port_registry_app              # HTML UI + HTTP MCP
python3 -m port_registry_app --mcp-stdio  # stdio MCP
python3 -m port_registry_app.cli <subcommand> [flags]
```

Installed commands are `portskill` (app) and `portskill-cli` (CLI). In a source
checkout, run from its root; an installed sidecar also provides `port_registry.py`
and `app.py` wrappers at its root. Use absolute wrapper paths when working in
another project, and pass that project's path explicitly.

The HTML UI and HTTP MCP share the same local listener. Open the printed URL or
read `ui_url` / `mcp_url` from `~/.config/port-registry/listen.json`; the port is
allocated and reused, so do not assume `8765`.

## Service workflow

1. Inspect `status --project PATH` and reuse a suitable unreleased range.
   Projects are keyed by resolved directory path, not a free-text name.
2. Prefer MCP `portskill` with `mode: start|stop|release|restart|status`, or CLI
   `path --mode MODE --project PATH`. Inspect `ran`, `skipped`, `result`, and
   `needs_input` in the response; partial completion is not full success.
3. For individual operations, `allocate --count N --project PATH` returns a
   range `id`; pass it as `--range-id ID` to `start`, `stop`, or `release`.
   Reserve ports before launching services to avoid collisions between agents.
4. Before starting, inspect the configured command or `.port-registry/start.sh`.
   Allocation scaffolds hooks, but the start placeholder refuses to launch.
   Preserve customized hooks and configure the service to use its assigned port.
5. Verify the service endpoint and registry status before reporting it running.
   Start/stop logs are in `.port-registry/start.log` and `stop.log`.

`stop` terminates the tracked process group and removes Tailnet mappings. It also
releases the range by default (`settings.stop_also_release`); pass
`--also-release off` to retain the reservation. `release` only frees a stopped or
unused range and refuses a live process with `process_still_running`.
`activate` marks a range active without launching it; use it only when needed
for an externally started service. Repeated start/stop calls are safe no-ops
when already in the requested state.

## Exposure and missing input

Use `--tailnet serve` for private Tailnet exposure, `funnel` for public internet
exposure, and `none` for no exposure. Honor an explicit user choice or recorded
range mode; do not invent an exposure choice.

On CLI exit `3` or MCP structured `needs_input`, read the returned JSON. If the
required choice is not already authorized, relay `prompt` and `options` verbatim
and wait for the answer. Resume the same operation using `resume_hint`, which is
a flag name (for example, `--tailnet`), with the selected value appended. If login
is required, complete that prerequisite before retrying; earlier phases may
already have succeeded.

The default bind is `127.0.0.1`; non-loopback requires `--allow-non-loopback`.
HTTP access is open unless the optional passkey gate is enabled. Tailscale sharing
is not authentication. Funnel of Portskill's own listen port is blocked; Serve
of that port defaults off. Stdio MCP does not use HTTP authentication.

## State and conditional guidance

`~/.config/port-registry/registry.json` is authoritative; `.port-registry.json`
is an optional project mirror. Use the tools to update state. Report corrupt
registry/listen files without overwriting them.

Read only the reference needed for the task:

- [Administration](references/administration.md): MCP profiles, defaults, presets,
  environment import/export, history, diagnostics, and experimental Handoff.
- [Roster preview](references/roster-preview.md): when the user requests Roster's
  seeded frontend preview, including its isolated preview data and private URL.

Use CLI `<subcommand> --help` or the connected MCP tool schema for exact arguments.
