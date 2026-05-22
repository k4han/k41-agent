import { A } from "@solidjs/router";
import { FolderOpen, MessageSquare, Pencil, PlaySquare, Trash2 } from "lucide-solid";
import { createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DeleteThreadDialog } from "@/components/DeleteThreadDialog";
import { InlineRenameInput } from "@/components/InlineRenameInput";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, patchJson } from "@/lib/api";
import {
  chatThreadHref,
  groupThreadsByWorkspace,
  threadApiPath,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import type { ThreadListPayload, ThreadSummary } from "@/lib/chatThreads";

export function ChatHistoryListPage() {
  const [data, setData] = createSignal<ThreadListPayload>();
  const [error, setError] = createSignal("");
  const [editingThreadId, setEditingThreadId] = createSignal<string | null>(null);
  const [editingTitle, setEditingTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [selectedWorkspaceKey, setSelectedWorkspaceKey] = createSignal("all");
  const { showToast } = useToast();

  const workspaceGroups = createMemo(() => groupThreadsByWorkspace(data()?.threads || []));
  const isBackgroundThread = (thread: ThreadSummary) => thread.kind === "background";
  const filteredWorkspaceGroups = createMemo(() => {
    const selected = selectedWorkspaceKey();
    if (selected === "all") {
      return workspaceGroups();
    }
    return workspaceGroups().filter((group) => group.key === selected);
  });

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
        <Show when={(data()?.threads.length || 0) > 0}>
          <select
            class="select history-workspace-filter"
            value={selectedWorkspaceKey()}
            onChange={(event) => setSelectedWorkspaceKey(event.currentTarget.value)}
            aria-label="Workspace filter"
          >
            <option value="all">All workspaces</option>
            <For each={workspaceGroups()}>
              {(group) => (
                <option value={group.key}>
                  {group.label}
                </option>
              )}
            </For>
          </select>
        </Show>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <Show
            when={payload.threads.length > 0}
            fallback={<div class="empty">No conversation threads found.</div>}
          >
            <div class="history-workspace-groups">
              <For
                each={filteredWorkspaceGroups()}
                fallback={<div class="empty">No conversations in this workspace.</div>}
              >
                {(group) => (
                  <section class="panel history-workspace-section">
                    <div class="history-workspace-header">
                      <FolderOpen size={15} />
                      <div class="history-workspace-heading">
                        <h2 title={group.label}>{group.label}</h2>
                        <span>{group.threads.length} threads</span>
                      </div>
                    </div>
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
                          <For each={group.threads}>
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
                                        <Show
                                          when={isBackgroundThread(thread)}
                                          fallback={<MessageSquare size={13} />}
                                        >
                                          <PlaySquare size={13} />
                                        </Show>
                                        <span class="mono">
                                          {truncateText(thread.title || thread.thread_id, 40)}
                                        </span>
                                        <Show when={isBackgroundThread(thread)}>
                                          <span class="badge badge-info history-thread-kind">
                                            Background
                                          </span>
                                        </Show>
                                      </A>
                                    }
                                  >
                                    <InlineRenameInput
                                      class="history-thread-rename-input"
                                      value={editingTitle()}
                                      onInput={setEditingTitle}
                                      onBlur={() => void finishRenameThread(thread)}
                                      onCancel={cancelRenameThread}
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
                  </section>
                )}
              </For>
            </div>
          </Show>
        )}
      </DataGate>

      <DeleteThreadDialog
        open={deleteTarget() !== null}
        thread={deleteTarget()}
        deleting={deleting()}
        onClose={cancelDelete}
        onConfirm={() => void confirmDeleteThread()}
      />
    </AppShell>
  );
}
