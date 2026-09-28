# Portskill architecture

Open the [portable interactive model](exports/portskill.html), or use the registered
Fractal studio at <http://127.0.0.1:20073/?model=portskill&scene=overview> while the
local development server is running. The portable HTML contains all scenes and
journeys and works without a Fractal installation. Its export is a snapshot;
regenerate after model changes.

## Views

| View | Purpose | Exports |
| --- | --- | --- |
| One local service registry | Clients, Portskill, local services, storage, optional Tailscale and macOS shell | [SVG](exports/overview.svg) · [PNG](exports/overview.png) |
| UI, MCP, and CLI converge | Internal adapters and shared command operations | [SVG](exports/runtime.svg) · [PNG](exports/runtime.png) |
| Allocation, observation, and control | Core responsibilities and lifecycle dependencies | [SVG](exports/lifecycle.svg) · [PNG](exports/lifecycle.png) |
| What Portskill can stop | Tracked processes, configured hooks, and observed-only listeners | [SVG](exports/ownership.svg) · [PNG](exports/ownership.png) |
| Stop succeeds after verification | A local tracked service, successful hook, no Tailnet mapping, release enabled | [SVG](exports/verified-stop.svg) · [PNG](exports/verified-stop.png) |
| An untracked listener prevents success | No tracked PID or runnable hook; occupied ports prevent Stop success | [SVG](exports/untracked-stop.svg) · [PNG](exports/untracked-stop.png) |

The runtime and lifecycle views intentionally focus inside their scope; the
renderer reports omitted external connections. Use the overview or ownership view
for surrounding context. Sequence journeys are authored scenarios, not captured
traces, timings, or complete branching state machines.

## Source and interpretation

Authored on 2026-09-23 from base commit `714b4e2` plus the working tree's existing
Stop verification changes. Stable element and relationship IDs live in `model.c4`;
scene configuration, boundaries, and provenance live in `fractal.json`; ordered
Stop scenarios live in `sequences.json`.

The command components group responsibilities implemented mainly in `cli.py`.
They are not independent processes or modules. Core UI/MCP commands invoke CLI
subprocesses; dashboard observations also call shared Python helpers directly.
The model focuses on service lifecycle and access paths. Presets, environment
history, discovery/import, experimental session handoffs, and other auxiliary
features are not individually decomposed.

| Claim | Inspected implementation |
| --- | --- |
| HTTP and stdio composition | `port_registry_app/server.py`: `main`, `Handler.do_POST`; `__main__.py` |
| UI actions and MCP dispatch | `server.py`: `dispatch_ui_action`, `run_cli_action`; `mcp.py`: `mcp_handle`, `run_cli` |
| Registry authority and project mirrors | `cli.py`: `locked_registry`, `write_local_project_file` |
| Start, occupancy, and explicit reclaim | `cli.py`: `cmd_start`, `reclaim_range` |
| Stop hook, tracked termination, verification | `cli.py`: `cmd_stop`, `park_item_for_exit`, `run_stop_script_if_ready` |
| Allocation versus reachability | `cli.py`: `observed_range_status`, `reachable_range_ports`; `tests/test_stop_verification.py` |
| Optional sharing and HTTP access | `README.md`, `cli.py`: `run_tailnet`; `server.py`: HTTP access guards |
| Optional macOS supervision | `macos/PortskillMenu/main.swift`, `scripts/portskill-daemon.sh` |

Saved allocation state, live TCP reachability, and tracked process ownership are
separate facts. Stop runs a configured hook even without a tracked PID, terminates
only tracked processes through its built-in termination path, then verifies all
range ports and tracked processes. A failed verification or failed hook preserves
allocation. A project hook can perform its own configured cleanup; the ownership
view does not claim to sandbox it. Stop can reserve or release on success according
to settings/flags. Remote ranges are links only and do not authorize local process
actions. Tailnet exposure does not provide HTTP authentication.

## Regenerate and validate

Use absolute paths: the `bin/fractal` wrapper changes its working directory to the
Fractal checkout. A relative `--directory` can silently select that checkout's own
model. Verify that validation reports `model: portskill`.

```sh
FRACTAL_ROOT=/Users/bens/Development/fractal
MODEL_DIR=/Users/bens/Development/Portskill/docs/diagrams/fractal
"$FRACTAL_ROOT/bin/fractal" validate --directory "$MODEL_DIR" --json
"$FRACTAL_ROOT/bin/fractal" project --directory "$MODEL_DIR" --scene overview --json
"$FRACTAL_ROOT/bin/fractal" layout --directory "$MODEL_DIR" --scene overview --json
"$FRACTAL_ROOT/bin/fractal" export --directory "$MODEL_DIR" --scene overview \
  --format html --output "$MODEL_DIR/exports/portskill.html"
"$FRACTAL_ROOT/bin/fractal" export --directory "$MODEL_DIR" --scene overview \
  --output "$MODEL_DIR/exports/overview.svg"
"$FRACTAL_ROOT/bin/fractal" export --directory "$MODEL_DIR" --scene overview \
  --format png --output "$MODEL_DIR/exports/overview.png"
"$FRACTAL_ROOT/bin/fractal" sequence-export --directory "$MODEL_DIR" \
  --journey verified-stop --output "$MODEL_DIR/exports/verified-stop.svg"
```

Repeat scene exports for `runtime`, `lifecycle`, and `ownership`, and sequence
exports for `untracked-stop`; add `--format png` for raster sequence exports.
Model registration is in the user catalog, pointing to this directory. It preserves
the existing example projects and does not copy model files into Fractal itself.

## Verification

Validation passed with 19 elements, 19 relationships, four scenes, and two journeys.
Every scene was projected and laid out; both sequence geometries were generated.
All evidence paths exist, and their relevant source passages were inspected.
The six PNG exports were visually inspected for containment, labels, reading order,
and legibility. The live studio API returned the registered model with all four
scene IDs and both journey IDs. No application source was changed for this model.

The in-app browser remained unavailable after its documented recovery checks;
tracking issue `td-fd2487` was reopened with that review evidence. PNG rendering
works through Fractal's standalone Chromium renderer outside the filesystem sandbox.

The portable HTML was opened through `file://` in standalone Chromium: the
initial overview rendered, switching to the lifecycle perspective succeeded,
and the untracked-listener sequence displayed its failure response. No page
errors were reported; the initial portable rendering was visually inspected.
