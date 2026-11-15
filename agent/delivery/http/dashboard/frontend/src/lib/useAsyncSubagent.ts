import { batch, createEffect, createMemo, createSignal, on, onCleanup, type Accessor } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import type { SubagentActivity, TranscriptItem, TranscriptTool } from "@/components/Transcript";
import { apiFetch, fetchWithCsrf } from "@/lib/api";
import { asyncTaskPath, liveAsyncTask, mergeAsyncTask, type AsyncTask } from "@/lib/asyncSubagents";
import { readNDJSONStream, mergeToolActivity, type AssistantIdRef, type StreamCallbacks } from "@/lib/chatStreamHandler";
import { handleProtocolEvent } from "@/lib/protocolStreamHandler";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { SSE_RECONNECT_DELAY_MS } from "@/lib/uiConstants";

export function useAsyncSubagent(activity: Accessor<SubagentActivity>, opened: Accessor<boolean>) {
  const [task, setTask] = createSignal<AsyncTask>();
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [live, setLive] = createStore<{ items: Array<TranscriptItem & { id: number }> }>({ items: [] });
  const setLiveItems = (update: (items: typeof live.items) => typeof live.items) =>
    setLive("items", reconcile(update(live.items), { key: "id" }));
  const enabled = () => activity().execution_mode === "async" && !activity().read_only;
  const identity = createMemo(() => `${activity().parent_thread_id}/${activity().task_id || activity().delegation_id}`);
  const currentTask = createMemo(() => enabled() ? mergeAsyncTask(activity() as AsyncTask, task() || {}) : undefined);
  let snapshotRequest: AbortController | undefined;
  let nextId = 0;
  let refs: AssistantIdRef = { id: null };
  let streamed = { received: false };
  let streamRunId = "";

  const refresh = async () => {
    if (!enabled()) return;
    snapshotRequest?.abort();
    const controller = new AbortController(), key = identity();
    snapshotRequest = controller;
    try {
      const value = await apiFetch<AsyncTask>(asyncTaskPath(activity().parent_thread_id, activity().task_id || activity().delegation_id), { signal: controller.signal });
      if (!controller.signal.aborted && key === identity()) {
        setTask((current) => mergeAsyncTask(current || value, value));
        setError("");
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Task could not be loaded.");
    }
  };

  const perform = async (action: string, payload?: unknown) => {
    if (busy() || !enabled()) return false;
    setBusy(true);
    setError("");
    const key = identity();
    try {
      const value = await apiFetch<AsyncTask>(`${asyncTaskPath(activity().parent_thread_id, activity().task_id || activity().delegation_id)}/${action}`,
        { method: "POST", json: payload });
      if (key === identity()) {
        setTask((current) => mergeAsyncTask(current || value, value));
        await refresh();
      }
      return true;
    } catch (failure) {
      if (key === identity()) setError(failure instanceof Error ? failure.message : "Task operation failed.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  createEffect(on(identity, () => {
    batch(() => {
      setTask(undefined);
      setLive("items", []);
      setError("");
    });
    nextId = 0;
    refs = { id: null };
    streamed = { received: false };
    streamRunId = "";
    void refresh();
  }));
  const updated = (event: Event) => {
    const value = (event as CustomEvent<AsyncTask>).detail;
    if (enabled() && value?.task_id === (activity().task_id || activity().delegation_id) && value.parent_thread_id === activity().parent_thread_id) {
      setTask((current) => mergeAsyncTask(current || { ...activity(), ...value } as AsyncTask, value));
      void refresh();
    }
  };
  window.addEventListener(CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, updated);
  window.addEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, refresh);
  onCleanup(() => {
    snapshotRequest?.abort();
    window.removeEventListener(CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, updated);
    window.removeEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, refresh);
  });

  const streamIdentity = createMemo(() => JSON.stringify([identity(), opened(), enabled(), currentTask()?.thread_id,
    currentTask()?.run_id, currentTask()?.status === "running"]));
  createEffect(on(streamIdentity, () => {
    if (!opened() || !enabled() || currentTask()?.status !== "running") return;
    const controller = new AbortController();
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    onCleanup(() => {
      controller.abort();
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    });
    const patch = (id: number, change: (item: TranscriptItem & { id: number }) => TranscriptItem & { id: number }) => {
      setLiveItems((items) => items.map((item) => item.id === id ? change(item) : item));
    };
    const toolPatch = (id: string, change: Partial<TranscriptTool>) => setLiveItems((items) => items.map((item) =>
      item.type === "tool" && item.tool_call_id === id ? mergeToolActivity(item, change) : item));
    const callbacks: StreamCallbacks = {
      appendItem: (item) => { const id = nextId++; setLiveItems((items) => [...items, { ...item, id }]); return id; },
      updateMessage: (id, text) => patch(id, (item) => item.type === "message" || item.type === "thinking" ? { ...item, text: item.text + text } : item),
      replaceMessage: (id, text) => patch(id, (item) => item.type === "message" ? { ...item, text } : item),
      finishThinking: (id, durationMs) => patch(id, (item) => item.type === "thinking" ? { ...item, durationMs } : item),
      removeItem: (id) => setLiveItems((items) => items.filter((item) => item.id !== id)),
      updateToolResult: (id, _name, result) => toolPatch(id, { result, resultReceived: true }),
      updateToolActivity: toolPatch, updatePlanReviewResult: () => {}, updateUserInputRequestResult: () => {},
      setRecursionLimitReached: () => {}, onThreadCreated: () => {},
    };
    const thread = { id: currentTask()!.thread_id, message: "" };
    const connect = async () => {
      try {
        const response = await fetchWithCsrf("/v2/api/chat/events/reconnect", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
          body: JSON.stringify({ thread_id: thread.id }),
        });
        if (response.ok && response.body) await readNDJSONStream(response.body, (event) => {
          if (controller.signal.aborted) return;
          const runId = String(event.event_id || "").split(":")[0];
          if (runId && streamRunId !== runId) {
            streamRunId = runId;
            refs = { id: null };
            streamed = { received: false };
          }
          handleProtocolEvent(event, refs, streamed, thread, callbacks);
        });
      } catch (failure) {
        if (!controller.signal.aborted) console.debug("Subagent live stream unavailable", failure);
      }
      if (!controller.signal.aborted) {
        void refresh();
        // Task admission can be published before its stream session exists.
        retryTimer = setTimeout(() => void connect(), SSE_RECONNECT_DELAY_MS);
      }
    };
    void connect();
  }));

  return { task: currentTask, enabled, busy, error, refresh, perform, liveItems: () => live.items,
    activity: () => currentTask() || activity() };
}

export function useAsyncTasks(threadId: Accessor<string>) {
  const [count, setCount] = createSignal(0);
  const [state, setState] = createStore<{ tasks: AsyncTask[] }>({ tasks: [] });
  const [error, setError] = createSignal("");
  let reload = async () => {};
  createEffect(on(threadId, (parent) => {
    setCount(0);
    setState("tasks", []);
    setError("");
    if (!parent) return;
    let request: AbortController | undefined;
    const refresh = async () => {
      request?.abort();
      const controller = new AbortController();
      request = controller;
      try {
        const tasks: AsyncTask[] = [];
        let offset = 0, total = 0, activeCount = 0;
        do {
          const result = await apiFetch<{ tasks: AsyncTask[]; total: number; active_count: number }>(
            `${asyncTaskPath(parent)}?status=active&limit=100&offset=${offset}`, { signal: controller.signal });
          if (controller.signal.aborted) return;
          tasks.push(...result.tasks);
          offset += result.tasks.length;
          total = result.total;
          activeCount = result.active_count;
          if (!result.tasks.length) break;
        } while (offset < total);
        if (!controller.signal.aborted) {
          const current = new Map(state.tasks.map((task) => [task.task_id, task]));
          const unique = new Map(tasks.filter((task) => liveAsyncTask(task.status))
            .map((task) => [task.task_id, mergeAsyncTask(current.get(task.task_id) || task, task)]));
          setState("tasks", reconcile(Array.from(unique.values()), { key: "task_id" }));
          setCount(activeCount);
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Background tasks could not be loaded.");
      }
    };
    reload = refresh;
    const updated = (event: Event) => {
      const value = (event as CustomEvent<SubagentActivity>).detail;
      if (value?.root_thread_id === parent || value?.parent_thread_id === parent) void refresh();
    };
    void refresh();
    window.addEventListener(CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, updated);
    window.addEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, refresh);
    onCleanup(() => {
      request?.abort();
      reload = async () => {};
      window.removeEventListener(CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, updated);
      window.removeEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, refresh);
    });
  }));
  return { count, tasks: () => state.tasks, error, refresh: () => reload() };
}
