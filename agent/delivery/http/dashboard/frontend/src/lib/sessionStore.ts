import { createSignal, onCleanup, onMount } from "solid-js";
import { createStore, reconcile } from "solid-js/store";

import { SSE_URLS } from "@/lib/endpoints";
import { CUSTOM_DOM_EVENTS, SESSION_EVENTS } from "@/lib/eventConstants";
import { SSE_RECONNECT_DELAY_MS } from "@/lib/uiConstants";
import type { ActiveSession } from "@/types";

const [state, setState] = createStore<{ sessions: ActiveSession[] }>({ sessions: [] });
const [revision, setRevision] = createSignal(0);
let consumers = 0;
let source: EventSource | null = null;
let reconnectTimer: number | undefined;
let receivedLiveData = false;

function notify(name: string, detail?: unknown) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

function setSessions(sessions: ActiveSession[]) {
  setState("sessions", reconcile(sessions, { key: "session_id" }));
}

function close() {
  source?.close();
  source = null;
  if (reconnectTimer !== undefined) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = undefined;
  }
}

function connect() {
  if (consumers === 0 || typeof EventSource === "undefined") return;
  close();
  const connection = new EventSource(SSE_URLS.sessions);
  source = connection;

  connection.addEventListener(SESSION_EVENTS.SNAPSHOT, (event) => {
    if (source !== connection) return;
    try {
      const payload = JSON.parse(event.data) as { sessions?: ActiveSession[] };
      if (Array.isArray(payload.sessions)) {
        receivedLiveData = true;
        setSessions(payload.sessions);
      }
    } catch (error) {
      console.error("Failed to parse sessions snapshot", error);
    }
  });

  const upsert = (event: MessageEvent, eventName: string) => {
    if (source !== connection) return;
    try {
      const session = JSON.parse(event.data) as ActiveSession;
      receivedLiveData = true;
      const exists = state.sessions.some((item) => item.session_id === session.session_id);
      setSessions(exists
        ? state.sessions.map((item) => item.session_id === session.session_id ? session : item)
        : [...state.sessions, session]);
      notify(eventName, session);
      notify(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
        threadId: session.thread_id,
        agent_name: session.agent_name,
      });
      if (eventName === CUSTOM_DOM_EVENTS.SESSION_STARTED) notify(CUSTOM_DOM_EVENTS.THREADS_CHANGED);
    } catch (error) {
      console.error("Failed to parse session event", error);
    }
  };

  connection.addEventListener(SESSION_EVENTS.SESSION_STARTED, (event) => upsert(event, CUSTOM_DOM_EVENTS.SESSION_STARTED));
  connection.addEventListener(SESSION_EVENTS.SESSION_UPDATED, (event) => upsert(event, CUSTOM_DOM_EVENTS.SESSION_UPDATED));
  connection.addEventListener(SESSION_EVENTS.SESSION_STOPPED, (event) => {
    if (source !== connection) return;
    try {
      const stopped = JSON.parse(event.data) as { session_id: string; thread_id: string };
      receivedLiveData = true;
      setSessions(state.sessions.filter((item) => item.session_id !== stopped.session_id));
      notify(CUSTOM_DOM_EVENTS.SESSION_STOPPED, stopped);
      notify(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, { threadId: stopped.thread_id });
      notify(CUSTOM_DOM_EVENTS.THREADS_CHANGED);
    } catch (error) {
      console.error("Failed to parse stopped session", error);
    }
  });
  connection.onerror = () => {
    if (source !== connection || connection.readyState !== EventSource.CLOSED) return;
    close();
    if (consumers > 0) reconnectTimer = window.setTimeout(connect, SSE_RECONNECT_DELAY_MS);
  };
}

/** One connection shared by all mounted consumers; the final cleanup closes it. */
export function useSessions(initial: ActiveSession[] = []) {
  if (!receivedLiveData && state.sessions.length === 0 && initial.length > 0) setSessions(initial);
  let subscribed = false;
  onMount(() => {
    subscribed = true;
    consumers += 1;
    if (consumers === 1) connect();
  });
  onCleanup(() => {
    if (!subscribed) return;
    consumers -= 1;
    if (consumers === 0) {
      close();
      receivedLiveData = false;
      setSessions([]);
    }
  });
  return {
    sessions: () => {
      revision();
      return state.sessions;
    },
    invalidate: () => setRevision((value) => value + 1),
  };
}
