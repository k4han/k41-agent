import { A } from "@solidjs/router";
import { FolderOpen, MessageSquare, Pencil, PlaySquare, Trash2 } from "lucide-solid";
import { createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DashboardTable } from "@/components/DashboardTable";
import { DeleteThreadDialog } from "@/components/DeleteThreadDialog";
import { InlineRenameInput } from "@/components/InlineRenameInput";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, patchJson } from "@/lib/api";
import {
  chatThreadHref,
  groupThreadsByWorkspace,
  threadApiPath,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import { ALL_WORKSPACES_KEY } from "@/lib/workspaceConstants";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { ThreadListPayload, ThreadSummary } from "@/lib/chatThreads";

export function ChatHistoryListPage() {
  const [data, setData] = createSignal<ThreadListPayload>();
  const [error, setError] = createSignal("");
  const [editingThreadId, setEditingThreadId] = createSignal<string | null>(null);
  const [editingTitle, setEditingTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = createSignal(false);
  const [deleting, setDeleting] = createSignal(false);
  const [selectedThreadIds, setSelectedThreadIds] = createSignal<Set<string>>(new Set());
  const [selectedWorkspaceKey, setSelectedWorkspaceKey] = createSignal(ALL_WORKSPACES_KEY);
  const { showToast } = useToast();

  const workspaceGroups = createMemo(() => {
    return groupThreadsByWorkspace(data()?.threads || []).map((group) => {
      const isRepo = group.threads.some((t) => t.workspace?.metadata?.repository_full_name);
      return {
        ...group,
        isRepo,
      };
    });
  });
  const workspaceFilterOptions = createMemo(() => [
    { value: ALL_WORKSPACES_KEY, label: "🗂 All workspaces" },
    ...workspaceGroups().map((group) => {
      const icon = group.isRepo ? "⎇" : "🗀";
      return {
        value: group.key,
        label: `${icon} ${group.label}`,
      };
    }),
  ]);
  const selectedCount = createMemo(() => selectedThreadIds().size);
  const isBackgroundThread = (thread: ThreadSummary) => thread.kind === "background";
  const filteredWorkspaceGroups = createMemo(() => {
    const selected = selectedWorkspaceKey();
    if (selected === ALL_WORKSPACES_KEY) {
      return workspaceGroups();
    }
    return workspaceGroups().filter((group) => group.key === selected);
  });

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<ThreadListPayload>("/dashboard-api/chat-history");
      setData(payload);
      setSelectedThreadIds((current) => {
        const knownIds = new Set(payload.threads.map((thread) => thread.thread_id));
        return new Set([...current].filter((threadId) => knownIds.has(threadId)));
      });
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

  const cancelBulkDelete = () => {
    setBulkDeleteOpen(false);
  };

  const setThreadSelected = (threadId: string, selected: boolean) => {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      if (selected) {
        next.add(threadId);
      } else {
        next.delete(threadId);
      }
      return next;
    });
  };

  const setGroupSelected = (threads: ThreadSummary[], selected: boolean) => {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      threads.forEach((thread) => {
        if (selected) {
          next.add(thread.thread_id);
        } else {
          next.delete(thread.thread_id);
        }
      });
      return next;
    });
  };

  const isGroupSelected = (threads: ThreadSummary[]) => (
    threads.length > 0 && threads.every((thread) => selectedThreadIds().has(thread.thread_id))
  );

  const requestDeleteSelected = () => {
    if (selectedCount() === 0) {
      return;
    }
    setBulkDeleteOpen(true);
  };

  const confirmDeleteThread = async () => {
    const thread = deleteTarget();
    if (!thread) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJson(threadApiPath(thread.thread_id));
      setThreadSelected(thread.thread_id, false);
      showToast("Thread deleted.", "success");
      await load();
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const confirmDeleteSelected = async () => {
    const threadIds = [...selectedThreadIds()];
    if (threadIds.length === 0) {
      setBulkDeleteOpen(false);
      return;
    }

    setDeleting(true);
    try {
      const results = await Promise.allSettled(
        threadIds.map((threadId) => deleteJson(threadApiPath(threadId))),
      );
      const failedThreadIds = results.flatMap((result, index) =>
        result.status === "rejected" ? [threadIds[index]] : [],
      );
      const deletedCount = threadIds.length - failedThreadIds.length;

      if (deletedCount > 0) {
        showToast(
          `${deletedCount} thread${deletedCount === 1 ? "" : "s"} deleted.`,
          "success",
        );
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
      }
      if (failedThreadIds.length > 0) {
        showToast(
          `${failedThreadIds.length} delete${failedThreadIds.length === 1 ? "" : "s"} failed.`,
          "error",
        );
      }

      setSelectedThreadIds(new Set(failedThreadIds));
      await load();
    } finally {
      setDeleting(false);
      setBulkDeleteOpen(false);
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
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
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
          <SelectControl
            class="history-workspace-filter"
            value={selectedWorkspaceKey()}
            options={workspaceFilterOptions()}
            onChange={setSelectedWorkspaceKey}
            ariaLabel="Workspace filter"
          />
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
              <Show when={selectedCount() > 0}>
                <div class="history-bulk-actions panel">
                  <span class="muted">{selectedCount()} selected</span>
                  <div class="row-wrap">
                    <button
                      class="btn btn-sm"
                      type="button"
                      disabled={deleting()}
                      onClick={() => setSelectedThreadIds(new Set())}
                    >
                      Clear
                    </button>
                    <button
                      class="btn btn-sm btn-danger"
                      type="button"
                      disabled={deleting()}
                      onClick={requestDeleteSelected}
                    >
                      <Trash2 size={13} />
                      Delete
                    </button>
                  </div>
                </div>
              </Show>
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
                    <DashboardTable
                      columns={[
                        {
                          class: "history-select-cell",
                          header: (
                            <input
                              type="checkbox"
                              checked={isGroupSelected(group.threads)}
                              aria-label={`Select ${group.label} threads`}
                              onChange={(event) => setGroupSelected(
                                group.threads,
                                event.currentTarget.checked,
                              )}
                            />
                          ),
                        },
                        { header: "Thread" },
                        { header: "Platform" },
                        { header: "User" },
                        { header: "Steps" },
                        {},
                      ]}
                      rows={group.threads}
                    >
                      {(thread) => (
                        <tr>
                          <td class="history-select-cell">
                            <input
                              type="checkbox"
                              checked={selectedThreadIds().has(thread.thread_id)}
                              aria-label={`Select ${thread.title || thread.thread_id}`}
                              onChange={(event) => setThreadSelected(
                                thread.thread_id,
                                event.currentTarget.checked,
                              )}
                            />
                          </td>
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
                                    fallback={<MessageSquare size={13} class="history-thread-icon" />}
                                  >
                                    <PlaySquare size={13} class="history-thread-icon task" />
                                  </Show>
                                  <span class="mono">
                                    {truncateText(thread.title || thread.thread_id, 40)}
                                  </span>
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
                    </DashboardTable>
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
      <DeleteThreadDialog
        open={bulkDeleteOpen()}
        thread={null}
        threadCount={selectedCount()}
        deleting={deleting()}
        onClose={cancelBulkDelete}
        onConfirm={() => void confirmDeleteSelected()}
      />
    </AppShell>
  );
}
