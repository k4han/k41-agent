import { AlertCircle, Check, ChevronRight, CirclePause, Loader2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, on, Show } from "solid-js";
import { TranscriptItemView, type SubagentActivity } from "@/components/Transcript";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { summarizeSubagent } from "@/lib/subagentSummary";
import { useSubagentHistory } from "@/lib/useSubagentHistory";

export function SubagentDetails(props: { activity: SubagentActivity; itemId?: number }) {
  const [opened, setOpened] = createSignal(false);
  const history = useSubagentHistory(() => props.activity);
  const activity = createMemo(() => summarizeSubagent(props.activity, history.items()));
  const summaryLabel = () => history.ready()
    ? activity().summary.fullLabel
    : history.error() ? "Tools unavailable" : "Loading tools...";
  const failed = () => props.activity.status === "failed";
  const paused = () => ["interrupted", "stopped"].includes(props.activity.status);
  createEffect(on(history.identity, () => setOpened(false)));

  return (
    <details class="subagent-details" open={opened()} data-transcript-item-id={props.itemId} onToggle={(event) => {
      const open = event.currentTarget.open;
      setOpened(open);
      if (open) event.currentTarget.dispatchEvent(
        new CustomEvent(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, { bubbles: true }),
      );
    }}>
      <summary class="subagent-summary">
        <ChevronRight size={16} class="subagent-chevron" aria-hidden="true" />
        <span class="subagent-status-icon" classList={{ "is-failed": failed(), "is-completed": props.activity.status === "completed" }}
          title={activity().statusText}>
          <Show when={activity().live} fallback={
            <Show when={failed()} fallback={
              <Show when={paused()} fallback={<Check size={14} />}><CirclePause size={14} /></Show>
            }><AlertCircle size={14} /></Show>
          }><Loader2 size={14} class="spin-icon" /></Show>
        </span>
        <span class="subagent-summary-text">
          <span class="subagent-heading" title={`${props.activity.agent_name} · ${summaryLabel()}`}>
            <strong class="subagent-name">{props.activity.agent_name}</strong>
            <span class="subagent-tool-summary">· {summaryLabel()}</span>
          </span>
          <span class="subagent-current-tool" title={activity().detail}>{activity().detail}</span>
        </span>
      </summary>
      <Show when={opened()}>
        <div class="subagent-transcript">
          <Show when={history.loading()}><p>Loading subagent history...</p></Show>
          <Show when={history.error()}><p role="alert">{history.error()} <button type="button" onClick={() => void history.retry()}>Retry</button></p></Show>
          <Show when={props.activity.error}><p role="alert">{props.activity.error}</p></Show>
          <For each={history.items()}>{(item) => <TranscriptItemView item={item}
            streaming={activity().live} threadId={props.activity.thread_id} />}</For>
        </div>
      </Show>
    </details>
  );
}
