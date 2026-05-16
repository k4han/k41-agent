import { A, useParams } from "@solidjs/router";
import { ArrowLeft, Clock, MessageSquare, RefreshCw, Trash2, User } from "lucide-solid";
import { createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson } from "@/lib/api";
import { formatValue, truncateText } from "@/lib/utils";

type ThreadSummary = {
  thread_id: string;
  latest_checkpoint_id: string;
  checkpoint_count: number;
  platform: string;
  user_id: string;
  channel_id: string;
};

type ThreadListPayload = {
  threads: ThreadSummary[];
};

type ThreadMessage = {
  id: string | null;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{ id: string; name: string; args: unknown }>;
};

type ThreadMessagesPayload = {
  thread_id: string;
  messages: ThreadMessage[];
  platform: string;
  user_id: string;
  channel_id: string;
};

export function ChatHistoryListPage() {
  const [data, setData] = createSignal<ThreadListPayload>();
  const [error, setError] = createSignal("");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<ThreadListPayload>("/dashboard-api/chat-history"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chat history");
    }
  };

  const deleteThread = async (threadId: string) => {
    if (!confirm(`Delete thread "${threadId}"?`)) {
      return;
    }
    try {
      await deleteJson(`/dashboard-api/chat-history/${threadId}`);
      showToast("Thread deleted.", "success");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    }
  };

  onMount(load);

  return (
    <AppShell
      title="Chat History"
      subtitle="Browse past conversations stored in the checkpoint database."
      actions={
        <button class="btn" type="button" onClick={load}>
          <RefreshCw size={14} />
          Refresh
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <Show
            when={payload.threads.length > 0}
            fallback={<div class="empty">No conversation threads found.</div>}
          >
            <div class="panel">
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Thread</th>
                      <th>Platform</th>
                      <th>User</th>
                      <th>Steps</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    <For each={payload.threads}>
                      {(thread) => (
                        <tr>
                          <td>
                            <A
                              href={`/history/${encodeURIComponent(thread.thread_id)}`}
                              class="history-thread-link"
                            >
                              <MessageSquare size={13} />
                              <span class="mono">{truncateText(thread.thread_id, 40)}</span>
                            </A>
                          </td>
                          <td>
                            <span class="badge">{thread.platform}</span>
                          </td>
                          <td>
                            <span class="muted">{truncateText(thread.user_id, 24)}</span>
                          </td>
                          <td>
                            <span class="muted">{thread.checkpoint_count}</span>
                          </td>
                          <td>
                            <div class="row-wrap">
                              <A
                                href={`/history/${encodeURIComponent(thread.thread_id)}`}
                                class="btn btn-sm"
                              >
                                View
                              </A>
                              <button
                                class="btn btn-sm btn-danger"
                                type="button"
                                onClick={() => deleteThread(thread.thread_id)}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </div>
          </Show>
        )}
      </DataGate>
    </AppShell>
  );
}

export function ChatHistoryDetailPage() {
  const params = useParams<{ threadId: string }>();
  const [data, setData] = createSignal<ThreadMessagesPayload>();
  const [error, setError] = createSignal("");

  const load = async () => {
    setError("");
    try {
      const threadId = decodeURIComponent(params.threadId);
      setData(
        await apiFetch<ThreadMessagesPayload>(
          `/dashboard-api/chat-history/${threadId}`,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load thread");
    }
  };

  onMount(load);

  return (
    <AppShell
      title="Thread Detail"
      subtitle={data()?.thread_id || "Loading..."}
      actions={
        <div class="row-wrap">
          <A href="/history" class="btn">
            <ArrowLeft size={14} />
            Back
          </A>
          <button class="btn" type="button" onClick={load}>
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="row-wrap">
              <span class="badge">{payload.platform}</span>
              <span class="badge">
                <User size={11} />
                {payload.user_id}
              </span>
              <Show when={payload.channel_id}>
                <span class="badge">{payload.channel_id}</span>
              </Show>
              <span class="badge">{payload.messages.length} messages</span>
            </div>

            <div class="panel">
              <div class="history-transcript">
                <Show
                  when={payload.messages.length > 0}
                  fallback={
                    <div class="empty">No messages in this thread.</div>
                  }
                >
                  <For each={payload.messages}>
                    {(msg) => (
                      <Show
                        when={msg.role !== "tool"}
                        fallback={
                          <details class="tool-call">
                            <summary>
                              <span class="hint">Tool result</span>{" "}
                              <span class="mono">{msg.name}</span>
                            </summary>
                            <pre>{truncateText(msg.content, 2000)}</pre>
                          </details>
                        }
                      >
                        <Show
                          when={!msg.tool_calls?.length}
                          fallback={
                            <div class="history-tool-calls">
                              <For each={msg.tool_calls}>
                                {(tc) => (
                                  <details class="tool-call" open>
                                    <summary>
                                      <span class="hint">Tool call</span>{" "}
                                      <span class="mono">{tc.name}</span>
                                    </summary>
                                    <pre>{formatValue(tc.args)}</pre>
                                  </details>
                                )}
                              </For>
                            </div>
                          }
                        >
                          <div class={`message ${msg.role}`}>
                            <div class="message-bubble">
                              <div class="hint">{msg.role}</div>
                              {msg.content}
                            </div>
                          </div>
                        </Show>
                      </Show>
                    )}
                  </For>
                </Show>
              </div>
            </div>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
