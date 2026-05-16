import { A, useParams } from "@solidjs/router";
import { ArrowLeft, MessageSquare, RefreshCw, Trash2, User } from "lucide-solid";
import { createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { TranscriptItemView } from "@/components/Transcript";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import type { TranscriptItem, TranscriptTool } from "@/components/Transcript";

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

type HistoryTranscriptItem = TranscriptItem & { key: string };

function toHistoryTranscript(messages: ThreadMessage[]): HistoryTranscriptItem[] {
  const items: HistoryTranscriptItem[] = [];
  const pendingByCallId = new Map<string, TranscriptTool>();

  const findPendingToolByName = (name?: string | null): TranscriptTool | undefined => {
    if (!name) {
      return undefined;
    }
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.type === "tool" && item.name === name && item.result === null) {
        return item;
      }
    }
    return undefined;
  };

  messages.forEach((msg, messageIndex) => {
    if (msg.role === "tool") {
      const target =
        (msg.tool_call_id ? pendingByCallId.get(msg.tool_call_id) : undefined) ||
        findPendingToolByName(msg.name);

      if (target) {
        target.result = msg.content;
        target.tool_call_id = target.tool_call_id || msg.tool_call_id || null;
        target.name = target.name || msg.name || "unknown";
        return;
      }

      items.push({
        key: `tool-result-${messageIndex}-${msg.tool_call_id || msg.name || "unknown"}`,
        type: "tool",
        tool_call_id: msg.tool_call_id || null,
        name: msg.name || "unknown",
        args: null,
        result: msg.content,
      });
      return;
    }

    if (msg.tool_calls?.length) {
      msg.tool_calls.forEach((toolCall, toolCallIndex) => {
        const item: HistoryTranscriptItem = {
          key: `tool-call-${messageIndex}-${toolCallIndex}-${toolCall.id || "unknown"}`,
          type: "tool",
          tool_call_id: toolCall.id || null,
          name: toolCall.name || "unknown",
          args: toolCall.args ?? null,
          result: null,
        };
        items.push(item);
        if (toolCall.id) {
          pendingByCallId.set(toolCall.id, item);
        }
      });
    }

    if (msg.content || !msg.tool_calls?.length) {
      items.push({
        key: `message-${messageIndex}-${msg.id || "unknown"}`,
        type: "message",
        role: msg.role,
        text: msg.content,
      });
    }
  });

  return items;
}

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
  const transcriptItems = createMemo(() => toHistoryTranscript(data()?.messages || []));

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
                  when={transcriptItems().length > 0}
                  fallback={
                    <div class="empty">No messages in this thread.</div>
                  }
                >
                  <For each={transcriptItems()}>
                    {(item) => <TranscriptItemView item={item} />}
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
