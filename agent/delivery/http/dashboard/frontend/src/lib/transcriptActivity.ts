import type { TranscriptItem } from "@/components/Transcript";
import { HTML_PREVIEW_TOOL_NAME } from "@/lib/htmlPreview";
import { hasToolCallResult } from "@/lib/toolCallStatus";

/**
 * Transcript rows always carry a numeric id, both for streamed and history
 * items, so the activity view can build stable row keys from them.
 */
export type TranscriptActivitySource = TranscriptItem & { id: number };

export type TranscriptActivityEntry<T extends TranscriptActivitySource> =
  | { kind: "item"; key: string; item: T }
  | { kind: "activity"; key: string; items: T[] };

export type TranscriptActivityView<T extends TranscriptActivitySource> = {
  /** Ordered row keys. A key stays stable while its row keeps its identity. */
  keys: string[];
  entries: Map<string, TranscriptActivityEntry<T>>;
  /** Key of the group that may receive more calls, even after results arrive. */
  liveKey: string | null;
};

/** Ignore assistant placeholders with no visible text or rich content. */
export function isEmptyAssistantMessage(item: TranscriptItem): boolean {
  return item.type === "message"
    && item.role === "assistant"
    && !item.text.trim()
    && !item.attachments?.length
    && !item.generatedImagePending;
}

/** Completed tools leave the agent waiting for its next visible response. */
export function isTranscriptWaitingForModel(
  items: readonly TranscriptItem[],
  streaming: boolean,
): boolean {
  if (!streaming) return false;
  let hasTools = false;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (isEmptyAssistantMessage(item)) continue;
    if (item.type !== "tool") break;
    hasTools = true;
    if (!hasToolCallResult(item)) return false;
  }
  return hasTools;
}

/**
 * Items that must never be folded into an activity group and that also break
 * the surrounding group in two.
 *
 * Visible messages end a tool run and remain in their original position.
 * Interactive requests and rich results also remain separate rows.
 */
export function isTranscriptActivityBoundary(item: TranscriptItem): boolean {
  if (item.type === "plan_review" || item.type === "user_input_request") {
    return true;
  }
  if (item.type === "tool") {
    return item.name === HTML_PREVIEW_TOOL_NAME;
  }
  return item.type === "message" && !isEmptyAssistantMessage(item);
}

/**
 * Id of the assistant message that holds the final answer of the thread.
 *
 * The answer actions (copy, regenerate) belong only to this message. As this
 * helper ignores streaming, callers hide the actions separately while a run is
 * still in progress.
 */
export function findTranscriptAnswerId<T extends TranscriptActivitySource>(
  items: readonly T[],
): number | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item && item.type === "message" && item.role === "assistant"
      && !isEmptyAssistantMessage(item)) {
      return item.id;
    }
  }
  return null;
}

/**
 * Split a flat transcript into visible rows and collapsible tool groups.
 *
 * Consecutive tools form their own group without consuming surrounding text.
 * Empty assistant placeholders are skipped. Only a trailing group can still
 * receive tools from the running turn.
 */
export function buildTranscriptActivityView<T extends TranscriptActivitySource>(
  items: readonly T[],
  streaming: boolean,
): TranscriptActivityView<T> {
  const keys: string[] = [];
  const entries = new Map<string, TranscriptActivityEntry<T>>();
  let buffer: T[] = [];

  const pushEntry = (entry: TranscriptActivityEntry<T>) => {
    keys.push(entry.key);
    entries.set(entry.key, entry);
  };

  const flushActivity = () => {
    if (buffer.length === 0) {
      return;
    }
    const entry: TranscriptActivityEntry<T> = {
      kind: "activity",
      key: `activity-${buffer[0].id}`,
      items: buffer,
    };
    buffer = [];
    pushEntry(entry);
  };

  items.forEach((item) => {
    if (isEmptyAssistantMessage(item)) {
      return;
    }
    if (isTranscriptActivityBoundary(item)) {
      flushActivity();
      pushEntry({ kind: "item", key: `item-${item.id}`, item });
      return;
    }
    buffer.push(item);
  });
  flushActivity();

  let liveKey: string | null = null;
  if (streaming && keys.length > 0) {
    // Keep the trailing group available for follow-up calls between results.
    const lastEntry = entries.get(keys[keys.length - 1]);
    if (lastEntry?.kind === "activity") {
      liveKey = lastEntry.key;
    }
  }
  return { keys, entries, liveKey };
}
