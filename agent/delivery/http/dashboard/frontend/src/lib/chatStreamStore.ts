import { createSignal } from "solid-js";
import type { TranscriptItem } from "@/components/Transcript";

export type ChatTranscriptItem = TranscriptItem & { id: number; key?: string };

export type PersistedStreamSignals = {
  items: ReturnType<typeof createSignal<ChatTranscriptItem[]>>;
  streaming: ReturnType<typeof createSignal<boolean>>;
  controller: ReturnType<typeof createSignal<AbortController | null>>;
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
      items: createSignal<ChatTranscriptItem[]>(initialItems),
      streaming: createSignal<boolean>(false),
      controller: createSignal<AbortController | null>(null),
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
