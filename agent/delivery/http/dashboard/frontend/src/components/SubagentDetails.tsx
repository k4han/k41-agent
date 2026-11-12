import { createSignal, For, Show } from "solid-js";
import { TranscriptItemView, type SubagentActivity, type TranscriptItem } from "@/components/Transcript";
import { apiFetch } from "@/lib/api";
import { toThreadTranscript, type ThreadMessage } from "@/lib/chatThreads";

export function SubagentDetails(props: { activity: SubagentActivity }) {
  const [opened, setOpened] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [history, setHistory] = createSignal<TranscriptItem[] | null>(null);
  const items = () => props.activity.items?.length ? props.activity.items : history() || [];
  const load = async () => {
    if (loading() || history() || props.activity.items?.length) return;
    setLoading(true);
    setError("");
    try {
      const { parent_thread_id, delegation_id } = props.activity;
      const result = await apiFetch<{ messages: ThreadMessage[] }>(
        `/v2/api/chat/${encodeURIComponent(parent_thread_id)}/subagents/${encodeURIComponent(delegation_id)}`,
      );
      setHistory(toThreadTranscript(result.messages, props.activity.thread_id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Subagent history could not be loaded.");
    } finally {
      setLoading(false);
    }
  };
  return (
    <details class="subagent-details" onToggle={(event) => {
      const open = event.currentTarget.open;
      setOpened(open);
      if (open) void load();
    }}>
      <summary><strong>{props.activity.agent_name}</strong> <span>{props.activity.status}</span></summary>
      <Show when={opened()}>
        <div class="subagent-transcript">
          <Show when={loading()}><p>Loading subagent history...</p></Show>
          <Show when={error()}><p role="alert">{error()} <button type="button" onClick={() => void load()}>Retry</button></p></Show>
          <Show when={props.activity.error}><p role="alert">{props.activity.error}</p></Show>
          <For each={items()}>{(item) => <TranscriptItemView item={item}
            streaming={["started", "running"].includes(props.activity.status)} threadId={props.activity.thread_id} />}</For>
        </div>
      </Show>
    </details>
  );
}
