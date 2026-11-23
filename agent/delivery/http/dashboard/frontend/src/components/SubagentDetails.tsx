import { CircleAlert, Check, ChevronRight, CirclePause, Loader2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, on, Show } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import { useSearchParams } from "@solidjs/router";
import { TranscriptItemView, type SubagentActivity } from "@/components/Transcript";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { summarizeSubagent } from "@/lib/subagentSummary";
import { useSubagentHistory } from "@/lib/useSubagentHistory";
import { useAsyncSubagent } from "@/lib/useAsyncSubagent";
import { AsyncTaskControls } from "@/components/AsyncTaskControls";
import { mergeSubagentTranscript, type SubagentTranscriptItem } from "@/lib/asyncSubagents";

export function SubagentDetails(props: { activity: SubagentActivity; itemId?: number }) {
  const [opened, setOpened] = createSignal(false);
  const [query] = useSearchParams();
  const background = useAsyncSubagent(() => props.activity, opened);
  const history = useSubagentHistory(background.activity);
  const [transcript, setTranscript] = createStore<{ items: SubagentTranscriptItem[] }>({ items: [] });
  createEffect(() => {
    setTranscript("items", reconcile(mergeSubagentTranscript(history.items(), background.liveItems()), { key: "key" }));
  });
  const items = () => transcript.items;
  const activity = createMemo(() => summarizeSubagent(background.activity(), items()));
  const summaryLabel = () => history.ready() || background.liveItems().length
    ? activity().summary.fullLabel
    : history.error() ? "Tools unavailable" : "Loading tools...";
  const failed = () => ["failed", "timed_out"].includes(background.activity().status);
  const paused = () => ["interrupted", "stopped", "waiting", "cancelled"].includes(background.activity().status);
  createEffect(on([() => props.activity.delegation_id, () => query.task_id], ([delegation, target]) =>
    setOpened(Boolean(target && target === (props.activity.task_id || delegation)))));

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
        <span class="subagent-status-icon" classList={{ "is-failed": failed(), "is-completed": background.activity().status === "completed" }}
          title={activity().statusText}>
          <Show when={activity().live} fallback={
            <Show when={failed()} fallback={
              <Show when={paused()} fallback={<Check size={14} />}><CirclePause size={14} /></Show>
            }><CircleAlert size={14} /></Show>
          }><Loader2 size={14} class="spin-icon" /></Show>
        </span>
        <span class="subagent-summary-text">
          <span class="subagent-heading" title={`${background.activity().agent_name} · ${summaryLabel()}`}>
            <strong class="subagent-name">{background.activity().agent_name}</strong>
            <span class="subagent-tool-summary">· {summaryLabel()}</span>
          </span>
          <span class="subagent-current-tool" title={activity().detail}>{activity().detail}</span>
        </span>
      </summary>
      <Show when={opened()}>
        <div class="subagent-transcript">
          <Show when={history.loading() && !history.ready() && !background.liveItems().length}><p>Loading subagent history...</p></Show>
          <Show when={history.error()}><p role="alert">{history.error()} <button type="button" onClick={() => void history.retry()}>Retry</button></p></Show>
          <Show when={background.activity().error}><p role="alert">{background.activity().error}</p></Show>
          <For each={items()}>{(item) => <TranscriptItemView item={item}
            streaming={activity().live} threadId={props.activity.thread_id} />}</For>
          <Show when={background.enabled()}><AsyncTaskControls task={background.task()} busy={background.busy()}
            error={background.error()} perform={background.perform} /></Show>
        </div>
      </Show>
    </details>
  );
}
