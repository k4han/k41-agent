import { createSignal } from "solid-js";
import { apiFetch, postJson } from "@/lib/api";
import type { SystemVersionInfo } from "@/types";

const [versionInfo, setVersionInfo] = createSignal<SystemVersionInfo | null>(null);
const [versionLoading, setVersionLoading] = createSignal(false);
const [isUpdateDialogOpen, setIsUpdateDialogOpen] = createSignal(false);
const [updateState, setUpdateState] = createSignal<"idle" | "updating" | "reconnecting" | "success" | "error">("idle");
const [updateError, setUpdateError] = createSignal("");

export {
  versionInfo,
  versionLoading,
  isUpdateDialogOpen,
  setIsUpdateDialogOpen,
  updateState,
  setUpdateState,
  updateError,
  setUpdateError,
};

export async function checkForUpdates(force = false): Promise<SystemVersionInfo | null> {
  setVersionLoading(true);
  try {
    const url = force ? "/dashboard-api/system/version?force=true" : "/dashboard-api/system/version";
    const data = await apiFetch<SystemVersionInfo>(url);
    setVersionInfo(data);
    return data;
  } catch (err) {
    console.error("Failed to check version:", err);
    return null;
  } finally {
    setVersionLoading(false);
  }
}

export function openUpdateDialog(): void {
  setIsUpdateDialogOpen(true);
}

export function closeUpdateDialog(): void {
  // Prevent closing during active update
  if (updateState() === "updating" || updateState() === "reconnecting") {
    return;
  }
  setIsUpdateDialogOpen(false);
  if (updateState() === "error") {
    setUpdateState("idle");
    setUpdateError("");
  }
}

export async function startSystemUpdate(): Promise<void> {
  setUpdateState("updating");
  setUpdateError("");

  try {
    await postJson("/dashboard-api/system/update");
    // Successfully queued update, server will stop and restart
    setUpdateState("reconnecting");
    await pollForServerRestart();
  } catch (err) {
    setUpdateState("error");
    setUpdateError(err instanceof Error ? err.message : "Failed to initiate update");
  }
}

async function pollForServerRestart(): Promise<void> {
  const start = Date.now();
  const timeoutMs = 120000; // 2 minutes max
  const initialCurrentVersion = versionInfo()?.current_version;

  while (Date.now() - start < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      const resp = await fetch("/health", { cache: "no-store" });
      if (resp.ok) {
        const data = (await resp.json()) as { status?: string; version?: string };
        const elapsed = Date.now() - start;
        // Server has restarted with new version, or is back up healthy after at least 8 seconds
        if (data.status === "ok" && (data.version !== initialCurrentVersion || elapsed > 8000)) {
          setUpdateState("success");
          // Re-fetch version info with force
          await checkForUpdates(true);
          setTimeout(() => {
            window.location.reload();
          }, 1500);
          return;
        }
      }
    } catch {
      // Server is restarting or temporarily unreachable, continue polling
    }
  }

  setUpdateState("error");
  setUpdateError("Server restart timed out. Please check your console or server.log.");
}
