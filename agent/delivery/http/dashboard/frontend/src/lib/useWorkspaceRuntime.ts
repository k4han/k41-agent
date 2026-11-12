import { batch, createEffect, createMemo, createSignal, on, onCleanup, onMount } from "solid-js";

import { apiFetch, postJson } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { createRefreshQueue, matchesWorkspaceEvent, WORKSPACE_REFRESH_DELAY_MS, WORKSPACE_IDLE_POLL_MS, WORKSPACE_RUNNING_POLL_MS, type WorkspaceEventIdentity } from "@/lib/workspaceRefresh";
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
  projectId: () => number | undefined = () => undefined, visible: () => boolean = () => true) {
  const [payload, setPayload] = createSignal<ThreadRuntimePayload>({ workspace: null, runtime: null });
  const [requesting, setRequesting] = createSignal(false);
  const [error, setError] = createSignal("");
  const busy = createMemo(() => requesting() || payloadTransitionBusy(payload()));
  let revision = 0;
  let controller = new AbortController();
  let refreshTimer: number | undefined;
  const [documentVisible, setDocumentVisible] = createSignal(document.visibilityState !== "hidden");
  const scope = () => threadId() ? `thread:${threadId()}` : projectId() ? `project:${projectId()}` : "";

  const readRuntime = async () => {
    const thread = threadId();
    const project = projectId();
    const currentScope = scope();
    const version = ++revision;
    if (!currentScope) return null;
    let result: ThreadRuntimePayload;
    if (thread) {
      result = await apiFetch<ThreadRuntimePayload>(`/dashboard-api/workspace/runtime/${encodeURIComponent(thread)}`, { signal: controller.signal });
    } else {
      const summary = await apiFetch<ProjectSummary>(`/dashboard-api/projects/${project}`, { signal: controller.signal });
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
  };

  let queue = createRefreshQueue(async () => { await readRuntime(); });
  const refresh = async (invalidate = true): Promise<ThreadRuntimePayload | null> => {
    const currentScope = scope();
    await queue.refresh(invalidate);
    return currentScope && currentScope === scope() ? payload() : null;
  };
  const refreshQuietly = () => { void refresh(false).catch(() => undefined); };
  const pollInterval = createMemo(() => !scope() || !documentVisible() ? 0 : payloadTransitionBusy(payload()) ? 2000
    : payload().workspace ? visible() ? WORKSPACE_RUNNING_POLL_MS : WORKSPACE_IDLE_POLL_MS : 0);
  createEffect(on(scope, () => {
    revision += 1;
    controller.abort();
    controller = new AbortController();
    queue.dispose();
    queue = createRefreshQueue(async () => { await readRuntime(); });
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
    batch(() => {
      setPayload({ workspace: null, runtime: null });
      setError("");
      setRequesting(false);
    });
    refreshQuietly();
  }));
  createEffect(() => {
    const interval = pollInterval();
    if (!interval) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      await refresh(false).catch(() => undefined);
      if (!stopped) timer = window.setTimeout(poll, interval);
    };
    timer = window.setTimeout(poll, interval);
    onCleanup(() => { stopped = true; window.clearTimeout(timer); });
  });
  onMount(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<WorkspaceEventIdentity>).detail;
      const current = { thread_id: threadId(), project_id: payload().workspace?.project_id ?? projectId(),
        project_workspace_id: payload().workspace?.project_workspace_id ?? undefined };
      const eventThread = detail?.thread_id || detail?.threadId;
      // A conversation can receive runtime events before its workspace is known or after it is rebound.
      const matchesScope = Boolean(eventThread && eventThread === current.thread_id)
        || current.project_workspace_id === undefined && current.project_id !== undefined && detail?.project_id === current.project_id;
      if (!matchesScope && !matchesWorkspaceEvent(detail, current)) return;
      revision++;
      if (!documentVisible() || refreshTimer !== undefined) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        void refresh(true).catch(() => undefined);
      }, WORKSPACE_REFRESH_DELAY_MS);
    };
    const visibilityChanged = () => {
      const next = document.visibilityState !== "hidden";
      setDocumentVisible(next);
      if (next) void refresh(true).catch(() => undefined);
      else { window.clearTimeout(refreshTimer); refreshTimer = undefined; }
    };
    const focused = () => { if (documentVisible()) refreshQuietly(); };
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_RUNTIME_CHANGED, changed);
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, changed);
    window.addEventListener("focus", focused);
    document.addEventListener("visibilitychange", visibilityChanged);
    onCleanup(() => {
      revision += 1;
      controller.abort();
      queue.dispose();
      window.clearTimeout(refreshTimer);
      window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_RUNTIME_CHANGED, changed);
      window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, changed);
      window.removeEventListener("focus", focused);
      document.removeEventListener("visibilitychange", visibilityChanged);
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
        if (thread === threadId()) { revision++; await refresh(true); }
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
          thread_id: thread, backend, project_workspace_id: binding.project_workspace_id, expected_generation: binding.runtime_generation,
          ...(previewToken ? { preview_token: previewToken } : {}),
        },
      );
      if (currentScope !== scope()) return;
      // Retain the accepted state even when the first status refresh loses its connection.
      revision++;
      setPayload((current) => ({ ...current, runtime: { status: "ready", generation: binding.runtime_generation!, transition: result.transition } }));
      await refresh(true);
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
          project_workspace_id: payload().transition_workspace_id || binding.project_workspace_id, expected_operation_id: operationId,
        });
      }
      if (currentScope === scope()) { revision++; await refresh(true); }
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
          ...(threadId() ? { thread_id: threadId() } : {}),
          ...(previewToken ? { preview_token: previewToken } : {}),
        },
      );
      if (currentScope !== scope()) return;
      revision++;
      await refresh(true);
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
