import { A, useParams } from "@solidjs/router";
import { ArrowLeft, MessageSquare, Pencil, RefreshCw, Trash2, User } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { TranscriptItemView } from "@/components/Transcript";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, patchJson } from "@/lib/api";
import {
  chatThreadHref,
  decodeThreadRouteParam,
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import type { ThreadListPayload, ThreadMessagesPayload, ThreadSummary } from "@/lib/chatThreads";

export function ChatHistoryListPage() {
  const [data, setData] = createSignal<ThreadListPayload>();
  const [error, setError] = createSignal("");
  const [editingThreadId, setEditingThreadId] = createSignal<string | null>(null);
  const [editingTitle, setEditingTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<ThreadListPayload>("/dashboard-api/chat-history"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chat history");
    }
  };

  const requestDeleteThread = (thread: ThreadSummary) => {
    setDeleteTarget(thread);
  };

  const cancelDelete = () => {
    setDeleteTarget(null);
  };

  const confirmDeleteThread = async () => {
    const thread = deleteTarget();
    if (!thread) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJson(threadApiPath(thread.thread_id));
      showToast("Thread deleted.", "success");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const startRenameThread = (thread: ThreadSummary) => {
    setEditingThreadId(thread.thread_id);
    setEditingTitle(thread.title || thread.thread_id);
  };

  const cancelRenameThread = () => {
    setEditingThreadId(null);
    setEditingTitle("");
  };

  const finishRenameThread = async (thread: ThreadSummary) => {
    if (editingThreadId() !== thread.thread_id) {
      return;
    }

    const trimmedTitle = editingTitle().trim();
    if (!trimmedTitle) {
      showToast("Thread name cannot be empty.", "warning");
      cancelRenameThread();
      return;
    }

    if (trimmedTitle === (thread.title || thread.thread_id).trim()) {
      cancelRenameThread();
      return;
    }

    try {
      const updated = await patchJson<ThreadSummary>(
        threadApiPath(thread.thread_id),
        { title: trimmedTitle },
      );
      setData((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          threads: current.threads.map((thread) =>
            thread.thread_id === updated.thread_id ? { ...thread, ...updated } : thread,
          ),
        };
      });
      cancelRenameThread();
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
      showToast("Thread renamed.", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Rename failed", "error");
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
                            <Show
                              when={editingThreadId() === thread.thread_id}
                              fallback={
                                <A
                                  href={chatThreadHref(thread.thread_id)}
                                  class="history-thread-link"
                                >
                                  <MessageSquare size={13} />
                                  <span class="mono">{truncateText(thread.title || thread.thread_id, 40)}</span>
                                </A>
                              }
                            >
                              <input
                                class="history-thread-rename-input"
                                value={editingTitle()}
                                ref={(element) => {
                                  window.setTimeout(() => {
                                    element.focus();
                                    element.select();
                                  }, 0);
                                }}
                                onInput={(event) => setEditingTitle(event.currentTarget.value)}
                                onBlur={() => void finishRenameThread(thread)}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    event.currentTarget.blur();
                                  }
                                  if (event.key === "Escape") {
                                    event.preventDefault();
                                    cancelRenameThread();
                                  }
                                }}
                              />
                            </Show>
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
                                href={chatThreadHref(thread.thread_id)}
                                class="btn btn-sm"
                              >
                                Chat
                              </A>
                              <button
                                class="btn btn-sm"
                                type="button"
                                onClick={() => startRenameThread(thread)}
                              >
                                <Pencil size={12} />
                              </button>
                              <button
                                class="btn btn-sm btn-danger"
                                type="button"
                                onClick={() => requestDeleteThread(thread)}
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

      <Dialog
        open={deleteTarget() !== null}
        title="Delete Thread"
        onClose={cancelDelete}
        footer={
          <div class="row-wrap">
            <button class="btn" type="button" onClick={cancelDelete} disabled={deleting()}>
              Cancel
            </button>
            <button class="btn btn-danger" type="button" onClick={() => void confirmDeleteThread()} disabled={deleting()}>
              <Trash2 size={14} />
              {deleting() ? "Deleting..." : "Delete"}
            </button>
          </div>
        }
      >
        <p>
          Are you sure you want to delete thread{" "}
          <span class="mono">{truncateText(deleteTarget()?.title || deleteTarget()?.thread_id || "", 60)}</span>?
        </p>
        <p class="muted" style="margin-top: 8px;">This action cannot be undone.</p>
      </Dialog>
    </AppShell>
  );
}

export function ChatHistoryDetailPage() {
  const params = useParams<{ threadId: string }>();
  const [data, setData] = createSignal<ThreadMessagesPayload>();
  const [error, setError] = createSignal("");
  const [editingDetailTitle, setEditingDetailTitle] = createSignal<string | null>(null);
  const transcriptItems = createMemo(() => toThreadTranscript(data()?.messages || []));
  const currentThreadId = createMemo(() => decodeThreadRouteParam(params.threadId || ""));

  const load = async (threadId = currentThreadId()) => {
    if (!threadId) {
      return;
    }
    setError("");
    setData(undefined);
    try {
      setData(
        await apiFetch<ThreadMessagesPayload>(
          threadApiPath(threadId),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load thread");
    }
  };

  const startCurrentThreadRename = () => {
    const threadId = currentThreadId();
    if (!threadId) {
      return;
    }
    setEditingDetailTitle(data()?.title || threadId);
  };

  const cancelCurrentThreadRename = () => {
    setEditingDetailTitle(null);
  };

  const finishCurrentThreadRename = async () => {
    const threadId = currentThreadId();
    const title = editingDetailTitle();
    if (!threadId || title === null) {
      return;
    }

    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      cancelCurrentThreadRename();
      return;
    }

    if (trimmedTitle === (data()?.title || threadId).trim()) {
      cancelCurrentThreadRename();
      return;
    }

    try {
      const updated = await patchJson<ThreadSummary>(
        threadApiPath(threadId),
        { title: trimmedTitle },
      );
      setData((current) => current ? { ...current, ...updated } : current);
      cancelCurrentThreadRename();
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed");
    }
  };

  createEffect(() => {
    void load(currentThreadId());
  });

  return (
    <AppShell
      title="Thread Detail"
      subtitle={
        editingDetailTitle() !== null
          ? (
            <input
              class="page-subtitle-input"
              value={editingDetailTitle() || ""}
              ref={(element) => {
                window.setTimeout(() => {
                  element.focus();
                  element.select();
                }, 0);
              }}
              onInput={(event) => setEditingDetailTitle(event.currentTarget.value)}
              onBlur={() => void finishCurrentThreadRename()}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  event.currentTarget.blur();
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  cancelCurrentThreadRename();
                }
              }}
            />
          )
          : data()?.title || data()?.thread_id || "Loading..."
      }
      actions={
        <div class="row-wrap">
          <A href="/history" class="btn">
            <ArrowLeft size={14} />
            Back
          </A>
          <A href={chatThreadHref(currentThreadId())} class="btn btn-primary">
            <MessageSquare size={14} />
            Continue Chat
          </A>
          <button class="btn" type="button" onClick={startCurrentThreadRename}>
            <Pencil size={14} />
            Rename
          </button>
          <button class="btn" type="button" onClick={() => void load()}>
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
