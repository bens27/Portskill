/**
 * Portable Port Registry view builders + HTML (extracted from Roster packages/web).
 * Self-contained: no Roster org/console deps. Import types from ./port-registry.ts.
 */
import type {
  PortRegistryRangeState,
  PortRegistryRawState,
  PortRegistryTailnetMode
} from "./port-registry.ts";

export interface PortRegistryRangeView {
  readonly id: string;
  readonly start: number;
  readonly end: number;
  readonly state: PortRegistryRangeState;
  readonly note: string | null;
  readonly tailnetMode: PortRegistryTailnetMode;
  readonly pid: number | null;
  readonly startedAt: string | null;
}

export interface PortRegistryProjectView {
  readonly project: string;
  readonly projectLabel: string;
  readonly ranges: readonly PortRegistryRangeView[];
}

export interface PortRegistryView {
  readonly pool: { readonly start: number; readonly end: number };
  readonly projects: readonly PortRegistryProjectView[];
  readonly stats: {
    readonly totalRanges: number;
    readonly activeRanges: number;
    readonly poolPortsUsed: number;
    readonly poolPortsTotal: number;
  };
}

export function buildPortRegistryView(raw: PortRegistryRawState): PortRegistryView {
  const projects = Object.entries(raw.projects)
    .map(([project, entry]) => buildPortRegistryProjectView(project, entry.ranges))
    .sort((left, right) => left.projectLabel.localeCompare(right.projectLabel));

  const allRanges = projects.flatMap((project) => project.ranges);
  const nonReleased = allRanges.filter((range) => range.state !== "released");
  const poolPortsUsed = nonReleased.reduce(
    (total, range) => total + (range.end - range.start + 1),
    0
  );

  return {
    pool: raw.pool,
    projects,
    stats: {
      totalRanges: allRanges.length,
      activeRanges: allRanges.filter((range) => range.state === "active").length,
      poolPortsUsed,
      poolPortsTotal: raw.pool.end - raw.pool.start + 1
    }
  };
}

function buildPortRegistryProjectView(
  project: string,
  rawRanges: readonly PortRegistryRawState["projects"][string]["ranges"][number][]
): PortRegistryProjectView {
  return {
    project,
    projectLabel: portRegistryProjectLabel(project),
    ranges: [...rawRanges].map(normalizePortRegistryRange).sort((left, right) => left.start - right.start)
  };
}

function normalizePortRegistryRange(
  raw: PortRegistryRawState["projects"][string]["ranges"][number]
): PortRegistryRangeView {
  return {
    id: raw.id,
    start: raw.start,
    end: raw.end,
    state: isPortRegistryRangeState(raw.state) ? raw.state : "reserved",
    note: raw.note ?? null,
    tailnetMode: normalizePortRegistryTailnetMode(raw.tailnet?.mode),
    pid: raw.lifecycle?.pid ?? null,
    startedAt: raw.lifecycle?.started_at ?? null
  };
}

function isPortRegistryRangeState(value: string): value is PortRegistryRangeState {
  return value === "reserved" || value === "active" || value === "released";
}

function normalizePortRegistryTailnetMode(value: string | null | undefined): PortRegistryTailnetMode {
  return value === "serve" || value === "funnel" || value === "none" ? value : null;
}

function portRegistryProjectLabel(project: string): string {
  const segments = project.split(/[/\\]/u).filter(Boolean);
  return segments.at(-1) ?? project;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/gu, (character) => {
    switch (character) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return character;
    }
  });
}

export function statHtml(label: string, value: number): string {
  return `<div class="stat"><span>${escapeHtml(label)}</span><b>${value}</b></div>`;
}

export function portRegistryStatsHtml(view: PortRegistryView | undefined): string {
  if (!view) {
    return "";
  }

  return `<div class="stats pr-stats">
    ${statHtml("Allocated ranges", view.stats.totalRanges)}
    ${statHtml("Active ranges", view.stats.activeRanges)}
    ${statHtml("Pool ports used", view.stats.poolPortsUsed)}
    ${statHtml("Pool ports total", view.stats.poolPortsTotal)}
  </div>`;
}

export function portRegistryHtml(view: PortRegistryView | undefined): string {
  if (!view) {
    return `<div class="empty">Port registry data unavailable.</div>`;
  }

  if (view.projects.length === 0) {
    return `<div class="empty">No ports allocated yet.</div>`;
  }

  return view.projects.map(portRegistryProjectHtml).join("");
}

export function portRegistryProjectHtml(project: PortRegistryProjectView): string {
  return `<div class="pr-project">
  <h3 class="pr-project-name" title="${escapeHtml(project.project)}">${escapeHtml(project.projectLabel)}</h3>
  <div class="pr-ranges">${project.ranges.map((range) => portRegistryRangeHtml(project.project, range)).join("")}</div>
</div>`;
}

export function portRegistryRangeHtml(project: string, range: PortRegistryRangeView): string {
  const stateClass = portRangeStateClass(range.state);
  const portLabel = range.start === range.end ? `${range.start}` : `${range.start}–${range.end}`;
  const tailnetBadge =
    range.tailnetMode && range.tailnetMode !== "none"
      ? `<span class="badge b-onb">${escapeHtml(range.tailnetMode)}</span>`
      : "";
  const metaParts = [
    range.note ? escapeHtml(range.note) : "<em>no note</em>",
    range.pid ? `pid ${range.pid}` : undefined
  ].filter(Boolean);
  const canStart = range.state !== "active";
  const canStop = range.state === "active";
  const canRelease = range.state !== "released";

  return `<div class="pr-range" data-range-id="${escapeHtml(range.id)}">
  <div class="pr-range-top">
    <span class="mono">${portLabel}</span>
    <span class="badge ${stateClass}">${escapeHtml(range.state)}</span>
    ${tailnetBadge}
  </div>
  <div class="pr-range-meta">${metaParts.join(" &middot; ")}</div>
  <div class="pr-range-actions">
    <button type="button" class="pr-btn" data-pr-action="start" data-project="${escapeHtml(project)}" data-range-id="${escapeHtml(range.id)}" ${canStart ? "" : "disabled"}>Start</button>
    <button type="button" class="pr-btn" data-pr-action="stop" data-project="${escapeHtml(project)}" data-range-id="${escapeHtml(range.id)}" ${canStop ? "" : "disabled"}>Stop</button>
    <button type="button" class="pr-btn pr-btn-danger" data-pr-action="release" data-project="${escapeHtml(project)}" data-range-id="${escapeHtml(range.id)}" ${canRelease ? "" : "disabled"}>Release</button>
  </div>
</div>`;
}

export function portRangeStateClass(state: PortRegistryRangeState): string {
  switch (state) {
    case "active":
      return "b-exec";
    case "reserved":
      return "b-onb";
    case "released":
      return "b-term";
  }
}

/** Subset of Roster consoleCss() tokens/classes needed for the Port Registry view. */
export function portRegistryConsoleCss(): string {
  return `:root{--paper:#f3f4f1;--panel:#fff;--ink:#15181d;--muted:#5d6572;--line:#dde0da;--cobalt:#2743d6;--green:#188a5e;--amber:#b97303;--red:#bf3b3b;--violet:#6c46c8}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 system-ui,sans-serif}.app{display:flex;min-height:100vh}.sidebar{width:212px;background:var(--ink);color:#c9cdd6}.brand{padding:22px 20px;border-bottom:1px solid rgba(255,255,255,.1)}.brand h1{margin:0;color:white;letter-spacing:.14em}.tag,.mono{font-family:ui-monospace,Menlo,monospace}.tag{font-size:11px;color:#7e8694}.nav{display:grid;gap:4px;padding:14px 10px}.nav a{color:#c9cdd6;text-decoration:none;padding:8px 10px;border-radius:7px}.nav a.active{background:var(--cobalt);color:white}.main{flex:1;min-width:0}.topbar{display:flex;justify-content:space-between;padding:14px 28px;border-bottom:1px solid var(--line);background:#fafbf9;position:sticky;top:0}.live{display:flex;gap:7px;align-items:center;color:var(--muted);font-family:ui-monospace,Menlo,monospace;font-size:11px}.live i{width:7px;height:7px;border-radius:50%;background:var(--green)}.content{padding:26px 28px;max-width:1180px;margin:auto}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:20px}.stat,.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px}.stat{padding:13px 16px}.stat span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase}.stat b{font-size:24px}.view-head{margin:0 0 16px}.view-head h2{margin:0;font-size:27px}.eyebrow{margin:0;color:var(--cobalt);font-size:11px;text-transform:uppercase;letter-spacing:.14em}.badge{font-size:11px;border-radius:999px;padding:2px 8px}.b-exec{background:#e2f2eb;color:var(--green)}.b-onb{background:#eee8fa;color:var(--violet)}.b-block{background:#f8eeda;color:var(--amber)}.b-term{background:#f8e6e6;color:var(--red)}.empty{padding:18px;color:var(--muted)}.pr-panel{padding:18px}.pr-project{margin-bottom:18px}.pr-project:last-child{margin-bottom:0}.pr-project-name{margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:11px}.pr-ranges{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}.pr-range{border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fafbf9}.pr-range-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.pr-range-meta{margin-top:8px;color:var(--muted);font-size:12px}.pr-range-actions{display:flex;gap:8px;margin-top:10px}.pr-btn{flex:1;border:1px solid var(--line);background:white;border-radius:6px;padding:6px 8px;font-size:12px;cursor:pointer;color:var(--ink)}.pr-btn:hover:not(:disabled){border-color:var(--cobalt);color:var(--cobalt)}.pr-btn:disabled{opacity:.4;cursor:not-allowed}.pr-btn-danger:hover:not(:disabled){border-color:var(--red);color:var(--red)}@media(max-width:760px){.app{display:block}.sidebar{width:auto}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}`;
}

/** Full standalone HTML page for the Port Registry light UI (sidebar + stats + ranges). */
export function renderPortRegistryPageHtml(view: PortRegistryView): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Port Registry</title>
<style>${portRegistryConsoleCss()}</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><h1>PORT</h1><div class="tag">registry</div></div>
    <nav class="nav" aria-label="Views">
      <a class="active" href="/port-registry">Port Registry</a>
    </nav>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="crumb">Environment · Dev tooling / <b>Port Registry</b></div>
      <div class="live"><i></i> local registry</div>
    </header>
    <section class="content">
      <div class="view-head"><p class="eyebrow">Environment · Dev tooling</p><h2>Port registry</h2></div>
      ${portRegistryStatsHtml(view)}
      <div class="panel pr-panel">${portRegistryHtml(view)}</div>
    </section>
  </main>
</div>
<script>
(function(){
  document.addEventListener('click',function(event){
    var btn=event.target.closest('[data-pr-action]');
    if(!btn||btn.disabled)return;
    var action=btn.getAttribute('data-pr-action');
    var rangeId=btn.getAttribute('data-range-id');
    var project=btn.getAttribute('data-project');
    btn.disabled=true;
    fetch('/port-registry/actions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({project:project,rangeId:rangeId,action:action})})
      .then(function(res){return res.json().then(function(body){return {ok:res.ok,body:body};});})
      .then(function(result){
        if(result.ok){window.location.reload();return;}
        btn.disabled=false;
        if(result.body&&result.body.needs_input){alert(result.body.prompt||'Needs input');}
        else{alert((result.body&&result.body.message)||'Action failed');}
      })
      .catch(function(){btn.disabled=false;});
  });
})();
</script>
</body>
</html>`;
}
