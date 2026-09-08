# Portskill

[![CI](https://github.com/bens27/Portskill/actions/workflows/ci.yml/badge.svg)](https://github.com/bens27/Portskill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A local port registry for developers and AI agents. Reserve project port ranges, start and stop services, and manage everything through an HTML UI, MCP, or a Python CLI.

> I made this tool after getting tired of juggling experiments and port problems on a single machine. — Ben

- Allocate ports from a shared pool and keep project assignments in one place.
- Save service start/stop commands and choose which services start by default.
- Connect agents over HTTP or stdio MCP, with per-tool controls and command chains.
- Optionally expose registered services through Tailscale Serve.
- Run with Python's standard library: no Node.js or runtime pip dependencies.

Portskill is early-stage software for personal development machines. The registry lives at `~/.config/port-registry/registry.json`; project folders do not need their own copy. The Python module remains `port_registry_app` for compatibility.

## Quick start

Requires **Python 3.10+** on **macOS or Linux**. Tailscale is optional and only needed for Tailnet sharing.

```bash
git clone https://github.com/bens27/Portskill.git
cd Portskill
./scripts/run.sh
```

The server prints its URL and opens the dashboard. Use `./scripts/run.sh --no-open` to skip opening a browser. The listen port is allocated automatically and reused when available; do not assume a fixed port.

To retrieve the current URL:

```bash
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.config/port-registry/listen.json').read_text())['ui_url'])"
```

In the dashboard, reserve a project range, configure a service, then use **Start** and **Stop**. The **Settings** panel controls defaults; **Agent connection** has MCP setup instructions.

```bash
./scripts/cli.sh allocate --count 1 --tailnet none --project .
./scripts/cli.sh status
./scripts/doctor.sh
```

`allocate` returns a range ID. Pass it to `start`, `stop`, or `release` using `--range-id`. By default, **Stop also Release** frees the reservation after stopping a service; turn it off in Settings to keep ports reserved.

## Connect an agent

HTTP MCP uses the **same local listener** as the HTML UI. Read `mcp_url` from `~/.config/port-registry/listen.json` for the endpoint. It accepts JSON-RPC at `POST /mcp`; `GET /mcp` provides discovery information.

For stdio MCP, use [examples/mcp.stdio.json](examples/mcp.stdio.json), replacing the path with your checkout:

```json
{
  "mcpServers": {
    "portskill": {
      "command": "python3",
      "args": ["-m", "port_registry_app", "--mcp-stdio"],
      "env": {"PYTHONPATH": "/absolute/path/to/Portskill"}
    }
  }
}
```

The `portskill` tool handles the service lifecycle. Individual `start`, `stop`, `release`, and `status` tools are also available. The default `full` profile exposes core tools; opt into `lean` with `./scripts/cli.sh settings set --mcp-tools-profile lean`. Session Handoff requires its own opt-in regardless of profile. Reconnect your MCP client after changing available tools.

An optional [agent skill](skill/SKILL.md) documents the CLI workflow. See the [usage guide](docs/usage.md) for tool controls, command chains, presets, and HTTP authentication.

## Optional installation

Run directly from the checkout, or install into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
portskill --help
portskill-cli doctor
```

Use `python -m pip install -e .` for an editable development install. The wheel includes the core application and static assets. Experimental Handoff assets and Mac build scripts are supplied in the source checkout; see below for configuring a kit path with an installed app.

On macOS, build a local app and optionally keep it running after Terminal closes:

```bash
./scripts/build-app.sh
./scripts/install-keepalive.sh install
./scripts/install-keepalive.sh status
```

Portskill does not ship a notarized or signed binary. Local builds and the optional LaunchAgent are described in the [usage guide](docs/usage.md#keep-alive-macos-optional).

## Local access and data

The default listener is `127.0.0.1`. Local HTTP UI/API/MCP access is open unless you enable the optional passkey gate; stdio MCP does not use HTTP authentication. Service commands execute with your user account's permissions.

- Non-loopback binds are refused unless you pass `--allow-non-loopback`.
- Sharing Portskill through Tailscale does not add HTTP authentication. `settings.serve_portskill_on_tailscale` defaults **off** for new installs.
- Funnel of Portskill's own listen port is refused. Funnel of a registered user service is an explicit action.
- Passkey gate setup and first registration are **loopback-only**. JSON API requests require `Content-Type: application/json`.
- Corrupt registry or listen files fail closed; Portskill does not silently replace them.

Read [SECURITY.md](SECURITY.md) before changing network exposure. Multi-user access and remote machine management are not implemented.

## Experimental (Beta)

### Session Handoff

**Disabled by default.** Session Handoff helps an agent write a handoff document and resume work in a fresh session. The bundled kit includes skills, a ledger, optional context-watch hooks, and browser helpers. Support varies by agent surface and may change.

Open **Experimental (Beta)** in the dashboard and turn on **Enable Session Handoff**, or run:

```bash
./scripts/cli.sh settings set --handoff-enabled on
# Disable again:
./scripts/cli.sh settings set --handoff-enabled off
```

When disabled, Handoff tools are absent from MCP discovery, direct and chained MCP calls are rejected, and the dashboard does not read the handoff ledger. Enabling makes `handoff_status`, `handoff_skill`, `handoff_template`, `handoff_list`, `handoff_resolve`, `handoff_new_path`, `handoff_resume`, `handoff_supersede`, and `handoff_install_help` eligible for use. Individual tool switches still apply; the `lean` profile keeps them hidden until you enable them.

Enabling in Portskill does **not** install agent hooks. Use the section's install help and the [kit README](vendor/session-handoff-kit/README.md) to choose a surface. Disabling in Portskill does **not** remove hooks or extensions you previously installed; manage those in the agent or browser where you installed them. Existing explicit `handoff_enabled: true` settings remain enabled.

Source checkouts include `vendor/session-handoff-kit/`. For a wheel or Mac app, point at that directory in a separate source checkout before enabling:

```bash
portskill-cli settings set --handoff-kit /absolute/path/to/Portskill/vendor/session-handoff-kit
portskill-cli settings set --handoff-enabled on
```

`PORTSKILL_HANDOFF_KIT` overrides the kit path. An absent, unconfigured kit does not affect core health; `doctor` reports an error if an enabled or explicitly configured kit is missing.

## Development and support

```bash
./scripts/smoke_test.sh
```

The suite covers CLI, MCP, HTTP, service lifecycle, defaults, and packaging with temporary test state. CI runs on Linux and macOS. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, [CHANGELOG.md](CHANGELOG.md) for changes, and [GitHub Issues](https://github.com/bens27/Portskill/issues) for bugs and feature requests.

Licensed under the [MIT License](LICENSE).
