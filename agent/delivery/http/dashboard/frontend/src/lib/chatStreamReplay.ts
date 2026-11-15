import type { TranscriptItem, TranscriptTool } from "@/components/Transcript";
import { mergeSubagentTranscript } from "@/lib/asyncSubagents";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import type { StreamCallbacks } from "@/lib/chatStreamHandler";

function transcriptIdentity(item: TranscriptItem & { key?: string }): string | undefined {
  if (item.type === "async_wakeup") return `async_wakeup:${item.wakeup_id}`;
  if ((item.type === "message" || item.type === "thinking") && item.messageId) {
    return `${item.type}:${item.messageId.replace(/^lc_run--/, "")}`;
  }
  if ("tool_call_id" in item && item.tool_call_id) return `${item.type}:${item.tool_call_id}`;
  return item.key;
}

/** Keep existing rows mounted when a stream is replaced by its checkpoint. */
export function preserveTranscriptIds(
  snapshot: TranscriptItem[], previous: readonly ChatTranscriptItem[], allocate: () => number,
): ChatTranscriptItem[] {
  const identities = new Map(previous.flatMap((item) => {
    const key = transcriptIdentity(item);
    return key ? [[key, item.id] as const] : [];
  }));
  return snapshot.map((item) => ({ ...item, id: identities.get(transcriptIdentity(item) || "") ?? allocate() }));
}

/** Replay a run onto the current transcript without removing earlier runs. */
export function createReplayCallbacks(callbacks: StreamCallbacks, previous: readonly ChatTranscriptItem[]): StreamCallbacks {
  const identities = new Map(previous.flatMap((item) => {
    const key = transcriptIdentity(item);
    return key ? [[key, item] as const] : [];
  }));
  const reused = new Map<number, ChatTranscriptItem>();
  const text = new Map<number, string>();
  const tools = new Map(previous.flatMap((item) => item.type === "tool" && item.tool_call_id
    ? [[item.tool_call_id, item] as const] : []));

  const writeText = (id: number, value: string, threadId?: string) => {
    text.set(id, value);
    const saved = reused.get(id);
    if (!saved || (saved.type !== "message" && saved.type !== "thinking")) return;
    // A replay may initially contain only a prefix of an already saved answer.
    if (saved.text.startsWith(value)) return;
    if (callbacks.replaceMessage) callbacks.replaceMessage(id, value, threadId);
    else if (value.startsWith(saved.text)) callbacks.updateMessage(id, value.slice(saved.text.length), threadId);
  };

  return {
    ...callbacks,
    appendItem: (item, mode, threadId) => {
      const key = transcriptIdentity(item);
      const existing = key ? identities.get(key) : undefined;
      if (!existing) return callbacks.appendItem(item, mode, threadId);
      reused.set(existing.id, existing);
      text.set(existing.id, "");
      return existing.id;
    },
    updateMessage: (id, chunk, threadId) => {
      if (reused.has(id)) writeText(id, (text.get(id) || "") + chunk, threadId);
      else callbacks.updateMessage(id, chunk, threadId);
    },
    replaceMessage: (id, value, threadId) => {
      if (reused.has(id)) writeText(id, value, threadId);
      else if (callbacks.replaceMessage) callbacks.replaceMessage(id, value, threadId);
      else callbacks.updateMessage(id, value, threadId);
    },
    finishThinking: (id, durationMs, threadId) => {
      const saved = reused.get(id);
      callbacks.finishThinking?.(id, saved?.type === "thinking" ? saved.durationMs ?? durationMs : durationMs, threadId);
    },
    removeItem: (id, threadId) => {
      if (!reused.has(id)) callbacks.removeItem?.(id, threadId);
    },
    updateToolActivity: (id, patch, threadId) => {
      const saved = tools.get(id);
      const change: Partial<TranscriptTool> = { ...patch };
      if (saved?.resultReceived && change.executionStatus === "running") delete change.executionStatus;
      if (saved?.subagent && change.subagent) {
        const finished = !["started", "pending", "running"].includes(saved.subagent.status);
        change.subagent = {
          ...change.subagent,
          status: finished && ["started", "running"].includes(change.subagent.status)
            ? saved.subagent.status : change.subagent.status,
          ...(saved.subagent.items || change.subagent.items ? {
            items: mergeSubagentTranscript(saved.subagent.items || [], change.subagent.items || []),
          } : {}),
        };
      }
      callbacks.updateToolActivity?.(id, change, threadId);
    },
  };
}
