import { For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { Play, Square, X } from "lucide-solid";

import { useHomeSessions } from "@/components/home/useHomeSessions";
import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { ActiveSession } from "@/types";

export function ActiveSessionsPanel(props: { initial: ActiveSession[] }) {
  const { sessions } = useHomeSessions(props.initial);
  const { showToast } = useToast();

  const stop = async (sessionId: string) => {
    try {
      await postJson(API_PATHS.sessionsStop, { session_id: sessionId });
      showToast("Session stop requested.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to stop session", "error");
    }
  };

  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">Active sessions</div>
          <div class="panel-subtitle">
            Agents running right now. Updates live.
          </div>
        </div>
        <div class="muted">{sessions().length} running</div>
      </div>
      <div class="panel-body session-list">
        <Show
          when={sessions().length > 0}
          fallback={<div class="empty">No agents are running.</div>}
        >
          <ul class="session-items">
            <For each={sessions()}>
              {(session) => (
                <li class="session-item">
                  <div class="session-item-main">
                    <div class="session-item-name">
                      <span class="session-agent">{session.agent_name}</span>
                      <span class="muted session-item-thread" title={session.thread_id}>
                        {truncateThread(session.thread_id)}
                      </span>
                    </div>
                    <div class="session-item-meta muted">
                      <span>{session.platform}</span>
                      <span>·</span>
                      <span>{session.current_step}</span>
                      <span>·</span>
                      <span>{session.elapsed_display}</span>
                    </div>
                  </div>
                  <button
                    class="btn btn-sm btn-warning"
                    type="button"
                    title="Stop session"
                    onClick={() => stop(session.session_id)}
                  >
                    <Square size={12} />
                    Stop
                  </button>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>
    </section>
  );
}

function truncateThread(threadId: string): string {
  if (threadId.length <= 14) return threadId;
  return `${threadId.slice(0, 8)}…${threadId.slice(-4)}`;
}
