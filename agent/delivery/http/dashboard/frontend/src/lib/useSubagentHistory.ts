import { batch, createEffect, createMemo, createSignal, on, onCleanup, type Accessor } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import type { SubagentActivity, TranscriptItem } from "@/components/Transcript";
import { apiFetch } from "@/lib/api";
import { toThreadTranscript, type ThreadMessage } from "@/lib/chatThreads";

export function useSubagentHistory(activity: Accessor<SubagentActivity>) {
  const identity = createMemo(() => JSON.stringify([
    activity().parent_thread_id, activity().delegation_id, activity().thread_id,
  ]));
  const revision = createMemo(() => JSON.stringify([
    identity(), activity().run_id, activity().version, activity().checkpoint_id, activity().status,
  ]));
  const hasLiveItems = createMemo(() => activity().items !== undefined);
  const [history, setHistory] = createStore<{ key: string; revision: string; items: TranscriptItem[] }>({ key: "", revision: "", items: [] });
  const [loadingKey, setLoadingKey] = createSignal<string | null>(null);
  const [error, setError] = createSignal("");
  let controller: AbortController | undefined;
  const savedItems = () => history.key === identity() ? history.items : undefined;
  const items = () => activity().items ?? savedItems() ?? [];
  const ready = () => hasLiveItems() || savedItems() !== undefined;

  const load = async () => {
    const snapshot = activity();
    const key = identity(), version = revision();
    if (hasLiveItems() || history.revision === version || loadingKey() === version) return;
    controller?.abort();
    const request = new AbortController();
    controller = request;
    setLoadingKey(version);
    setError("");
    try {
      const result = await apiFetch<{ messages: ThreadMessage[] }>(
        `/v2/api/chat/${encodeURIComponent(snapshot.parent_thread_id)}/subagents/${encodeURIComponent(snapshot.delegation_id)}`,
        { signal: request.signal },
      );
      if (!request.signal.aborted && revision() === version && !hasLiveItems()) {
        batch(() => {
          setHistory("items", reconcile(toThreadTranscript(result.messages, snapshot.thread_id), { key: "key" }));
          setHistory({ key, revision: version });
        });
      }
    } catch (failure) {
      if (!request.signal.aborted && revision() === version) {
        setError(failure instanceof Error ? failure.message : "Subagent history could not be loaded.");
      }
    } finally {
      if (controller === request) {
        controller = undefined;
        setLoadingKey(null);
      }
    }
  };

  createEffect(on([revision, hasLiveItems], () => {
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

  return { identity, items, ready, error, loading: () => loadingKey() === revision(), retry: load };
}
