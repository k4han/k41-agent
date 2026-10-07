import { createSignal } from "solid-js";
import { apiFetch } from "@/lib/api";
import type { SystemVersionInfo } from "@/types";

const [versionInfo, setVersionInfo] = createSignal<SystemVersionInfo | null>(null);
const [versionLoading, setVersionLoading] = createSignal(false);
const [versionCheckError, setVersionCheckError] = createSignal("");
const [isUpdateDialogOpen, setIsUpdateDialogOpen] = createSignal(false);
const [updateState, setUpdateState] = createSignal<"idle" | "updating" | "reconnecting" | "success" | "error">("idle");
const [updateError, setUpdateError] = createSignal("");
let versionRequestId = 0;

type UpdateStatus = {
  status: "queued" | "running" | "updated" | "current" | "cancelled" | "failed";
  latest_version?: string;
  current_version?: string;
  error?: string | null;
};

function timeoutSignal(ms: number): AbortSignal {
  if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

export {
  versionInfo,
  versionLoading,
  versionCheckError,
  isUpdateDialogOpen,
  setIsUpdateDialogOpen,
  updateState,
  setUpdateState,
  updateError,
  setUpdateError,
};

export async function checkForUpdates(force = false): Promise<SystemVersionInfo | null> {
  const requestId = ++versionRequestId;
  setVersionLoading(true);
  setVersionCheckError("");
  try {
    const url = force ? "/dashboard-api/system/version?force=true" : "/dashboard-api/system/version";
    const data = await apiFetch<SystemVersionInfo>(url, { signal: timeoutSignal(35000) });
    if (requestId === versionRequestId) {
      setVersionInfo(data);
      setVersionCheckError(data.error || "");
    }
    return data;
  } catch (err) {
    console.error("Failed to check version:", err);
    if (requestId === versionRequestId) {
      setVersionCheckError(err instanceof Error ? err.message : "Failed to check for updates");
    }
    return null;
  } finally {
    if (requestId === versionRequestId) setVersionLoading(false);
  }
}

export function openUpdateDialog(): void {
  setIsUpdateDialogOpen(true);
}

export function closeUpdateDialog(): void {
  if (updateState() === "updating" || updateState() === "reconnecting") return;
  setIsUpdateDialogOpen(false);
  if (updateState() === "error") {
    setUpdateState("idle");
    setUpdateError("");
  }
}

export async function startSystemUpdate(): Promise<void> {
  if (["updating", "reconnecting", "success"].includes(updateState())) return;
  const initialCurrentVersion = versionInfo()?.current_version;
  const targetVersion = versionInfo()?.latest_version;
  setUpdateState("updating");
  setUpdateError("");

  try {
    const result = await apiFetch<{ status: string; update_id?: string }>(
      "/dashboard-api/system/update", { method: "POST", signal: timeoutSignal(15000) },
    );
    if (result.status !== "started") throw new Error("The update process was not started.");
    if (!result.update_id) setUpdateState("reconnecting");
    await pollForServerRestart(result.update_id, initialCurrentVersion, targetVersion);
  } catch (err) {
    setUpdateState("error");
    setUpdateError(err instanceof Error ? err.message : "Failed to initiate update");
  }
}

async function pollForServerRestart(
  updateId: string | undefined,
  initialCurrentVersion: string | undefined,
  targetVersion: string | undefined,
): Promise<void> {
  const start = Date.now();
  const timeoutMs = 600000; // Downloads and dependency syncs can take several minutes.
  let status: UpdateStatus | null = null;
  let hasSeenServerDown = false;

  while (Date.now() - start < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    if (updateId) {
      try {
        status = await apiFetch<UpdateStatus>(
          `/dashboard-api/system/update/status?update_id=${encodeURIComponent(updateId)}`,
          { cache: "no-store", signal: timeoutSignal(5000) },
        );
        setUpdateState(status.status === "queued" || status.status === "running" ? "updating" : "reconnecting");
      } catch {
        // Status is temporarily unavailable during restart, or on older releases.
        setUpdateState("reconnecting");
        hasSeenServerDown = true;
      }
      if (status?.status === "failed" || status?.status === "cancelled") {
        throw new Error(status.error || "The update failed. Check ~/.k41-agent/update.log for details.");
      }
    }
    let ready = false;
    try {
      const resp = await fetch("/health", { cache: "no-store", signal: timeoutSignal(5000) });
      if (resp.ok) {
        const data = (await resp.json()) as { status?: string; version?: string };
        if (data.status === "ok") {
          const expectedVersion = status?.status === "current"
            ? status.current_version : status?.latest_version || targetVersion;
          // A rollback also restarts the server. Success requires the installed release.
          // Legacy backends without a status endpoint cannot distinguish a restart
          // when force-reinstalling the same version by version alone, so require
          // observed downtime in that case to avoid an instant false success or a
          // 10-minute timeout.
          const legacyReinstalledSameVersion = Boolean(
            !status && initialCurrentVersion && targetVersion
            && initialCurrentVersion === targetVersion
            && data.version === expectedVersion && hasSeenServerDown,
          );
          ready = Boolean(expectedVersion && data.version === expectedVersion && (
            status?.status === "updated" || status?.status === "current" ||
            (!status && initialCurrentVersion && data.version !== initialCurrentVersion) ||
            legacyReinstalledSameVersion
          ));
        }
      } else {
        setUpdateState("reconnecting");
        hasSeenServerDown = true;
      }
    } catch {
      // Server is restarting or temporarily unreachable.
      setUpdateState("reconnecting");
      hasSeenServerDown = true;
    }
    if (ready) {
      setUpdateState("success");
      // A GitHub outage must not delay the reload after a successful update.
      setVersionInfo((info) => info ? {
        ...info, current_version: (status?.status === "current"
          ? status.current_version : status?.latest_version || targetVersion)!,
      } : info);
      void checkForUpdates(true);
      setTimeout(() => window.location.reload(), 1500);
      return;
    }
  }

  setUpdateState("error");
  setUpdateError("The update is taking longer than expected. Check ~/.k41-agent/update.log and server.log before retrying.");
}
