import { createEffect, createSignal, on, onCleanup, onMount } from "solid-js";
import { computerApi, type ComputerState } from "@/lib/computers";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { createRefreshQueue, WORKSPACE_IDLE_POLL_MS, WORKSPACE_REFRESH_DELAY_MS } from "@/lib/workspaceRefresh";

export function useComputerSession(backend: () => string, locator: () => string, visible: () => boolean) {
  const [state, setState] = createSignal<ComputerState | null>(null);
  let queue: ReturnType<typeof createRefreshQueue> | undefined;
  let timer: number | undefined;
  let revision = 0;
  const refresh = (invalidate = false) => queue?.refresh(invalidate) ?? Promise.resolve();
  const invalidate = () => {
    revision++;
    if (!visible() || timer !== undefined) return;
    timer = window.setTimeout(() => { timer = undefined; void refresh(true); }, WORKSPACE_REFRESH_DELAY_MS);
  };
  createEffect(on(() => [backend(), locator()] as const, ([currentBackend, currentLocator]) => {
    revision++;
    window.clearTimeout(timer);
    timer = undefined;
    setState(null);
    if (!currentBackend || !currentLocator) return;
    const controller = new AbortController();
    const current = createRefreshQueue(async () => {
      const requestRevision = revision;
      try {
        const next = await computerApi.status(currentBackend, currentLocator, controller.signal);
        if (!controller.signal.aborted && requestRevision === revision) setState(next);
      } catch { /* Service discovery keeps the last known state. */ }
    });
    queue = current;
    if (visible()) void refresh();
    onCleanup(() => { controller.abort(); current.dispose(); if (queue === current) queue = undefined; });
  }));
  createEffect(on(() => visible() && Boolean(backend() && locator()), (enabled) => {
    if (!enabled) return;
    void refresh();
    let stopped = false;
    let poll: number;
    const tick = async () => { await refresh(); if (!stopped) poll = window.setTimeout(tick, WORKSPACE_IDLE_POLL_MS); };
    poll = window.setTimeout(tick, WORKSPACE_IDLE_POLL_MS);
    onCleanup(() => { stopped = true; window.clearTimeout(poll); window.clearTimeout(timer); timer = undefined; });
  }));
  onMount(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ backend?: string; locator?: string; reset?: boolean }>).detail;
      if (!detail || detail.backend === backend() && detail.locator === locator()) {
        if (detail?.reset) setState(previous => previous ? { ...previous, running: false } : previous);
        invalidate();
      }
    };
    window.addEventListener(CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, changed);
    window.addEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, changed);
    onCleanup(() => {
      window.clearTimeout(timer);
      window.removeEventListener(CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, changed);
      window.removeEventListener(CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED, changed);
    });
  });
  return { state, refresh, invalidate };
}
