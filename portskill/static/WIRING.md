# Portskill UI wiring

This package ships three ways to see/use the Portskill UI. Node is **not** required for (1) or (2).

## 1. Static preview (sample data)

Open the pre-rendered Roster-style page in a browser:

```bash
open ui/portskill-preview.html
# or: xdg-open ui/portskill-preview.html
```

This uses baked-in sample projects/ranges and shows Start / Stop / Release buttons. Action clicks will fail against a `file://` origin (no server); use the live UI below for real actions.

## 2. Live light UI + MCP (stdlib Python)

Preferred entrypoint (Portskill **app**):

```bash
PYTHONPATH=. python3 -m portskill
# optional: … --port 8765 --no-open
# MCP stdio only: python3 -m portskill --mcp-stdio
# or after pip install -e .: portskill
# macOS: open macos/Port\ Registry.app
```

Thin wrappers `app.py` / `serve_ui.py` still call the same package.

Then open the printed URL (default `http://127.0.0.1:8765/`).

- Reads `PORTSKILL_REGISTRY_PATH` or `~/.config/portskill/registry.json`
- `GET /` and `GET /portskill` → HTML console (sidebar, stats, range cards, Start/Stop/Release)
- `GET /api/state` → raw registry JSON
- `POST /portskill/actions` → invokes `portskill.cli` (see contract below)
- `POST /mcp` → MCP JSON-RPC (`initialize`, `tools/list`, `tools/call`); `GET /mcp` → discovery
- Binds `127.0.0.1` only; no Node
- MCP tools: portskill (one MCP tool for your agent to handle all port management functions), allocate, start, stop, release, status, doctor

## 3. Import TS modules into a host console

Self-contained TypeScript extracts (no `@roster/*` imports):

| Module | Role |
|--------|------|
| `portskill.ts` | Shared raw/action types |
| `portskill-view.ts` | `buildPortskillView`, HTML builders, CSS subset |
| `portskill-api.ts` | `portskillStateHttpResponse`, `portskillActionHttpResponse` |

Example host wiring:

```ts
import { buildPortskillView, renderPortskillPageHtml } from "./portskill-view.ts";
import {
  portskillStateHttpResponse,
  portskillActionHttpResponse
} from "./portskill-api.ts";
import type { PortskillRawState } from "./portskill.ts";

// GET /api/state
await portskillStateHttpResponse({ read: () => loadRegistry() });

// POST /portskill/actions
await portskillActionHttpResponse(request, {
  run: async (project, rangeId, action) => runCli(project, rangeId, action)
});

// Or render HTML yourself:
const html = renderPortskillPageHtml(buildPortskillView(raw as PortskillRawState));
```

Point your HTTP gateway at those helpers; implement `PortskillStateSource.read` and `PortskillActionRunner.run` against the same registry schema the CLI uses.

## 4. POST `/portskill/actions` contract

Request JSON:

```json
{ "project": "/absolute/project/path", "rangeId": "r…", "action": "start" }
```

`action` must be one of: `start` | `stop` | `release`.

Success response (HTTP 200):

```json
{ "ok": true }
```

The live page JS reloads on success (same as Roster client).

Validation failure (HTTP 400):

```json
{ "ok": false, "message": "expected { project, rangeId, action } with action in start|stop|release" }
```

CLI / runner failure (HTTP 422) — may include exit-3 needs-input payload:

```json
{
  "ok": false,
  "message": "…",
  "needs_input": true,
  "prompt": "…",
  "options": ["serve", "funnel", "none"],
  "resume_hint": "…"
}
```

The live `serve_ui.py` passes `--tailnet none` on `start` when needed to avoid exit-3 in the common local case; if the CLI still returns exit 3, the response is 422 with the needs_input JSON.

## Reference only

`ui/reference/portskill-console.e2e.test.ts` is the original Roster vitest coverage. It depends on the Roster test harness and full packages — do not run it standalone from this skill package.

## 5. MCP connect

**Stdio:**

```bash
python3 app.py --mcp-stdio
```

Configure MCP hosts with `command=python3` and `args=[/absolute/path/to/app.py, --mcp-stdio]`.

**HTTP:** run `python3 app.py`, then POST JSON-RPC to `http://127.0.0.1:8765/mcp`.

Example `tools/call` for allocate:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "allocate",
    "arguments": { "count": 1, "project": "/tmp/demo", "tailnet": "none" }
  }
}
```

Exit-3 needs_input returns `isError: true` with structured `needs_input`, `prompt`, `options`, and `resume_hint` (`--tailnet`).
