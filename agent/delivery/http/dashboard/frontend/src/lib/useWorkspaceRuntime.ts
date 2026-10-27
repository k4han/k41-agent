import { batch, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";

import { apiFetch, postJson } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { ProjectSummary, ThreadRuntimePayload, WorkspaceBackendKey, WorkspaceBinding } from "@/types";

export function runtimeTransitionBusy(runtime: ThreadRuntimePayload["runtime"]): boolean {
  const phase = runtime?.transition?.phase;
  return Boolean(phase && phase !== "completed" && phase !== "failed");
}

function payloadTransitionBusy(payload: ThreadRuntimePayload): boolean {
  const phase = payload.transition?.phase;
  return runtimeTransitionBusy(payload.runtime) || Boolean(phase && phase !== "completed" && phase !== "failed");
}

export function useWorkspaceRuntime(threadId: () => string, applyWorkspace: (binding: WorkspaceBinding) => void,
  projectId: () => number | undefined = () => undefined) {
  const [payload, setPayload] = createSignal<ThreadRuntimePayload>({ workspace: null, runtime: null });
  const [requesting, setRequesting] = createSignal(false);
  const [error, setError] = createSignal("");
  const busy = createMemo(() => requesting() || payloadTransitionBusy(payload()));
  let revision = 0;
  const scope = () => threadId() ? `thread:${threadId()}` : projectId() ? `project:${projectId()}` : "";

  const refresh = async () => {
    const thread = threadId();
    const project = projectId();
    const currentScope = scope();
    const version = ++revision;
    if (!currentScope) return null;
    let result: ThreadRuntimePayload;
    if (thread) {
      result = await apiFetch<ThreadRuntimePayload>(`/dashboard-api/workspace/runtime/${encodeURIComponent(thread)}`);
    } else {
      const summary = await apiFetch<ProjectSummary>(`/dashboard-api/projects/${project}`);
      result = { ...summary, runtime: summary.runtime || null,
        kind: summary.workspace.project_workspace_id && summary.migration_status === "ready" ? "shared" : undefined };
    }
    if (version !== revision || currentScope !== scope()) return null;
    const bindingChanged = JSON.stringify(result.workspace) !== JSON.stringify(payload().workspace);
    batch(() => {
      if (payloadTransitionBusy(payload()) && !payloadTransitionBusy(result)) setError("");
      setPayload(result);
      if (result.workspace && bindingChanged) applyWorkspace(result.workspace);
    });
    return result;
  };

  const refreshQuietly = () => { void refresh().catch(() => undefined); };
  createEffect(() => {
    scope();
    revision += 1;
    batch(() => {
      setPayload({ workspace: null, runtime: null });
      setError("");
      setRequesting(false);
    });
    refreshQuietly();
  });
  createEffect(() => {
    const switching = payloadTransitionBusy(payload());
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

  const switchBackend = async (backend: WorkspaceBackendKey, previewToken?: string) => {
    if (busy()) return;
    const binding = payload().workspace;
    const thread = threadId();
    const currentScope = scope();
    if (!binding) return;
    if (!binding.project_id) {
      const generation = payload().host_mirror?.generation;
      if (!generation || !thread) return;
      setRequesting(true); setError("");
      try {
        await postJson("/dashboard-api/workspace/runtime/switch", { thread_id: thread, backend, expected_generation: generation,
          ...(previewToken ? { preview_token: previewToken } : {}) });
        if (thread === threadId()) await refresh();
      } catch (reason) {
        if (thread === threadId()) setError(reason instanceof Error ? reason.message : "Failed to switch execution backend.");
        if (previewToken) throw reason;
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
          ...(previewToken ? { preview_token: previewToken } : {}),
        },
      );
      if (currentScope !== scope()) return;
      // Retain the accepted state even when the first status refresh loses its connection.
      setPayload((current) => ({ ...current, runtime: { status: "ready", generation: binding.runtime_generation!, transition: result.transition } }));
      await refresh();
    } catch (reason) {
      if (currentScope === scope()) {
        setError(reason instanceof Error ? reason.message : "Failed to switch execution backend.");
        refreshQuietly();
      }
      if (previewToken) throw reason;
    } finally {
      if (currentScope === scope()) setRequesting(false);
    }
  };
  const abandon = async () => {
    const binding = payload().workspace;
    const thread = threadId();
    const currentScope = scope();
    const operationId = payload().runtime?.transition?.operation_id || payload().transition?.operation_id;
    if (!binding || !operationId) return;
    setRequesting(true);
    setError("");
    try {
      if (!binding.project_id) {
        const generation = payload().host_mirror?.generation;
        if (!generation || !thread) return;
        await postJson("/dashboard-api/workspace/runtime/abandon", { thread_id: thread, expected_generation: generation,
          expected_operation_id: operationId });
      } else if (binding.project_workspace_id) {
        await postJson(`/dashboard-api/projects/${binding.project_id}/runtime/abandon`, {
          project_workspace_id: binding.project_workspace_id, expected_operation_id: operationId,
        });
      }
      if (currentScope === scope()) await refresh();
    } catch (reason) {
      if (currentScope === scope()) {
        setError(reason instanceof Error ? reason.message : "Failed to abandon the switch.");
        refreshQuietly();
      }
    } finally {
      if (currentScope === scope()) setRequesting(false);
    }
  };
  const confirmRecovery = async (action: "restore" | "local", previewToken?: string) => {
    if (busy()) return;
    const binding = payload().workspace;
    const currentScope = scope();
    if (!binding?.project_id || !binding.project_workspace_id || !binding.runtime_generation) return;
    setRequesting(true);
    setError("");
    try {
      await postJson<{ workspace: WorkspaceBinding }>(
        `/dashboard-api/projects/${binding.project_id}/runtime/recovery`, {
          action, project_workspace_id: binding.project_workspace_id, expected_generation: binding.runtime_generation,
          ...(previewToken ? { preview_token: previewToken } : {}),
        },
      );
      if (currentScope !== scope()) return;
      await refresh();
    } catch (reason) {
      if (currentScope === scope()) {
        setError(reason instanceof Error ? reason.message : "Failed to recover the workspace.");
        refreshQuietly();
      }
      if (previewToken) throw reason;
    } finally {
      if (currentScope === scope()) setRequesting(false);
    }
  };
  return { payload, busy, error, refresh, switchBackend, abandon, confirmRecovery };
}
