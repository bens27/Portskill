/**
 * Portable Port Registry HTTP helpers (extracted from Roster packages/api).
 * Local types from ./port-registry.ts — no @roster imports.
 */
import type {
  PortRegistryAction,
  PortRegistryActionResult,
  PortRegistryRawState
} from "./port-registry.ts";

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

export interface PortRegistryStateSource {
  read(): PortRegistryRawState | Promise<PortRegistryRawState>;
}

export interface PortRegistryActionRunner {
  run(
    project: string,
    rangeId: string,
    action: PortRegistryAction
  ): PortRegistryActionResult | Promise<PortRegistryActionResult>;
}

export interface PortRegistryActionRequestBody {
  readonly project?: unknown;
  readonly rangeId?: unknown;
  readonly action?: unknown;
}

export async function portRegistryStateHttpResponse(
  source: PortRegistryStateSource
): Promise<HttpResponse> {
  const state = await source.read();
  return {
    status: 200,
    headers: { "content-type": "application/json" },
    body: state
  };
}

export async function portRegistryActionHttpResponse(
  request: HttpRequest,
  runner: PortRegistryActionRunner
): Promise<HttpResponse> {
  const parsed = parsePortRegistryActionRequest(request.body);
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

export function parsePortRegistryActionRequest(
  body: unknown
): { readonly project: string; readonly rangeId: string; readonly action: PortRegistryAction } | undefined {
  if (typeof body !== "object" || body === null) {
    return undefined;
  }

  const candidate = body as PortRegistryActionRequestBody;
  const project = typeof candidate.project === "string" ? candidate.project : undefined;
  const rangeId = typeof candidate.rangeId === "string" ? candidate.rangeId : undefined;
  const action = typeof candidate.action === "string" ? candidate.action : undefined;
  if (!project || !rangeId || !isPortRegistryAction(action)) {
    return undefined;
  }

  return { project, rangeId, action };
}

function isPortRegistryAction(value: string | undefined): value is PortRegistryAction {
  return value === "start" || value === "stop" || value === "release";
}
