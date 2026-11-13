import { createEffect, createMemo, createSignal, on, onCleanup, type Accessor } from "solid-js";
import type { SubagentActivity, TranscriptItem } from "@/components/Transcript";
import { apiFetch } from "@/lib/api";
import { toThreadTranscript, type ThreadMessage } from "@/lib/chatThreads";

export function useSubagentHistory(activity: Accessor<SubagentActivity>) {
  const identity = createMemo(() => JSON.stringify([
    activity().parent_thread_id, activity().delegation_id, activity().thread_id,
  ]));
  const hasLiveItems = createMemo(() => activity().items !== undefined);
  const [history, setHistory] = createSignal<{ key: string; items: TranscriptItem[] } | null>(null);
  const [loadingKey, setLoadingKey] = createSignal<string | null>(null);
  const [error, setError] = createSignal("");
  let controller: AbortController | undefined;
  const savedItems = () => history()?.key === identity() ? history()!.items : undefined;
  const items = () => activity().items ?? savedItems() ?? [];
  const ready = () => hasLiveItems() || savedItems() !== undefined;

  const load = async () => {
    const snapshot = activity();
    const key = identity();
    if (hasLiveItems() || savedItems() !== undefined || loadingKey() === key) return;
    controller?.abort();
    const request = new AbortController();
    controller = request;
    setLoadingKey(key);
    setError("");
    try {
      const result = await apiFetch<{ messages: ThreadMessage[] }>(
        `/v2/api/chat/${encodeURIComponent(snapshot.parent_thread_id)}/subagents/${encodeURIComponent(snapshot.delegation_id)}`,
        { signal: request.signal },
      );
      if (!request.signal.aborted && identity() === key && !hasLiveItems()) {
        setHistory({ key, items: toThreadTranscript(result.messages, snapshot.thread_id) });
      }
    } catch (failure) {
      if (!request.signal.aborted && identity() === key) {
        setError(failure instanceof Error ? failure.message : "Subagent history could not be loaded.");
      }
    } finally {
      if (controller === request) {
        controller = undefined;
        setLoadingKey(null);
      }
    }
  };

  createEffect(on([identity, hasLiveItems], () => {
    controller?.abort();
    controller = undefined;
    setLoadingKey(null);
    setError("");
    if (!hasLiveItems()) void load();
  }));
  onCleanup(() => {
    controller?.abort();
    controller = undefined;
  });

  return { identity, items, ready, error, loading: () => loadingKey() === identity(), retry: load };
}
