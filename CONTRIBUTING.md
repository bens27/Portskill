# Contributing

Bug reports and focused pull requests are welcome. Include the Portskill version,
operating system, Python version, and steps to reproduce. Remove tokens, private
project paths, and service commands containing credentials from logs and screenshots.
Report vulnerabilities using [SECURITY.md](SECURITY.md).

## Development

Clone the repository and run `./scripts/run.sh`. Python 3.10+ is required;
the application has no third-party runtime dependencies. An editable installation
in a virtual environment is optional (`python -m pip install -e .`).

Run `./scripts/smoke_test.sh` before submitting a change. Tests use temporary
configuration and local loopback listeners; no Tailscale account or Mac app is
required. To additionally probe your existing server, use
`./scripts/smoke_test.sh --live`.

For behavior changes, add a regression test for the observable result. Preserve
stdlib-only runtime support, fail-closed handling of corrupt data, and existing
CLI/MCP compatibility. Keep optional features disabled unless explicitly enabled.

`port_registry_app/server.py` renders the live dashboard; files in `ui/` are
reference/preview assets. Keep the root `SKILL.md` and `skill/SKILL.md` aligned
when changing agent instructions. Do not commit runtime registries, auth files,
agent session notes, compiled apps, or local build output.

## Packaging

`python -m pip wheel --no-deps .` builds the core Python wheel. CI verifies that
it installs and runs outside the checkout. Handoff kit assets are distributed in
the source tree and require a configured kit path with a wheel or Mac app.

On macOS, `./scripts/build-app.sh` builds from tracked templates under
`macos/app-template/`. It writes local app bundles; installation of a persistent
LaunchAgent is a separate action through `scripts/install-keepalive.sh`.

## Pull requests

Explain the problem, resulting behavior, and validation. Keep changes focused,
link related issues, and update user documentation for defaults or interface
changes. A passing CI run checks functionality; it does not constitute a security
audit or signed release.
