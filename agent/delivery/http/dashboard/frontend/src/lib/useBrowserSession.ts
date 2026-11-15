import { batch, createEffect, createMemo, createSignal, on, onCleanup, onMount, untrack } from "solid-js";

import { apiFetch } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { createRefreshQueue, WORKSPACE_IDLE_POLL_MS, WORKSPACE_REFRESH_DELAY_MS } from "@/lib/workspaceRefresh";

export type BrowserSessionSummary = { thread_id: string; agent_name: string | null; generation: string };

export type AsyncBrowserHandoff = {
  thread_id: string;
  parent_thread_id: string;
  agent_name: string;
  task_id: string;
  run_id: string;
  interrupt_id: string;
  handoff_id: string;
  generation: string;
  reason: string;
};

export type BrowserStatus = {
  sessions?: BrowserSessionSummary[];
  pending_handoffs?: AsyncBrowserHandoff[];
  open: boolean;
  profile_id: string | null;
  profile_mode?: "inherit" | "profile";
  selected_profile_id?: string | null;
  generation: string | null;
  revision?: number;
  mode: "agent" | "human";
  tabs: { id: string; title: string; url: string }[];
  handoff: { handoff_id: string; reason: string; expired?: boolean } | null;
  handoff_expired: boolean;
  images: { artifact_id: string; image_url: string }[];
};

export type BrowserSession = ReturnType<typeof useBrowserSession>;

export function useBrowserSession(options: {
  threadId: () => string | null;
  visible: () => boolean;
  active: () => boolean;
  busy: () => boolean;
  onOpen: () => void;
  onHandoffChange: (pending: boolean, thread: string) => void;
  onSessionChange?: (active: boolean, thread: string | null) => void;
}) {
  const [status, setStatus] = createSignal<BrowserStatus | null>(null);
  const [discoveredSessions, setDiscoveredSessions] = createSignal<BrowserSessionSummary[]>([]);
  const [handoffs, setHandoffs] = createSignal<AsyncBrowserHandoff[]>([]);
  const [requestedSession, setRequestedSession] = createSignal<{ thread_id: string } | null>(null);
  const sessions = createMemo<BrowserSessionSummary[]>(() => {
    const current = status(), thread = options.threadId();
    if (!current || !thread) return [];
    const discovered = discoveredSessions();
    const children = discovered.filter(item => item.thread_id !== thread);
    for (const pending of handoffs()) {
      if (pending.thread_id !== thread && !children.some(item => item.thread_id === pending.thread_id)) {
        children.push({ thread_id: pending.thread_id, agent_name: pending.agent_name, generation: pending.generation });
      }
    }
    return current.open && current.generation
      ? [{ thread_id: thread, agent_name: discovered.find(item => item.thread_id === thread)?.agent_name ?? null,
          generation: current.generation }, ...children] : children;
  });
  const sessionKey = createMemo(() => sessions().map(item => `${item.thread_id}:${item.generation}`).join("|"));
  const hasOpenSession = () => sessions().length > 0;
  const [error, setError] = createSignal("");
  const [connected, setConnected] = createSignal(false);
  const [documentVisible, setDocumentVisible] = createSignal(document.visibilityState !== "hidden");
  let revision = 0;
  let discoveryRevision = 0;
  let announcedHandoff = "";
  let announcedAsyncHandoffs = new Set<string>();
  let queue: ReturnType<typeof createRefreshQueue> | undefined;
  let refreshTimer: number | undefined;
  let dirty = false;

  const applyStatus = (next: BrowserStatus, thread: string) => {
    if (options.threadId() !== thread) return;
    const previous = untrack(status);
    if (previous?.generation === next.generation && (next.revision ?? 0) < (previous?.revision ?? 0)) return;
    revision++;
    setStatus(next);
    setError("");
    if (next.handoff || !options.busy()) options.onHandoffChange(Boolean(next.handoff), thread);
    if (next.handoff && next.handoff.handoff_id !== announcedHandoff) {
      announcedHandoff = next.handoff.handoff_id;
      setRequestedSession({ thread_id: thread });
      options.onOpen();
    }
  };
  createEffect(on(handoffs, (pending) => {
    const fresh = pending.find(item => !announcedAsyncHandoffs.has(item.handoff_id));
    for (const item of pending) announcedAsyncHandoffs.add(item.handoff_id);
    if (fresh) {
      setRequestedSession({ thread_id: fresh.thread_id });
      options.onOpen();
    }
  }));
  const refresh = (invalidate = false) => queue?.refresh(invalidate) ?? Promise.resolve();
  const invalidate = () => {
    revision++;
    discoveryRevision++;
    dirty = true;
    if (!documentVisible() || refreshTimer !== undefined) return;
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined;
      dirty = false;
      void refresh(true);
    }, WORKSPACE_REFRESH_DELAY_MS);
  };

  createEffect(on(options.threadId, (thread) => {
    revision++;
    announcedHandoff = "";
    announcedAsyncHandoffs = new Set();
    dirty = false;
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
    batch(() => {
      setStatus(null);
      setDiscoveredSessions([]);
      setHandoffs([]);
      setRequestedSession(null);
    });
    setError("");
    setConnected(false);
    if (!thread) return;
    const controller = new AbortController();
    const current = createRefreshQueue(async () => {
      const requestRevision = revision;
      const requestDiscoveryRevision = discoveryRevision;
      try {
        const next = await apiFetch<BrowserStatus>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}`, { signal: controller.signal });
        if (!controller.signal.aborted) batch(() => {
          // Viewer updates supersede HTTP status but must not hide newly discovered children.
          if (requestRevision === revision) applyStatus(next, thread);
          if (requestDiscoveryRevision === discoveryRevision) {
            setDiscoveredSessions(next.sessions ?? []);
            setHandoffs(next.pending_handoffs ?? []);
          }
        });
      } catch (cause) {
        if (!controller.signal.aborted && requestRevision === revision) setError(cause instanceof Error ? cause.message : "Could not load browser status.");
      }
    });
    queue = current;
    void current.refresh();
    onCleanup(() => { controller.abort(); current.dispose(); if (queue === current) queue = undefined; });
  }));

  const interval = createMemo(() => !options.threadId() || !documentVisible() || !options.visible() ? 0
    : options.active() && hasOpenSession() && !connected() ? 5000
    : options.active() && connected() ? 0 : WORKSPACE_IDLE_POLL_MS);
  createEffect(() => {
    const delay = interval();
    if (!delay) return;
    let timer: number;
    const poll = async () => { await refresh(); if (!stopped) timer = window.setTimeout(poll, delay); };
    let stopped = false;
    timer = window.setTimeout(poll, delay);
    onCleanup(() => { stopped = true; window.clearTimeout(timer); });
  });
  createEffect(on(() => options.visible() && documentVisible(), (visible, previous) => {
    if (visible && previous === false) invalidate();
  }));
  createEffect(on(options.active, (active) => { if (active) invalidate(); }, { defer: true }));
  createEffect(() => options.onSessionChange?.(hasOpenSession(), options.threadId()));

  onMount(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ thread_id?: string; threadId?: string; browserThreadId?: string }>).detail;
      const thread = options.threadId(), changedThread = detail?.thread_id || detail?.threadId;
      if (!detail || (thread && (changedThread === thread || changedThread?.startsWith(`${thread}:sub:`)))) invalidate();
      if (thread && changedThread === thread && detail?.browserThreadId) {
        const requested = detail.browserThreadId;
        options.onOpen();
        void refresh(true).then(() => {
          if (options.threadId() === thread && (requested === thread || sessions().some(item => item.thread_id === requested))) {
            setRequestedSession({ thread_id: requested });
          }
        });
      }
    };
    const visibilityChanged = () => {
      const visible = document.visibilityState !== "hidden";
      setDocumentVisible(visible);
      if (!visible) { window.clearTimeout(refreshTimer); refreshTimer = undefined; }
      else if (dirty || options.visible()) invalidate();
    };
    const events = [CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED,
      CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, "k41-browser-handoff"];
    for (const event of events) window.addEventListener(event, changed);
    document.addEventListener("visibilitychange", visibilityChanged);
    onCleanup(() => {
      revision++;
      window.clearTimeout(refreshTimer);
      for (const event of events) window.removeEventListener(event, changed);
      document.removeEventListener("visibilitychange", visibilityChanged);
    });
  });
  return { status, sessions, sessionKey, hasOpenSession, handoffs, requestedSession, error, applyStatus, refresh, invalidate, setConnected };
}
