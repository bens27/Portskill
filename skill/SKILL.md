---
name: portskill
description: Manage shared local development ports and project services with Portskill. Use when the user asks to register a port, reserve or claim a port, register, host, serve, run or expose a project on Portskill or over Tailscale, to prevent port collisions, start or stop services, inspect the registry, connect its UI or MCP, or configure Tailscale exposure.
---

# Portskill

Use the connected
`portskill` MCP tool for lifecycle operations, or the CLI below.

## Entrypoints

```bash
python3 -m portskill              # HTML UI + HTTP MCP
python3 -m portskill --mcp-stdio  # stdio MCP
python3 -m portskill.cli <subcommand> [flags]
```

A pip install puts `portskill` (app) and `portskill-cli` (CLI) on PATH. When
`command -v portskill-cli` finds nothing, use the first that exists:

1. The sidecar wrapper beside this skill: `python3 <sidecar>/portskill_cli.py`,
   where `<sidecar>` is the directory holding the top-level `SKILL.md`, the parent
   of this `skill/` directory. `scripts/install-skill.sh` installs it at
   `${PORTSKILL_SKILL_DIR:-$HOME/.agents/skills/portskill}`; `~/.claude/skills/portskill`
   links there. The wrapper runs from any directory.
2. A source checkout: `<checkout>/scripts/cli.sh` (or
   `python3 <checkout>/portskill_cli.py`), from any directory. The checkout
   location varies by machine, for example `~/Development/Portskill`. To locate one,
   look for a directory holding both `portskill/cli.py` and `scripts/cli.sh`.
3. Fallback: `cd <checkout or sidecar> && python3 -m portskill.cli ...`, or run it
   from anywhere with `PYTHONPATH=<checkout or sidecar>`. Without either, `-m`
   fails with `No module named portskill`.

These run the same CLI and read the same registry. Pass the target project as an
absolute path, for example `--project "$(pwd)"`. `scripts/cli.sh` and option 3
change directory first, so a relative `--project .` names the wrong project.

The HTML UI and HTTP MCP share the same local listener. Open the printed URL or
read `ui_url` / `mcp_url` from `~/.config/portskill/listen.json`; the port is
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
4. Before starting, inspect the configured command or `.portskill/start.sh`.
   Allocation scaffolds hooks, but the start placeholder refuses to launch.
   Preserve customized hooks and configure the service to use its assigned port.
5. Verify the service endpoint and registry status before reporting it running.
   Start/stop logs are in `.portskill/start.log` and `stop.log`.

`stop` terminates the tracked process group and removes Tailnet mappings. It also
releases the range by default (`settings.stop_also_release`); pass
`--also-release off` to retain the reservation. `release` only frees a stopped or
unused range and refuses a live process with `process_still_running`.
`activate` marks a range active without launching it; use it only when needed
for an externally started service. Repeated start/stop calls are safe no-ops
when already in the requested state.

## Finding registered services

Plain `status` covers the project named by the current directory's
`.portskill.json`, or every project when there is none. `status --all` (MCP
`status` with `all: true`) always covers every registered project; it cannot be
combined with `--project`.

`status --find QUERY` (MCP `find`) searches every project, or only `--project`
when given. Text matches, case-insensitively, anywhere in the range `note` or
in its names: the project directory name and the range id. A number from 1 to
65535 is also a port and matches every range whose `start..end` contains it,
not only the start port; `--find 2024` therefore finds port 2024 and notes
mentioning 2024. Digit-only queries skip range ids (they are timestamps), and
numbers outside 1..65535 search text only. The result is a normal status
payload holding only the matching ranges; each one also carries `project` and
`matched` (`note`, `name` or `port`), and the payload adds `query` and
`matches` (0 when nothing matches). Both options are read-only and observe
ranges exactly like `status`.

## Drift and stale ranges

`status` never repairs the registry. Every range carries `drift` (null when
consistent); a local active range whose recorded state disagrees with reality
gets `{reasons, stale, hint}`, and the result gets a top-level `drift` summary.
Reasons: `pid_dead` (recorded process gone), `no_pid` (nothing tracked),
`port_unbound` (no listener on any port of the range), `untracked_listener`
(recorded process gone but something still holds a port). A range is `stale`
when it has no live process and no listener on any port of the range, on any
local address. For a live process, status only tries loopback connects (it never
binds a live service's ports), so a service listening only on a LAN address
shows `port_unbound`; that is a hint, never a release.

Run `sweep` (MCP `sweep`) to review drift; it is read-only. To free stale
ranges, pass the reviewed ids: `sweep --release --range-id ID` (repeatable; MCP
`release: true, range_ids: [...]`). Release re-checks under the registry lock
and frees only ranges that are confirmed and still stale; others are listed in
`skipped` with a `skip_reason`: `not_confirmed` (stale but not in the ids),
`no_longer_stale` (confirmed, in scope, no longer stale), `unknown_range` (no
range has that id; `project` and `start` are null) or `not_in_scope` (the range
belongs to a project outside `--project`). Nothing is freed for any of them.
Without range ids, release frees every currently stale range. Freeing uses the normal
stop teardown; a failed Tailscale teardown leaves the range reserved (see
`failed`/`warnings`). `untracked_listener` is never swept: stop that process or
restart the service. A live process with no listener: check
`.portskill/start.log` or restart.

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

`~/.config/portskill/registry.json` is authoritative; `.portskill.json`
is an optional project mirror. Use the tools to update state. Report corrupt
registry/listen files without overwriting them.

Migration from the pre-rename `port-registry` names: `~/.config/port-registry`,
`<project>/.port-registry/` and `<project>/.port-registry.json` become
`~/.config/portskill`, `.portskill/` and `.portskill.json`. They move on the first
run and leave a symlink at the old name, so old script paths keep resolving. The
registry reads lifecycle paths that start with `.port-registry/` as `.portskill/...`.
A mirror written before the rename can still list the old paths. `status` then
adds `mirror_warnings` (code `legacy_lifecycle_paths`), giving each stale path
and its `replacement`. Status only reports; it never edits the mirror. The
next change to that project (allocate, start, stop, activate or release)
rewrites the paths that start with `.port-registry/`. Any other path holding
`.port-registry` (for example an absolute one) stays as written: fix it by
updating the range's start/stop script. Do not hand-edit `.portskill.json`.

Read only the reference needed for the task:

- [Administration](references/administration.md): MCP profiles, defaults, presets,
  environment import/export, history, diagnostics, and experimental Handoff.
- [Roster preview](references/roster-preview.md): when the user requests Roster's
  seeded frontend preview, including its isolated preview data and private URL.

Use CLI `<subcommand> --help` or the connected MCP tool schema for exact arguments.
