/**
 * Portable Portskill HTTP helpers (extracted from Roster packages/api).
 * Local types from ./portskill.ts — no @roster imports.
 */
import type {
  PortskillAction,
  PortskillActionResult,
  PortskillRawState
} from "./portskill.ts";

/** Minimal HTTP request shape for host wiring. */
export interface HttpRequest {
  readonly method: string;
  readonly path: string;
  readonly headers: Readonly<Record<string, string | undefined>>;
  readonly body?: unknown;
}

/** Minimal HTTP response shape for host wiring. */
export interface HttpResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
}

export interface PortskillStateSource {
  read(): PortskillRawState | Promise<PortskillRawState>;
}

export interface PortskillActionRunner {
  run(
    project: string,
    rangeId: string,
    action: PortskillAction
  ): PortskillActionResult | Promise<PortskillActionResult>;
}

export interface PortskillActionRequestBody {
  readonly project?: unknown;
  readonly rangeId?: unknown;
  readonly action?: unknown;
}

export async function portskillStateHttpResponse(
  source: PortskillStateSource
): Promise<HttpResponse> {
  const state = await source.read();
  return {
    status: 200,
    headers: { "content-type": "application/json" },
    body: state
  };
}

export async function portskillActionHttpResponse(
  request: HttpRequest,
  runner: PortskillActionRunner
): Promise<HttpResponse> {
  const parsed = parsePortskillActionRequest(request.body);
  if (!parsed) {
    return {
      status: 400,
      headers: { "content-type": "application/json" },
      body: {
        ok: false,
        message: "expected { project, rangeId, action } with action in start|stop|release"
      }
    };
  }

  const result = await runner.run(parsed.project, parsed.rangeId, parsed.action);
  return {
    status: result.ok ? 200 : 422,
    headers: { "content-type": "application/json" },
    body: result
  };
}

export function parsePortskillActionRequest(
  body: unknown
): { readonly project: string; readonly rangeId: string; readonly action: PortskillAction } | undefined {
  if (typeof body !== "object" || body === null) {
    return undefined;
  }

  const candidate = body as PortskillActionRequestBody;
  const project = typeof candidate.project === "string" ? candidate.project : undefined;
  const rangeId = typeof candidate.rangeId === "string" ? candidate.rangeId : undefined;
  const action = typeof candidate.action === "string" ? candidate.action : undefined;
  if (!project || !rangeId || !isPortskillAction(action)) {
    return undefined;
  }

  return { project, rangeId, action };
}

function isPortskillAction(value: string | undefined): value is PortskillAction {
  return value === "start" || value === "stop" || value === "release";
}
