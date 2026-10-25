import { batch, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";

import { apiFetch, postJson } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { ThreadRuntimePayload, WorkspaceBackendKey, WorkspaceBinding } from "@/types";

export function runtimeTransitionBusy(runtime: ThreadRuntimePayload["runtime"]): boolean {
  const phase = runtime?.transition?.phase;
  return Boolean(phase && phase !== "completed" && phase !== "failed");
}

export function useWorkspaceRuntime(threadId: () => string, applyWorkspace: (binding: WorkspaceBinding) => void) {
  const [payload, setPayload] = createSignal<ThreadRuntimePayload>({ workspace: null, runtime: null });
  const [requesting, setRequesting] = createSignal(false);
  const [error, setError] = createSignal("");
  const busy = createMemo(() => requesting() || runtimeTransitionBusy(payload().runtime));
  let revision = 0;

  const refresh = async () => {
    const thread = threadId();
    const version = ++revision;
    if (!thread) return null;
    const result = await apiFetch<ThreadRuntimePayload>(`/dashboard-api/workspace/runtime/${encodeURIComponent(thread)}`);
    if (version !== revision || thread !== threadId()) return null;
    const bindingChanged = JSON.stringify(result.workspace) !== JSON.stringify(payload().workspace);
    batch(() => {
      if (runtimeTransitionBusy(payload().runtime) && !runtimeTransitionBusy(result.runtime)) setError("");
      setPayload(result);
      if (result.workspace && bindingChanged) applyWorkspace(result.workspace);
    });
    return result;
  };

  const refreshQuietly = () => { void refresh().catch(() => undefined); };
  createEffect(() => {
    threadId();
    revision += 1;
    batch(() => {
      setPayload({ workspace: null, runtime: null });
      setError("");
      setRequesting(false);
    });
    refreshQuietly();
  });
  createEffect(() => {
    const switching = runtimeTransitionBusy(payload().runtime);
    if (!payload().workspace && !switching) return;
    const timer = window.setInterval(refreshQuietly, switching ? 2000 : 5000);
    onCleanup(() => window.clearInterval(timer));
  });
  onMount(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ project_workspace_id?: number }>).detail;
      if (!payload().workspace || !detail?.project_workspace_id || detail.project_workspace_id === payload().workspace?.project_workspace_id) refreshQuietly();
    };
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_RUNTIME_CHANGED, changed);
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, changed);
    window.addEventListener("focus", refreshQuietly);
    onCleanup(() => {
      revision += 1;
      window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_RUNTIME_CHANGED, changed);
      window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, changed);
      window.removeEventListener("focus", refreshQuietly);
    });
  });

  const switchBackend = async (backend: WorkspaceBackendKey) => {
    if (busy()) return;
    const binding = payload().workspace;
    const thread = threadId();
    if (!binding) return;
    if (!binding.project_id) {
      const generation = payload().host_mirror?.generation;
      if (!generation || !thread) return;
      setRequesting(true); setError("");
      try {
        await postJson("/dashboard-api/workspace/runtime/switch", { thread_id: thread, backend, expected_generation: generation });
        if (thread === threadId()) await refresh();
      } catch (reason) {
        if (thread === threadId()) setError(reason instanceof Error ? reason.message : "Failed to switch execution backend.");
      } finally { if (thread === threadId()) setRequesting(false); }
      return;
    }
    if (!binding.project_workspace_id || !binding.runtime_generation) return;
    setRequesting(true);
    setError("");
    try {
      const result = await postJson<{ transition: NonNullable<ThreadRuntimePayload["runtime"]>["transition"] }>(
        `/dashboard-api/projects/${binding.project_id}/runtime/switch`, {
          backend, project_workspace_id: binding.project_workspace_id, expected_generation: binding.runtime_generation,
        },
      );
      if (thread !== threadId()) return;
      // Retain the accepted state even when the first status refresh loses its connection.
      setPayload((current) => ({ ...current, runtime: { status: "ready", generation: binding.runtime_generation!, transition: result.transition } }));
      await refresh();
    } catch (reason) {
      if (thread === threadId()) {
        setError(reason instanceof Error ? reason.message : "Failed to switch execution backend.");
        refreshQuietly();
      }
    } finally {
      if (thread === threadId()) setRequesting(false);
    }
  };
  const confirmRecovery = async (action: "restore" | "local") => {
    if (busy()) return;
    const binding = payload().workspace;
    const thread = threadId();
    if (!binding?.project_id || !binding.project_workspace_id || !binding.runtime_generation) return;
    setRequesting(true);
    setError("");
    try {
      await postJson<{ workspace: WorkspaceBinding }>(
        `/dashboard-api/projects/${binding.project_id}/runtime/recovery`, {
          action, project_workspace_id: binding.project_workspace_id, expected_generation: binding.runtime_generation,
        },
      );
      if (thread !== threadId()) return;
      await refresh();
    } catch (reason) {
      if (thread === threadId()) {
        setError(reason instanceof Error ? reason.message : "Failed to recover the workspace.");
        refreshQuietly();
      }
    } finally {
      if (thread === threadId()) setRequesting(false);
    }
  };
  return { payload, busy, error, refresh, switchBackend, confirmRecovery };
}
