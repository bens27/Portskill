export type PortskillRangeState = "reserved" | "active" | "released";

export type PortskillTailnetMode = "serve" | "funnel" | "none" | null;

export type PortskillAction = "start" | "stop" | "release";

export interface PortskillRawLifecycle {
  readonly pid?: number | null;
  readonly pgid?: number | null;
  readonly started_at?: string | null;
  readonly stopped_at?: string | null;
}

export interface PortskillRawTailnet {
  readonly mode?: string | null;
  readonly port?: number | null;
  readonly configured_at?: string | null;
}

export interface PortskillRawRange {
  readonly id: string;
  readonly start: number;
  readonly end: number;
  readonly state: string;
  readonly note?: string | null;
  readonly reserved_at?: string | null;
  readonly tailnet?: PortskillRawTailnet;
  readonly lifecycle?: PortskillRawLifecycle;
}

export interface PortskillRawProject {
  readonly ranges: readonly PortskillRawRange[];
}

export interface PortskillRawState {
  readonly version?: number;
  readonly pool: { readonly start: number; readonly end: number };
  readonly projects: Readonly<Record<string, PortskillRawProject>>;
}

export interface PortskillActionResult {
  readonly ok: boolean;
  readonly message?: string;
}
