import { createSignal, For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { Loader2, MessageSquarePlus, Square } from "lucide-solid";

import { useHomeSessions } from "@/components/home/useHomeSessions";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";
import { chatThreadHref } from "@/lib/chatThreads";
import { API_PATHS } from "@/lib/endpoints";
import type { ActiveSession } from "@/types";

export function ActiveSessionsPanel(props: { initial: ActiveSession[] }) {
  const { sessions } = useHomeSessions(props.initial);
  const { showToast } = useToast();
  const [stoppingId, setStoppingId] = createSignal("");
  const [confirmTarget, setConfirmTarget] = createSignal<ActiveSession | null>(null);

  const stop = async (session: ActiveSession) => {
    setConfirmTarget(null);
    setStoppingId(session.session_id);
    try {
      await postJson(API_PATHS.sessionsStop, { session_id: session.session_id });
      showToast("Session stop requested.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to stop session", "error");
    } finally {
      setStoppingId("");
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
          fallback={
            <EmptyState
              icon={<MessageSquarePlus size={20} />}
              message="No agents are running."
              hint="Start a conversation to see live activity here."
              href="/chat"
              action="Start a chat"
            />
          }
        >
          <ul class="session-items">
            <For each={sessions()}>
              {(session) => (
                <li class="session-item">
                  <A
                    class="session-item-link"
                    href={chatThreadHref(session.thread_id)}
                    title="Open thread"
                  >
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
                  </A>
                  <button
                    class="btn btn-sm btn-warning"
                    type="button"
                    title="Stop session"
                    disabled={stoppingId() !== ""}
                    onClick={() => setConfirmTarget(session)}
                  >
                    <Show
                      when={stoppingId() === session.session_id}
                      fallback={<Square size={12} />}
                    >
                      <Loader2 size={12} class="spin-icon" />
                    </Show>
                    Stop
                  </button>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>

      <ConfirmDialog
        open={confirmTarget() !== null}
        title="Stop this session?"
        message={`Agent "${confirmTarget()?.agent_name ?? ""}" will be interrupted. Progress in the current step may be lost.`}
        confirmLabel="Stop session"
        confirmVariant="warning"
        loading={stoppingId() !== ""}
        onClose={() => setConfirmTarget(null)}
        onConfirm={() => {
          const target = confirmTarget();
          if (target) {
            void stop(target);
          }
        }}
      />
    </section>
  );
}

function truncateThread(threadId: string): string {
  if (threadId.length <= 14) return threadId;
  return `${threadId.slice(0, 8)}…${threadId.slice(-4)}`;
}
