import { createSignal, onCleanup, onMount } from "solid-js";

import { SSE_URLS } from "@/lib/endpoints";
import { SESSION_EVENTS } from "@/lib/eventConstants";
import { SSE_RECONNECT_DELAY_MS } from "@/lib/uiConstants";
import type { ActiveSession } from "@/types";

export function useHomeSessions(initial: ActiveSession[]) {
  const [sessions, setSessions] = createSignal<ActiveSession[]>(initial);
  let disposed = false;
  let source: EventSource | null = null;
  let reconnectTimer: number | undefined;

  const close = () => {
    if (source) {
      source.close();
      source = null;
    }
  };

  const connect = () => {
    if (disposed) return;
    close();
    source = new EventSource(SSE_URLS.sessions);

    source.addEventListener(SESSION_EVENTS.SNAPSHOT, (event) => {
      try {
        const payload = JSON.parse(event.data) as { sessions?: ActiveSession[] };
        if (Array.isArray(payload.sessions)) {
          setSessions(payload.sessions);
        }
      } catch {
        // ignore parse errors silently
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_STARTED, (event) => {
      try {
        const session = JSON.parse(event.data) as ActiveSession;
        setSessions((prev) => {
          const exists = prev.some((s) => s.session_id === session.session_id);
          if (exists) {
            return prev.map((s) => (s.session_id === session.session_id ? session : s));
          }
          return [...prev, session];
        });
      } catch {
        // ignore
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_STOPPED, (event) => {
      try {
        const data = JSON.parse(event.data) as { session_id: string };
        setSessions((prev) => prev.filter((s) => s.session_id !== data.session_id));
      } catch {
        // ignore
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_UPDATED, (event) => {
      try {
        const session = JSON.parse(event.data) as ActiveSession;
        setSessions((prev) =>
          prev.map((s) => (s.session_id === session.session_id ? session : s)),
        );
      } catch {
        // ignore
      }
    });

    source.onerror = () => {
      if (disposed) return;
      if (source && source.readyState === EventSource.CLOSED) {
        close();
        reconnectTimer = window.setTimeout(connect, SSE_RECONNECT_DELAY_MS);
      }
    };
  };

  onMount(() => {
    connect();
  });
  onCleanup(() => {
    disposed = true;
    if (reconnectTimer !== undefined) {
      window.clearTimeout(reconnectTimer);
    }
    close();
  });

  return { sessions };
}
