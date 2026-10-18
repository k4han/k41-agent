import { batch, createSignal, untrack, type Accessor } from "solid-js";
import { createStore, produce, reconcile, unwrap } from "solid-js/store";
import type { TranscriptItem } from "@/components/Transcript";
import type { ContextBreakdown } from "@/types";
import { cloneValue } from "@/lib/utils";

export type ChatTranscriptItem = TranscriptItem & { id: number; key?: string };

export type TranscriptItemsUpdater =
  | ChatTranscriptItem[]
  | ((previous: ChatTranscriptItem[]) => ChatTranscriptItem[]);

/** Keep row identities stable while retaining the existing accessor/setter API. */
export function createTranscriptItems(initialItems: ChatTranscriptItem[] = []): [
  items: Accessor<ChatTranscriptItem[]>,
  setItems: (update: TranscriptItemsUpdater) => ChatTranscriptItem[],
  appendChunks: (chunks: ReadonlyMap<number, string>) => void,
  appendItem: (item: ChatTranscriptItem) => void,
  replaceMessage: (id: number, text: string) => void,
] {
  const [state, setState] = createStore({ items: cloneValue(unwrap(initialItems)) });
  const indices = new Map(initialItems.map((item, index) => [item.id, index]));
  return [
    () => state.items,
    (update) => {
      const next = untrack(() => typeof update === "function" ? update(unwrap(state.items)) : update);
      indices.clear();
      next.forEach((item, index) => indices.set(item.id, index));
      setState("items", reconcile(next, { key: "id" }));
      return state.items;
    },
    (chunks) => untrack(() => batch(() => {
      for (const [id, chunk] of chunks) {
        const index = indices.get(id);
        if (index === undefined || !chunk) continue;
        setState("items", index, produce((item) => {
          if (item.type === "message") item.text += chunk;
        }));
      }
    })),
    (item) => untrack(() => {
      const index = state.items.length;
      indices.set(item.id, index);
      // Append through the store path without copying or reconciling history.
      setState("items", index, cloneValue(unwrap(item)));
    }),
    (id, text) => untrack(() => {
      const index = indices.get(id);
      if (index === undefined) return;
      setState("items", index, produce((item) => {
        if (item.type === "message") item.text = text;
      }));
    }),
  ];
}

export interface ContextUsage {
  current_context_tokens: number;
  context_window: number;
  has_context_usage?: boolean;
  context_updated_at?: string;
  reserved_tokens?: number;
  provider?: string;
  model?: string;
  estimated?: boolean;
  input_tokens?: number;
  cache_read_tokens?: number;
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
