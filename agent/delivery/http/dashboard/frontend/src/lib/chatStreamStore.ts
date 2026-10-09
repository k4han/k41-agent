import { createSignal, untrack, type Accessor } from "solid-js";
import { createStore, reconcile, unwrap } from "solid-js/store";
import type { TranscriptItem } from "@/components/Transcript";
import type { ContextBreakdown } from "@/types";
import { cloneValue } from "@/lib/utils";

export type ChatTranscriptItem = TranscriptItem & { id: number; key?: string };

export type TranscriptItemsUpdater =
  | ChatTranscriptItem[]
  | ((previous: ChatTranscriptItem[]) => ChatTranscriptItem[]);

/** Keep row identities stable while retaining the existing accessor/setter API. */
export function createTranscriptItems(initialItems: ChatTranscriptItem[] = []): [
  Accessor<ChatTranscriptItem[]>,
  (update: TranscriptItemsUpdater) => ChatTranscriptItem[],
] {
  const [state, setState] = createStore({ items: cloneValue(unwrap(initialItems)) });
  return [
    () => state.items,
    (update) => {
      const next = untrack(() => typeof update === "function" ? update(unwrap(state.items)) : update);
      setState("items", reconcile(next, { key: "id" }));
      return state.items;
    },
  ];
}

export interface ContextUsage {
  current_context_tokens: number;
  context_window: number;
  estimated?: boolean;
  input_tokens?: number;
  output_tokens?: number;
  context_breakdown?: ContextBreakdown;
}

export type PersistedStreamSignals = {
  items: ReturnType<typeof createTranscriptItems>;
  streaming: ReturnType<typeof createSignal<boolean>>;
  controller: ReturnType<typeof createSignal<AbortController | null>>;
  reportedContextUsage: ReturnType<typeof createSignal<ContextUsage | null>>;
};

export const persistedStreams = new Map<string, PersistedStreamSignals>();

let nextItemId = 1;

export function allocItemId(): number {
  const id = nextItemId;
  nextItemId += 1;
  return id;
}

export function getOrCreateStreamSignals(
  threadId: string,
  initialItems: ChatTranscriptItem[] = []
): PersistedStreamSignals {
  let entry = persistedStreams.get(threadId);
  if (!entry) {
    entry = {
      items: createTranscriptItems(initialItems),
      streaming: createSignal<boolean>(false),
      controller: createSignal<AbortController | null>(null),
      reportedContextUsage: createSignal<ContextUsage | null>(null),
    };
    persistedStreams.set(threadId, entry);
  }
  return entry;
}

export function cleanupStreamSignals(threadId: string): void {
  persistedStreams.delete(threadId);
}

export function hasPersistedStream(threadId: string): boolean {
  return persistedStreams.has(threadId);
}

/**
 * Remove persisted stream entries that are no longer actively streaming.
 * Call on mount or before starting a new stream to prevent stale entries
 * from accumulating (e.g. after an unclean teardown).
 */
export function cleanupStaleStreams(): void {
  const toDelete: string[] = [];
  for (const [threadId, entry] of persistedStreams) {
    if (!entry.streaming[0]()) {
      toDelete.push(threadId);
    }
  }
  for (const threadId of toDelete) {
    persistedStreams.delete(threadId);
  }
}
