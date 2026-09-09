# Portskill administration

Commands below follow `portskill-cli` or `python3 -m port_registry_app.cli`.
Use the command's `--help` for additional flags.

## MCP connection and tool availability

HTTP MCP uses `POST /mcp` for JSON-RPC and `GET /mcp` for discovery on the UI's
listener. Read the actual endpoint from `~/.config/port-registry/listen.json`.
Stdio runs with `python3 -m port_registry_app --mcp-stdio`.

`settings set --mcp-tools-profile full|lean` changes tool discovery:

- `full` (default) exposes core tools. Handoff still requires its separate opt-in.
- `lean` exposes `portskill`, `status`, `settings_get`, `allocate`, `stop`, and
  `release`. Other tools, including `settings_set`, are hidden.

Use CLI `settings set --mcp-tool NAME=on|off` for individual overrides or to
recover hidden tools. Reconnect the MCP client after changes. `activate` is an
internal primitive, disabled in lean. Settings are stored in `mcp_tools` and
`mcp_tools_profile`; a missing per-tool key means enabled.

## Defaults, environments, and presets

| Task | CLI |
| --- | --- |
| Set a range's default | `set-default --range-id ID --state on|off --project PATH` |
| Start default services | `apply-defaults --project PATH` |
| Keep reservations on stop | `settings set --stop-also-release off` |
| Export an environment | `environment export --name NAME --out PATH --project PATH` |
| Import an environment | `environment import --file PATH` |
| Save live ranges as a preset | `preset save --name NAME --project PATH` |
| Inspect presets | `preset list` or `preset show --name NAME` |
| Apply or delete a preset | `preset apply --name NAME` or `preset delete --name NAME` |
| Inspect settings | `settings get` |
| Configure launch auto-apply | `settings set --auto-apply-preset NAME` and `settings set --auto-apply-on-launch on` |

Environment import reuses allocations by note and project; it does not start
services unless `--apply-defaults` is supplied. Preset apply imports services and
starts defaults. `apply-defaults` and `preset apply` accept `--also-stop-off` to
stop running services whose default is off. Missing `default_state` means off.
Use `--no-auto-apply` on app launch to skip configured auto-apply once.

For environment undo, use `history list`, `history restore --index N`, or
`history reset`, optionally with `--environment NAME`. This is
`registry.environment_history`, separate from Session Handoff history.

## Diagnostics and access

`doctor --project PATH` reports JSON and never wipes files. Exit `0` means healthy
(cold/absent state is allowed); exit `2`, `reason: doctor_failed`, means checks
failed. Inspect reported failures: corrupt state, missing runtime files, a
recorded listener that is unreachable, an unauthorized non-loopback bind, or an
invalid enabled/explicitly configured Handoff kit. Placeholder hooks and absent
Tailscale are informational, so doctor success alone does not prove a service ran.

Tailscale binary resolution: `PORT_REGISTRY_TAILSCALE_BIN`, then `tailscale` on
PATH, then the Mac app bundle. Remote-machine management is not implemented.

The optional HTTP passkey gate is controlled by `http-auth gate on|off`.
First registration and gate enable are loopback-only while the gate is off.
When enabled, protected routes need a passkey session cookie or optional bearer.
Mutating HTTP requests reject cross-origin Origin values; JSON bodies require
`Content-Type: application/json`.

## Experimental Session Handoff

Session Handoff is disabled by default. Enable when requested with
`settings set --handoff-enabled on`. While disabled, the dashboard does not read
the ledger and direct/chained `handoff_*` calls are rejected. Per-tool switches
and lean-profile restrictions still apply after enabling.

A wheel or Mac app needs a source kit configured with
`settings set --handoff-kit /absolute/path/to/session-handoff-kit`;
`PORTSKILL_HANDOFF_KIT` overrides that path. An enabled or explicitly configured
missing kit fails doctor. Toggling Handoff does not install or uninstall external
agent hooks. Use the kit's matching surface skill for the actual handoff workflow.
