import "@/styles/routes/history.css";

import { A, useSearchParams } from "@solidjs/router";
import { Download, Folder, Folders, FolderOpen, GitBranch, MessageSquare, MoreVertical, Pencil, PlaySquare, Trash2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, on, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { AppShell } from "@/components/AppShell";
import { DashboardTable } from "@/components/DashboardTable";
import { DeleteThreadDialog } from "@/components/DeleteThreadDialog";
import { EmptyState } from "@/components/EmptyState";
import { ExportChatDialog } from "@/components/ExportChatDialog";
import { InlineRenameInput } from "@/components/InlineRenameInput";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, patchJson } from "@/lib/api";
import {
  chatThreadHref,
  threadApiPath,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import { ALL_WORKSPACES_KEY } from "@/lib/workspaceConstants";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { useThreadWorkspaceGroups } from "@/lib/useThreadWorkspaceGroups";
import { useHistoryPagination } from "@/lib/useHistoryPagination";
import type { ThreadListPayload, ThreadSummary } from "@/lib/chatThreads";

export function ChatHistoryListPage() {
  const [searchParams] = useSearchParams();
  const [data, setData] = createSignal<ThreadListPayload>();
  const [error, setError] = createSignal("");
  const [editingThreadId, setEditingThreadId] = createSignal<string | null>(null);
  const [editingTitle, setEditingTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [exportTarget, setExportTarget] = createSignal<ThreadSummary | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = createSignal(false);
  const [deleting, setDeleting] = createSignal(false);
  const [selectedThreadIds, setSelectedThreadIds] = createSignal<Set<string>>(new Set());
  const [selectedWorkspaceKey, setSelectedWorkspaceKey] = createSignal(String(searchParams.workspace || ALL_WORKSPACES_KEY));
  const { showToast } = useToast();

  createEffect(on(() => searchParams.workspace, (key) => {
    setSelectedWorkspaceKey(String(key || ALL_WORKSPACES_KEY));
  }));

  const workspaceGroups = useThreadWorkspaceGroups(() => data()?.threads || []);
  const workspaceFilterOptions = createMemo(() => [
    {
      value: ALL_WORKSPACES_KEY,
      label: "All workspaces",
      icon: <Folders size={13} />,
    },
    ...workspaceGroups().map((group) => ({
      value: group.key,
      label: group.label,
      icon: group.isRepo ? <GitBranch size={13} /> : <Folder size={13} />,
    })),
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
  const pagination = useHistoryPagination(filteredWorkspaceGroups, selectedWorkspaceKey);

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
        const existing = current.threads.find((item) => item.thread_id === updated.thread_id);
        const merged: ThreadSummary = {
          ...existing,
          ...updated,
          updated_at: updated.updated_at || new Date().toISOString(),
        };
        return {
          ...current,
          threads: [
            merged,
            ...current.threads.filter((item) => item.thread_id !== updated.thread_id),
          ],
        };
      });
      cancelRenameThread();
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
      showToast("Thread renamed.", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Rename failed", "error");
    }
  };

  const handleThreadTitleUpdated = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string; title: string }>;
    const { threadId, title } = customEvent.detail;
    if (!threadId || !title) {
      return;
    }
    setData((current) => {
      if (!current || !current.threads.some((item) => item.thread_id === threadId)) {
        return current;
      }
      const existing = current.threads.find((item) => item.thread_id === threadId)!;
      const updated: ThreadSummary = {
        ...existing,
        title,
        updated_at: new Date().toISOString(),
      };
      return {
        ...current,
        threads: [
          updated,
          ...current.threads.filter((item) => item.thread_id !== threadId),
        ],
      };
    });
  };

  onMount(() => {
    void load();
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);
  });

  onCleanup(() => {
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);
  });

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
            when={payload().threads.length > 0}
            fallback={
              <EmptyState
                icon={<MessageSquare size={24} />}
                message="No conversation threads found."
                hint="Start a new chat to begin interacting with the agent."
                href="/chat"
                action="New chat"
                shortcutAction="new-session"
              />
            }
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
                each={pagination.visibleGroups()}
                fallback={
                  <EmptyState
                    icon={<MessageSquare size={24} />}
                    message="No conversations in this workspace."
                    hint="Start a new conversation in this workspace to see history here."
                    href="/chat"
                    action="New chat"
                    shortcutAction="new-session"
                  />
                }
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
                      tableClass="history-table"
                      columns={[
                        {
                          class: "history-select-cell",
                          header: (
                            <input
                              type="checkbox"
                              checked={isGroupSelected(pagination.threads(group))}
                              aria-label={`Select visible ${group.label} threads`}
                              onChange={(event) => setGroupSelected(
                                pagination.threads(group),
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
                      rows={pagination.threads(group)}
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
                            <ThreadRowActions
                              thread={thread}
                              onExport={() => setExportTarget(thread)}
                              onRename={() => startRenameThread(thread)}
                              onDelete={() => requestDeleteThread(thread)}
                            />
                          </td>
                        </tr>
                      )}
                    </DashboardTable>
                  </section>
                )}
              </For>
              <Show when={pagination.pageCount() > 1}>
                <nav class="history-pagination panel" aria-label="Chat history pagination">
                  <span class="muted" role="status">
                    Page {pagination.pageIndex() + 1} of {pagination.pageCount()} · {pagination.total()} threads
                  </span>
                  <div class="row-wrap">
                    <button class="btn btn-sm" type="button" disabled={pagination.pageIndex() === 0}
                      onClick={pagination.previous}>Previous</button>
                    <button class="btn btn-sm" type="button" disabled={pagination.pageIndex() + 1 >= pagination.pageCount()}
                      onClick={pagination.next}>Next</button>
                  </div>
                </nav>
              </Show>
            </div>
          </Show>
        )}
      </DataGate>

      <ExportChatDialog target={exportTarget()} onClose={() => setExportTarget(null)} />
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

function ThreadRowActions(props: {
  thread: ThreadSummary;
  onExport: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = createSignal(false);
  const [pos, setPos] = createSignal({ top: 0, left: 0 });
  let triggerRef: HTMLButtonElement | undefined;
  let menuRef: HTMLDivElement | undefined;

  const toggle = (e: MouseEvent) => {
    e.stopPropagation();
    if (open()) {
      setOpen(false);
      return;
    }
    const rect = triggerRef!.getBoundingClientRect();
    const menuWidth = 140;
    const left = Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8));
    const top = rect.bottom + 4 > window.innerHeight - 150 ? rect.top - 120 : rect.bottom + 4;
    setPos({ top, left });
    setOpen(true);
  };

  createEffect(() => {
    if (!open()) return;
    const dismiss = (e: MouseEvent) => {
      if (!menuRef?.contains(e.target as Node) && !triggerRef?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onScrollOrResize = () => setOpen(false);
    document.addEventListener("click", dismiss);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    onCleanup(() => {
      document.removeEventListener("click", dismiss);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    });
  });

  return (
    <div class="thread-row-actions">
      <A href={chatThreadHref(props.thread.thread_id)} class="btn btn-sm">
        Chat
      </A>
      <button
        ref={triggerRef}
        class="btn btn-sm btn-icon"
        type="button"
        title="More actions"
        aria-label="More actions"
        aria-expanded={open()}
        onClick={toggle}
      >
        <MoreVertical size={14} />
      </button>
      <Show when={open()}>
        <Portal>
          <div
            ref={menuRef}
            class="thread-action-menu"
            style={{
              top: `${pos().top}px`,
              left: `${pos().left}px`,
            }}
          >
            <button
              class="thread-action-item"
              type="button"
              onClick={() => {
                setOpen(false);
                props.onExport();
              }}
            >
              <Download size={13} />
              <span>Export</span>
            </button>
            <button
              class="thread-action-item"
              type="button"
              onClick={() => {
                setOpen(false);
                props.onRename();
              }}
            >
              <Pencil size={13} />
              <span>Rename</span>
            </button>
            <button
              class="thread-action-item danger-text"
              type="button"
              onClick={() => {
                setOpen(false);
                props.onDelete();
              }}
            >
              <Trash2 size={13} />
              <span>Delete</span>
            </button>
          </div>
        </Portal>
      </Show>
    </div>
  );
}
