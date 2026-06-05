import { For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { MessageSquare } from "lucide-solid";

import type { ThreadSummary } from "@/lib/chatThreads";
import { chatThreadHref } from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";

export function RecentThreadsPanel(props: { threads: ThreadSummary[] }) {
  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">Recent chat threads</div>
          <div class="panel-subtitle">Jump back into an ongoing conversation.</div>
        </div>
        <A class="btn btn-sm" href="/history">
          View all
        </A>
      </div>
      <div class="panel-body">
        <Show
          when={props.threads.length > 0}
          fallback={<div class="empty">No chat threads yet.</div>}
        >
          <ul class="compact-list">
            <For each={props.threads}>
              {(thread) => (
                <li class="compact-list-item">
                  <A class="compact-list-link" href={chatThreadHref(thread.thread_id)}>
                    <MessageSquare size={14} class="muted" />
                    <div class="compact-list-main">
                      <div class="compact-list-title">
                        {truncateText(thread.title || thread.thread_id, 64)}
                      </div>
                      <div class="muted compact-list-meta">
                        <span>{thread.agent_name || "default"}</span>
                        <Show when={thread.platform}>
                          <span>·</span>
                          <span>{thread.platform}</span>
                        </Show>
                        <Show when={thread.updated_at}>
                          <span>·</span>
                          <span>{formatRelative(thread.updated_at)}</span>
                        </Show>
                      </div>
                    </div>
                  </A>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>
    </section>
  );
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  const delta = Date.now() - ts;
  if (delta < 0) return "just now";
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (delta < minute) return "just now";
  if (delta < hour) return `${Math.floor(delta / minute)}m ago`;
  if (delta < day) return `${Math.floor(delta / hour)}h ago`;
  return `${Math.floor(delta / day)}d ago`;
}
