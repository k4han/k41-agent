import type { TranscriptItem } from "@/components/Transcript";
import { createTranscriptTool } from "@/components/Transcript";
import type { AppendScrollMode } from "@/lib/chatTypes";
import { recursionLimitStorageKey, STREAM_ERROR_CODES, STREAM_EVENTS } from "@/lib/eventConstants";

// ── Callback interface for stream event handling ──

export interface StreamCallbacks {
  appendItem: (item: TranscriptItem, mode: AppendScrollMode, threadId?: string) => number;
  updateMessage: (id: number, chunk: string, threadId?: string) => void;
  replaceMessage?: (id: number, text: string, threadId?: string) => void;
  removeItem?: (id: number, threadId?: string) => void;
  updateToolResult: (toolCallId: string, name: string, result: unknown, threadId?: string) => void;
  onError?: (message: string, code?: string) => void;
  setRecursionLimitReached: (value: boolean) => void;
  onThreadCreated: (threadId: string, streamThreadIdRef: StreamThreadIdRef) => void;
}

// ── Mutable refs shared across event handling ──

export type AssistantIdRef = { id: number | null };
export type StreamedRef = { received: boolean };
export type StreamThreadIdRef = { id: string; message: string };

// ── Core event handler ──

export function handleStreamEvent(
  event: Record<string, unknown>,
  assistantIdRef: AssistantIdRef,
  streamedRef: StreamedRef,
  streamThreadIdRef: StreamThreadIdRef,
  callbacks: StreamCallbacks,
): void {
  if (event.type === STREAM_EVENTS.THREAD_CREATED) {
    const threadId = String(event.thread_id || "");
    if (!threadId) {
      return;
    }
    callbacks.onThreadCreated(threadId, streamThreadIdRef);
    return;
  }

  if (event.type === STREAM_EVENTS.MESSAGE) {
    const content = String(event.content || "");
    if (!content) {
      return;
    }
    if (assistantIdRef.id === null) {
      assistantIdRef.id = callbacks.appendItem(
        { type: "message", role: "assistant", text: "" },
        "bottom",
        streamThreadIdRef.id,
      );
    }
    if (!streamedRef.received && callbacks.replaceMessage) {
      callbacks.replaceMessage(assistantIdRef.id, content, streamThreadIdRef.id);
    } else {
      callbacks.updateMessage(assistantIdRef.id, content, streamThreadIdRef.id);
    }
    streamedRef.received = true;
    return;
  }

  if (event.type === STREAM_EVENTS.TOOL_CALL) {
    if (assistantIdRef.id !== null && !streamedRef.received) {
      callbacks.removeItem?.(assistantIdRef.id, streamThreadIdRef.id);
    }
    callbacks.appendItem(
      createTranscriptTool({
        toolCallId: String(event.id || ""),
        name: String(event.name || "unknown"),
        args: event.args ?? null,
      }),
      "bottom",
      streamThreadIdRef.id,
    );
    assistantIdRef.id = null;
    streamedRef.received = false;
    return;
  }

  if (event.type === STREAM_EVENTS.TOOL_RESULT) {
    callbacks.updateToolResult(
      String(event.tool_call_id || ""),
      String(event.name || "unknown"),
      event.content ?? null,
      streamThreadIdRef.id,
    );
    return;
  }

  if (event.type === STREAM_EVENTS.ERROR) {
    const errorMessage = String(event.content || event.message || "Chat failed");
    const errorCode = typeof event.code === "string" ? event.code : undefined;
    if (assistantIdRef.id !== null && !streamedRef.received) {
      callbacks.removeItem?.(assistantIdRef.id, streamThreadIdRef.id);
      assistantIdRef.id = null;
    }
    callbacks.appendItem(
      {
        type: "message",
        role: "error",
        text: errorMessage,
      },
      "bottom",
      streamThreadIdRef.id,
    );
    callbacks.onError?.(errorMessage, errorCode);
    if (event.code === STREAM_ERROR_CODES.RECURSION_LIMIT_REACHED) {
      callbacks.setRecursionLimitReached(true);
      if (streamThreadIdRef.id) {
        window.localStorage.setItem(
          recursionLimitStorageKey(streamThreadIdRef.id),
          "true",
        );
      }
    }
    return;
  }

  if (event.type === STREAM_EVENTS.FINAL) {
    if (streamedRef.received) {
      return;
    }
    const content = String(event.content || "");
    if (!content) {
      return;
    }
    if (assistantIdRef.id === null) {
      assistantIdRef.id = callbacks.appendItem(
        { type: "message", role: "assistant", text: "" },
        "bottom",
        streamThreadIdRef.id,
      );
    }
    if (callbacks.replaceMessage) {
      callbacks.replaceMessage(assistantIdRef.id, content, streamThreadIdRef.id);
    } else {
      callbacks.updateMessage(assistantIdRef.id, content, streamThreadIdRef.id);
    }
    streamedRef.received = true;
  }
}

// ── NDJSON stream reader ──

export async function readNDJSONStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: Record<string, unknown>) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) {
        continue;
      }
      onEvent(JSON.parse(line) as Record<string, unknown>);
    }

    if (done) {
      break;
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as Record<string, unknown>);
  }
}
