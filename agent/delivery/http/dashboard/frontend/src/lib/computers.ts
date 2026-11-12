import { apiFetch, postJson } from "@/lib/api";

const BASE = "/dashboard-api/computers";

/** Desktop state: backend fields plus the dashboard's own control lock. */
export type ComputerState = {
  installed?: boolean;
  installing?: boolean;
  running?: boolean;
  failed?: boolean;
  controller?: string | null;
  app_controller: string | null;
  app_controller_expires_in: number;
};

const computerPath = (backend: string, locator: string) =>
  `${BASE}/${encodeURIComponent(backend)}/${encodeURIComponent(locator)}`;

export const computerApi = {
  status: (backend: string, locator: string, signal?: AbortSignal) => apiFetch<ComputerState>(computerPath(backend, locator), { signal }),
  start: (backend: string, locator: string) => postJson<ComputerState>(`${computerPath(backend, locator)}/start`, {}),
  takeControl: (backend: string, locator: string) => postJson<ComputerState>(`${computerPath(backend, locator)}/takeover`),
  heartbeat: (backend: string, locator: string) => postJson<ComputerState>(`${computerPath(backend, locator)}/heartbeat`),
  handBack: (backend: string, locator: string) => postJson<ComputerState>(`${computerPath(backend, locator)}/handback`),
  viewerTicket: (backend: string, locator: string) =>
    postJson<{ ticket: string }>(`${computerPath(backend, locator)}/viewer-ticket`),
};

export function computerViewerUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${BASE}/viewer`;
}
