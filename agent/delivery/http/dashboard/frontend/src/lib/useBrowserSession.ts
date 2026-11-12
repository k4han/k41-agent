import { createEffect, createMemo, createSignal, on, onCleanup, onMount, untrack } from "solid-js";

import { apiFetch } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { createRefreshQueue, WORKSPACE_IDLE_POLL_MS, WORKSPACE_REFRESH_DELAY_MS } from "@/lib/workspaceRefresh";

export type BrowserStatus = {
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
  const [error, setError] = createSignal("");
  const [connected, setConnected] = createSignal(false);
  const [documentVisible, setDocumentVisible] = createSignal(document.visibilityState !== "hidden");
  let revision = 0;
  let announcedHandoff = "";
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
      options.onOpen();
    }
  };
  const refresh = (invalidate = false) => queue?.refresh(invalidate) ?? Promise.resolve();
  const invalidate = () => {
    revision++;
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
    dirty = false;
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
    setStatus(null);
    setError("");
    setConnected(false);
    if (!thread) return;
    const controller = new AbortController();
    const current = createRefreshQueue(async () => {
      const requestRevision = revision;
      try {
        const next = await apiFetch<BrowserStatus>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}`, { signal: controller.signal });
        if (!controller.signal.aborted && requestRevision === revision) applyStatus(next, thread);
      } catch (cause) {
        if (!controller.signal.aborted && requestRevision === revision) setError(cause instanceof Error ? cause.message : "Could not load browser status.");
      }
    });
    queue = current;
    void current.refresh();
    onCleanup(() => { controller.abort(); current.dispose(); if (queue === current) queue = undefined; });
  }));

  const interval = createMemo(() => !options.threadId() || !documentVisible() || !options.visible() ? 0
    : options.active() && status()?.open && !connected() ? 5000
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
  createEffect(() => options.onSessionChange?.(Boolean(status()?.open), options.threadId()));

  onMount(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ thread_id?: string; threadId?: string }>).detail;
      if (!detail || (detail.thread_id || detail.threadId) === options.threadId()) invalidate();
    };
    const visibilityChanged = () => {
      const visible = document.visibilityState !== "hidden";
      setDocumentVisible(visible);
      if (!visible) { window.clearTimeout(refreshTimer); refreshTimer = undefined; }
      else if (dirty || options.visible()) invalidate();
    };
    const events = [CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED,
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
  return { status, error, applyStatus, refresh, invalidate, setConnected };
}
