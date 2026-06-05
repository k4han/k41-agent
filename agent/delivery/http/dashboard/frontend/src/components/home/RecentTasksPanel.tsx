import { For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { ExternalLink } from "lucide-solid";

import { StatusBadge } from "@/components/StatusBadge";
import { truncateText } from "@/lib/utils";
import type { BackgroundTask } from "@/types";

export function RecentTasksPanel(props: { tasks: BackgroundTask[] }) {
  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">Recent background tasks</div>
          <div class="panel-subtitle">Last 5 jobs submitted.</div>
        </div>
        <A class="btn btn-sm" href="/tasks">
          View all
        </A>
      </div>
      <div class="panel-body">
        <Show
          when={props.tasks.length > 0}
          fallback={<div class="empty">No background tasks yet.</div>}
        >
          <ul class="compact-list">
            <For each={props.tasks}>
              {(task) => (
                <li class="compact-list-item">
                  <div class="compact-list-main">
                    <div class="compact-list-title">
                      {truncateText(task.request || "(no description)", 80)}
                    </div>
                    <div class="muted compact-list-meta">
                      <span>{task.agent_name}</span>
                      <span>·</span>
                      <span>{task.elapsed_display}</span>
                    </div>
                  </div>
                  <StatusBadge status={task.status} />
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>
    </section>
  );
}
