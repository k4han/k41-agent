import { A } from "@solidjs/router";
import { For, Show } from "solid-js";
import { ExternalLink, Square } from "lucide-solid";

import { chatThreadHref } from "@/lib/chatThreads";
import type { ActiveSession, SchedulerRun } from "@/types";

export function RunningPill(props: { label?: string }) {
  return (
    <span class="badge badge-info scheduler-running-pill">
      <span class="live-dot" aria-hidden="true" />
      {props.label ?? "running"}
    </span>
  );
}

/** Whole seconds read better than milliseconds for a ticking live counter. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

export function RunningNowPanel(props: {
  sessions: ActiveSession[];
  runsByThread: ReadonlyMap<string, SchedulerRun>;
  now: number;
  stopping: ReadonlySet<string>;
  onStop: (session: ActiveSession) => void;
}) {
  return (
    <section class="panel scheduler-live-panel" aria-live="polite">
      <div class="panel-header">
        <div class="panel-title scheduler-live-title">
          <span class="live-dot" aria-hidden="true" />
          Running now
        </div>
        <span class="hint">
          {props.sessions.length} task{props.sessions.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul class="scheduler-live-list">
        <For each={props.sessions}>
          {(session) => {
            const run = () => props.runsByThread.get(session.thread_id);
            const stopping = () => props.stopping.has(session.thread_id);
            return (
              <li class="scheduler-live-item">
                <div class="scheduler-live-main">
                  <div class="scheduler-live-task" title={run()?.task}>
                    {run()?.task || "Scheduled task"}
                  </div>
                  <div class="scheduler-live-meta">
                    <span class="mono scheduler-live-elapsed">
                      {formatElapsed(props.now - session.started_at * 1000)}
                    </span>
                    <Show when={session.current_step}>
                      <span aria-hidden="true">·</span>
                      <span class="scheduler-live-step">{session.current_step}</span>
                    </Show>
                    <Show when={session.tools_called.length > 0}>
                      <span aria-hidden="true">·</span>
                      <span>
                        {session.tools_called.length} tool call{session.tools_called.length === 1 ? "" : "s"}
                      </span>
                    </Show>
                    <Show when={run()?.trigger_source === "manual"}>
                      <span class="chip">manual</span>
                    </Show>
                  </div>
                </div>
                <div class="scheduler-live-actions">
                  <A class="btn btn-sm" href={chatThreadHref(session.thread_id)}>
                    <ExternalLink size={13} />
                    Open
                  </A>
                  <button
                    class="btn btn-sm btn-danger"
                    type="button"
                    disabled={stopping()}
                    onClick={() => props.onStop(session)}
                  >
                    <Square size={11} fill="currentColor" />
                    {stopping() ? "Stopping..." : "Stop"}
                  </button>
                </div>
              </li>
            );
          }}
        </For>
      </ul>
    </section>
  );
}
