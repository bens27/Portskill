import { describe, expect, it } from "vitest";
import type {
  PortRegistryAction,
  PortRegistryActionResult,
  PortRegistryRawState
} from "../packages/interfaces/src/port-registry";
import { buildPortRegistryView, renderRosterConsoleHtml } from "../packages/web/src/index";
import type {
  HttpRequest,
  PortRegistryActionRunner,
  PortRegistryStateSource
} from "../packages/api/src/index";
import {
  portRegistryActionHttpResponse,
  portRegistryStateHttpResponse
} from "../packages/api/src/index";

describe("port registry console view", () => {
  it("groups ranges by project, sorts them, and computes pool/active stats", () => {
    const raw = sampleRegistry();
    const view = buildPortRegistryView(raw);

    expect(view.pool).toEqual({ start: 20000, end: 29999 });
    expect(view.projects.map((project) => project.projectLabel)).toEqual([
      "web-app",
      "overlay"
    ]);

    const webApp = view.projects.find((project) => project.projectLabel === "web-app");
    expect(webApp?.ranges.map((range) => range.id)).toEqual(["r1", "r2"]);
    expect(webApp?.ranges[0]?.start).toBeLessThan(webApp?.ranges[1]?.start ?? Infinity);

    expect(view.stats.totalRanges).toBe(3);
    expect(view.stats.activeRanges).toBe(1);
    expect(view.stats.poolPortsUsed).toBe(2 + 3);
    expect(view.stats.poolPortsTotal).toBe(10000);
  });

  it("defaults an unrecognized state and tailnet mode rather than throwing", () => {
    const raw: PortRegistryRawState = {
      pool: { start: 20000, end: 20099 },
      projects: {
        "/tmp/weird": {
          ranges: [
            {
              id: "r-weird",
              start: 20000,
              end: 20001,
              state: "unknown-state",
              tailnet: { mode: "not-a-real-mode" }
            }
          ]
        }
      }
    };

    const view = buildPortRegistryView(raw);
    const range = view.projects[0]?.ranges[0];
    expect(range?.state).toBe("reserved");
    expect(range?.tailnetMode).toBeNull();
  });
});

describe("port registry console rendering", () => {
  it("renders the nav link, project groups, badges, and action button states", () => {
    const view = buildPortRegistryView(sampleRegistry());
    const html = renderRosterConsoleHtml(rosterViewStub(), {
      activeView: "ports",
      portRegistry: view
    });

    expect(html).toContain('href="#ports"');
    expect(html).toContain('class="active" href="#ports"');
    expect(html).toContain("Port Registry");
    expect(html).toContain("web-app");
    expect(html).toContain("overlay");
    expect(html).toContain("20000–20001");
    expect(html).toContain("serve");

    // active range: start disabled, stop enabled, release enabled
    expect(html).toMatch(/data-range-id="r1"[\s\S]*?data-pr-action="start"[^>]*disabled/);
    expect(html).toMatch(/data-range-id="r1"[\s\S]*?data-pr-action="stop"(?![^>]*disabled)/);

    // reserved range: start enabled, stop disabled
    expect(html).toMatch(/data-range-id="r2"[\s\S]*?data-pr-action="stop"[^>]*disabled/);

    expect(html).toContain("Allocated ranges");
    expect(html).toContain("Pool ports used");
    expect(html).toContain("/port-registry/actions");
  });

  it("shows an empty state when no registry data is provided", () => {
    const html = renderRosterConsoleHtml(rosterViewStub(), { activeView: "ports" });
    expect(html).toContain("Port registry data unavailable.");
  });
});

describe("port registry API handlers", () => {
  it("returns the raw registry state from an injected source", async () => {
    const source: PortRegistryStateSource = { read: () => sampleRegistry() };
    const response = await portRegistryStateHttpResponse(source);
    expect(response.status).toBe(200);
    expect(response.body).toEqual(sampleRegistry());
  });

  it("invokes the action runner with a well-formed request and returns its result", async () => {
    const calls: Array<{ project: string; rangeId: string; action: PortRegistryAction }> = [];
    const runner: PortRegistryActionRunner = {
      run: (project, rangeId, action) => {
        calls.push({ project, rangeId, action });
        return { ok: true } satisfies PortRegistryActionResult;
      }
    };

    const request: HttpRequest = {
      method: "POST",
      path: "/port-registry/actions",
      headers: {},
      body: { project: "/Users/example/dev/web-app", rangeId: "r1", action: "stop" }
    };

    const response = await portRegistryActionHttpResponse(request, runner);
    expect(response.status).toBe(200);
    expect(response.body).toEqual({ ok: true });
    expect(calls).toEqual([
      { project: "/Users/example/dev/web-app", rangeId: "r1", action: "stop" }
    ]);
  });

  it("rejects a malformed action request without calling the runner", async () => {
    let called = false;
    const runner: PortRegistryActionRunner = {
      run: () => {
        called = true;
        return { ok: true };
      }
    };

    const request: HttpRequest = {
      method: "POST",
      path: "/port-registry/actions",
      headers: {},
      body: { project: "/x", rangeId: "r1", action: "reboot" }
    };

    const response = await portRegistryActionHttpResponse(request, runner);
    expect(response.status).toBe(400);
    expect(called).toBe(false);
  });

  it("surfaces a failed action as a 422 without throwing", async () => {
    const runner: PortRegistryActionRunner = {
      run: () => ({ ok: false, message: "start_script_placeholder" })
    };

    const request: HttpRequest = {
      method: "POST",
      path: "/port-registry/actions",
      headers: {},
      body: { project: "/x", rangeId: "r1", action: "start" }
    };

    const response = await portRegistryActionHttpResponse(request, runner);
    expect(response.status).toBe(422);
    expect(response.body).toEqual({ ok: false, message: "start_script_placeholder" });
  });
});

function sampleRegistry(): PortRegistryRawState {
  return {
    version: 1,
    pool: { start: 20000, end: 29999 },
    projects: {
      "/Users/example/dev/web-app": {
        ranges: [
          {
            id: "r1",
            start: 20000,
            end: 20001,
            state: "active",
            note: "web server",
            tailnet: { mode: "serve", port: 20000, configured_at: "2026-07-27T18:56:41Z" },
            lifecycle: { pid: 80376, pgid: 80376, started_at: "2026-07-27T18:56:41Z" }
          },
          {
            id: "r2",
            start: 20003,
            end: 20005,
            state: "reserved",
            note: null,
            tailnet: { mode: "none" }
          }
        ]
      },
      "/Users/example/dev/overlay": {
        ranges: [
          {
            id: "r3",
            start: 20100,
            end: 20100,
            state: "released",
            note: "old test port"
          }
        ]
      }
    }
  };
}

function rosterViewStub() {
  return {
    viewerId: "example",
    agents: [],
    feed: [],
    stats: {
      visiblePrincipals: 0,
      activeAgents: 0,
      openContracts: 0,
      latestEventSeq: 0
    }
  };
}
