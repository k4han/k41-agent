import { createSignal, onCleanup } from "solid-js";

import { API_PATHS } from "@/lib/endpoints";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import {
  handleStreamEvent,
  type StreamCallbacks,
} from "@/lib/chatStreamHandler";
import type { BackgroundTaskSnapshot } from "@/lib/chatTypes";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { ActiveSession, BackgroundTask } from "@/types";

export interface UseBackgroundStreamParams {
  applyThreadPayload: (payload: ThreadMessagesPayload) => void;
  streamCallbacks: StreamCallbacks;
}

export function useBackgroundStream(params: UseBackgroundStreamParams) {
  const { applyThreadPayload, streamCallbacks } = params;

  const [backgroundTask, setBackgroundTask] = createSignal<BackgroundTask | null>(null);
  const [backgroundLive, setBackgroundLive] = createSignal(false);
  const [backgroundStreamError, setBackgroundStreamError] = createSignal("");
  const [backgroundSession, setBackgroundSession] = createSignal<ActiveSession | null>(null);

  let backgroundEventSource: EventSource | null = null;
  let backgroundEventThreadId = "";

  const closeBackgroundStream = () => {
    if (backgroundEventSource) {
      backgroundEventSource.close();
    }
    backgroundEventSource = null;
    backgroundEventThreadId = "";
    setBackgroundLive(false);
    setBackgroundStreamError("");
    setBackgroundSession(null);
  };

  const applyBackgroundSnapshot = (snapshot: BackgroundTaskSnapshot) => {
    applyThreadPayload({
      thread_id: snapshot.thread_id,
      messages: snapshot.messages || [],
      platform: snapshot.platform,
      user_id: snapshot.user_id,
      channel_id: snapshot.channel_id,
      agent_name: snapshot.agent_name,
      title: snapshot.title,
      kind: snapshot.kind,
      workspace: snapshot.workspace,
    });
    setBackgroundTask(snapshot.task || null);
    setBackgroundSession(snapshot.active_session || null);
  };

  const openBackgroundStream = (threadId: string) => {
    closeBackgroundStream();
    const assistantIdRef = { id: null as number | null };
    const streamedRef = { received: false };
    const source = new EventSource(API_PATHS.backgroundTaskStream(threadId));
    backgroundEventSource = source;
    backgroundEventThreadId = threadId;
    setBackgroundLive(true);

    source.addEventListener("snapshot", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      assistantIdRef.id = null;
      streamedRef.received = false;
      setBackgroundStreamError("");
      applyBackgroundSnapshot(JSON.parse(event.data) as BackgroundTaskSnapshot);
    });
    source.addEventListener("agent", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      handleStreamEvent(
        JSON.parse(event.data) as Record<string, unknown>,
        assistantIdRef,
        streamedRef,
        { id: threadId, message: "" },
        streamCallbacks,
      );
    });
    source.addEventListener("task", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      const payload = JSON.parse(event.data) as { task?: BackgroundTask | null };
      setBackgroundTask(payload.task || null);
      setBackgroundStreamError("");
    });
    source.addEventListener("done", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      const payload = JSON.parse(event.data) as { task?: BackgroundTask | null };
      setBackgroundTask(payload.task || null);
      setBackgroundLive(false);
      source.close();
      if (backgroundEventSource === source) {
        backgroundEventSource = null;
        backgroundEventThreadId = "";
      }
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.TASKS_CHANGED));
    });
    source.addEventListener("heartbeat", () => {
      if (backgroundEventThreadId === threadId) {
        setBackgroundStreamError("");
      }
    });
    source.onerror = () => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      setBackgroundStreamError("Live updates disconnected.");
      if (source.readyState === EventSource.CLOSED) {
        setBackgroundLive(false);
      }
    };
  };

  onCleanup(() => {
    closeBackgroundStream();
  });

  return {
    backgroundTask,
    setBackgroundTask,
    backgroundLive,
    backgroundStreamError,
    backgroundSession,
    openBackgroundStream,
    closeBackgroundStream,
  };
}
