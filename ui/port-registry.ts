export type PortRegistryRangeState = "reserved" | "active" | "released";

export type PortRegistryTailnetMode = "serve" | "funnel" | "none" | null;

export type PortRegistryAction = "start" | "stop" | "release";

export interface PortRegistryRawLifecycle {
  readonly pid?: number | null;
  readonly pgid?: number | null;
  readonly started_at?: string | null;
  readonly stopped_at?: string | null;
}

export interface PortRegistryRawTailnet {
  readonly mode?: string | null;
  readonly port?: number | null;
  readonly configured_at?: string | null;
}

export interface PortRegistryRawRange {
  readonly id: string;
  readonly start: number;
  readonly end: number;
  readonly state: string;
  readonly note?: string | null;
  readonly reserved_at?: string | null;
  readonly tailnet?: PortRegistryRawTailnet;
  readonly lifecycle?: PortRegistryRawLifecycle;
}

export interface PortRegistryRawProject {
  readonly ranges: readonly PortRegistryRawRange[];
}

export interface PortRegistryRawState {
  readonly version?: number;
  readonly pool: { readonly start: number; readonly end: number };
  readonly projects: Readonly<Record<string, PortRegistryRawProject>>;
}

export interface PortRegistryActionResult {
  readonly ok: boolean;
  readonly message?: string;
}
