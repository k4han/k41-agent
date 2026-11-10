import { apiFetch, patchJson, postJson } from "@/lib/api";

const BASE = "/dashboard-api/instabox";

export type InstaboxMachine = {
  id: string;
  name: string | null;
  state: string | null;
  status: string;
  desired_state?: string | null;
  operation_id?: string | null;
  template?: string | null;
  snapshot?: string | null;
  vcpus?: number | null;
  mem_mib?: number | null;
  disk_gib?: number | null;
  ready?: boolean;
  metadata?: Record<string, string>;
  auto_suspend_after_seconds?: number | null;
  last_error?: unknown;
};

export type InstaboxOperation = {
  id: number;
  idempotency_key: string;
  kind: string;
  operation_id: string | null;
  machine_id: string | null;
  status: string;
  phase: string | null;
  result: unknown;
  error: { code?: string; message?: string } | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type InstaboxPort = {
  port: number;
  access: "public" | "private";
  name?: string | null;
  protocol?: string | null;
  url?: string | null;
  has_saved_token?: boolean;
};

export type InstaboxPortLink = {
  url: string;
  access: "public" | "private";
  token_missing?: boolean;
};

export type InstaboxSnapshot = {
  id: string;
  name: string;
  description?: string | null;
  state: "pending" | "ready" | "failed" | string;
  sourceMachineId?: string | null;
  createdAt?: string | null;
  sizeBytes?: number | null;
};

export type InstaboxDesktopState = {
  installed?: boolean;
  installing?: boolean;
  running?: boolean;
  failed?: boolean;
  controller?: string | null;
  app_controller: string | null;
  app_controller_expires_in: number;
};

export const ACTIVE_OPERATION_STATUSES = new Set(["submitting", "queued", "running", "pending"]);

const machinePath = (machineId: string) => `${BASE}/machines/${encodeURIComponent(machineId)}`;

function deleteWithBody<T>(path: string, json: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE", json });
}

export const instaboxApi = {
  machines: () => apiFetch<{ machines: InstaboxMachine[] }>(`${BASE}/machines`),
  machine: (machineId: string) => apiFetch<InstaboxMachine>(machinePath(machineId)),
  action: (machineId: string, action: "start" | "stop" | "suspend" | "resume") =>
    postJson<InstaboxOperation>(`${machinePath(machineId)}/${action}`),
  fork: (machineId: string, name?: string) =>
    postJson<InstaboxOperation>(`${machinePath(machineId)}/fork`, { name: name || null }),
  deleteMachine: (machineId: string, confirm: string) =>
    deleteWithBody<InstaboxOperation>(machinePath(machineId), { confirm }),

  operations: (machineId?: string, active = false) => {
    const params = new URLSearchParams();
    if (machineId) params.set("machine_id", machineId);
    if (active) params.set("active", "true");
    return apiFetch<{ operations: InstaboxOperation[] }>(`${BASE}/operations?${params.toString()}`);
  },

  ports: (machineId: string) => apiFetch<{ ports: InstaboxPort[] }>(`${machinePath(machineId)}/ports`),
  publishPort: (machineId: string, port: number, access: "public" | "private", name?: string) =>
    postJson<InstaboxPort & { link: InstaboxPortLink }>(`${machinePath(machineId)}/ports`, { port, access, name: name || null }),
  setPortAccess: (machineId: string, port: number, access: "public" | "private") =>
    patchJson<InstaboxPort & { link: InstaboxPortLink }>(`${machinePath(machineId)}/ports/${port}`, { access }),
  rotatePort: (machineId: string, port: number) =>
    postJson<InstaboxPort & { link: InstaboxPortLink }>(`${machinePath(machineId)}/ports/${port}/rotate`),
  portLink: (machineId: string, port: number) =>
    apiFetch<InstaboxPortLink>(`${machinePath(machineId)}/ports/${port}/link`),
  closePort: (machineId: string, port: number) =>
    apiFetch<{ closed: boolean }>(`${machinePath(machineId)}/ports/${port}`, { method: "DELETE" }),

  snapshots: () => apiFetch<{ snapshots: InstaboxSnapshot[] }>(`${BASE}/snapshots`),
  createSnapshot: (machineId: string, name: string, description?: string) =>
    postJson<InstaboxOperation>(`${machinePath(machineId)}/snapshots`, { name, description: description || null }),
  deleteSnapshot: (snapshotId: string, confirm: string) =>
    deleteWithBody<{ deleted: boolean }>(`${BASE}/snapshots/${encodeURIComponent(snapshotId)}`, { confirm }),
  machineFromSnapshot: (snapshotId: string, name?: string) =>
    postJson<InstaboxOperation>(`${BASE}/snapshots/${encodeURIComponent(snapshotId)}/machines`, { name: name || null }),
  restore: (machineId: string, snapshotId: string, confirmMachine: string, confirmSnapshot: string) =>
    postJson<InstaboxOperation>(`${machinePath(machineId)}/restore`, {
      snapshot_id: snapshotId,
      confirm_machine: confirmMachine,
      confirm_snapshot: confirmSnapshot,
    }),

  desktop: (machineId: string) => apiFetch<InstaboxDesktopState>(`${machinePath(machineId)}/desktop`),
  startDesktop: (machineId: string) => postJson<InstaboxDesktopState>(`${machinePath(machineId)}/desktop/start`, {}),
  takeControl: (machineId: string) => postJson<InstaboxDesktopState>(`${machinePath(machineId)}/desktop/takeover`),
  heartbeat: (machineId: string) => postJson<InstaboxDesktopState>(`${machinePath(machineId)}/desktop/heartbeat`),
  handBack: (machineId: string) => postJson<InstaboxDesktopState>(`${machinePath(machineId)}/desktop/handback`),
  viewerTicket: (machineId: string) => postJson<{ ticket: string }>(`${machinePath(machineId)}/desktop/viewer-ticket`),
};

export function instaboxViewerUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${BASE}/desktop/viewer`;
}

export function instaboxMachineHref(machineId: string): string {
  return `/settings/sandboxes/instabox/${encodeURIComponent(machineId)}`;
}

export function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
