import {
  ChevronDown,
  ChevronRight,
  Clipboard,
  File,
  Folder,
  GitCompare,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Trash2,
  X,
  Zap,
} from "lucide-solid";
import { createEffect, createMemo, createResource, createSignal, For, onCleanup, Show, untrack } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import { highlightCode, languageFromPath } from "@/lib/codeHighlight";
import { renderUnifiedDiffHtml } from "@/lib/diffView";
import { getBackendIcon } from "@/lib/iconRegistry";
import { createDarkMode } from "@/lib/theme";
import { formatWorkspaceRoot, localWorkspaceRef, workspaceDisplayLabel } from "@/lib/workspace";
import type { WorkspaceRef, WorkspaceUsagePayload, WorkspaceBackendKey } from "@/types";
import { isSandboxBackend } from "@/types";

type WorkspaceTreeEntry = {
  name: string;
  path: string;
  kind: "directory" | "file";
  size: number | null;
  modified_at: number;
};

type WorkspaceTreePayload = {
  root: string;
  path: string;
  entries: WorkspaceTreeEntry[];
  truncated: boolean;
};

type WorkspaceChange = {
  path: string;
  status: string;
  additions?: number;
  deletions?: number;
  old_path?: string;
  index_status?: string;
  working_tree_status?: string;
};

type WorkspaceChangesPayload = {
  root: string;
  is_git_repo: boolean;
  changes: WorkspaceChange[];
  message: string;
};

type WorkspaceDiffPayload = {
  root: string;
  path: string;
  is_git_repo: boolean;
  status: string;
  diff: string;
  truncated: boolean;
  message: string;
};

type WorkspaceFilePayload = {
  root: string;
  path: string;
  mime_type: string;
  size: number;
  content: string;
  truncated: boolean;
  binary: boolean;
  message: string;
};

type WorkspaceResolvePayload = {
  kind: string;
  label: string;
  workspace: WorkspaceRef;
};

type WorkspaceTab = "changes" | "files" | `file:${string}`;

function workspaceQuery(threadId: string, workspace: WorkspaceRef | null, extra?: Record<string, string>) {
  const params = new URLSearchParams();
  if (threadId) {
    params.set("thread_id", threadId);
  }
  if (workspace?.locator.trim()) {
    params.set("backend", workspace.backend);
    params.set("locator", workspace.locator.trim());
    const root = workspace.metadata?.root;
    if (
      isSandboxBackend(workspace.backend)
      && typeof root === "string"
      && root.trim()
    ) {
      params.set("root", root.trim());
    }
  }
  Object.entries(extra || {}).forEach(([key, value]) => {
    params.set(key, value);
  });
  return params.toString();
}

function formatFileSize(size: number | null): string {
  if (!size || size <= 0) {
    return "";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string): string {
  if (!status) {
    return "diff";
  }
  return status.replace("_", " ");
}

function fileTabId(path: string): WorkspaceTab {
  return `file:${path}`;
}

function fileTabPath(tab: WorkspaceTab): string {
  return tab.startsWith("file:") ? tab.slice("file:".length) : "";
}

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function DiffView(props: { diff: string; path: string }) {
  const html = createMemo(() => renderUnifiedDiffHtml(props.diff, props.path, { sideBySide: true }));
  return <div class="workspace-diff2html" innerHTML={html()} />;
}

function FileCodeView(props: { content: string; path: string; dark: boolean }) {
  const [highlighted] = createResource(
    () => ({ content: props.content, path: props.path, dark: props.dark }),
    async ({ content, path, dark }) => {
      const lang = languageFromPath(path);
      return highlightCode(content, lang, dark);
    },
  );
  return (
    <div class="workspace-file-view">
      <Show
        when={highlighted()}
        fallback={<pre class="workspace-file-view-plain">{props.content}</pre>}
      >
        <div class="workspace-file-view-shiki" innerHTML={highlighted()} />
      </Show>
    </div>
  );
}

function BackendIcon(props: { backend: WorkspaceBackendKey }) {
  const iconFn = getBackendIcon(props.backend);
  return iconFn();
}

async function writeToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(textarea);
  }
}

export function WorkspaceExplorer(props: {
  threadId: string;
  workingDir: string;
  workspace?: WorkspaceRef | null;
  disabled?: boolean;
  onWorkingDirChange: (value: WorkspaceRef | string | null) => void;
}) {
  const dark = createDarkMode();
  const { showToast } = useToast();
  const [draftWorkingDir, setDraftWorkingDir] = createSignal(props.workingDir);
  const [entriesByPath, setEntriesByPath] = createSignal<Record<string, WorkspaceTreeEntry[]>>({});
  const [expandedByPath, setExpandedByPath] = createSignal<Record<string, boolean>>({ "": true });
  const [treeLoadingByPath, setTreeLoadingByPath] = createSignal<Record<string, boolean>>({});
  const [treeError, setTreeError] = createSignal("");
  const [treeTruncatedByPath, setTreeTruncatedByPath] = createSignal<Record<string, boolean>>({});
  const [changes, setChanges] = createSignal<WorkspaceChange[]>([]);
  const [changesLoading, setChangesLoading] = createSignal(false);
  const [changesError, setChangesError] = createSignal("");
  const [gitMessage, setGitMessage] = createSignal("");
  const [isGitRepo, setIsGitRepo] = createSignal(true);
  const [expandedChangePath, setExpandedChangePath] = createSignal("");
  const [diffPayload, setDiffPayload] = createSignal<WorkspaceDiffPayload | null>(null);
  const [diffLoading, setDiffLoading] = createSignal(false);
  const [diffError, setDiffError] = createSignal("");
  const [activeTab, setActiveTab] = createSignal<WorkspaceTab>("files");
  const [fileTabs, setFileTabs] = createSignal<string[]>([]);
  const [filePayloads, setFilePayloads] = createSignal<Record<string, WorkspaceFilePayload>>({});
  const [fileLoadingByPath, setFileLoadingByPath] = createSignal<Record<string, boolean>>({});
  const [fileErrorByPath, setFileErrorByPath] = createSignal<Record<string, string>>({});
  const [actionMenuPath, setActionMenuPath] = createSignal<string | null>(null);
  const [renameTarget, setRenameTarget] = createSignal<WorkspaceTreeEntry | null>(null);
  const [renameDraft, setRenameDraft] = createSignal("");
  const [renaming, setRenaming] = createSignal(false);
  const [deleteTarget, setDeleteTarget] = createSignal<WorkspaceTreeEntry | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [workspaceRoot, setWorkspaceRoot] = createSignal("");
  const [reconnectingSandbox, setReconnectingSandbox] = createSignal(false);
  let generation = 0;

  const effectiveWorkspace = createMemo(() => props.workspace || localWorkspaceRef(props.workingDir));
  // Network queries must use a *real* workspace ref, never the local fallback
  // synthesised from `workingDir`. The fallback exists purely for display and
  // would otherwise turn a GitHub repo name (e.g. "facebook/react") or a
  // not-yet-attached sandbox id into a bogus local locator, which the backend
  // resolves as a missing directory and answers with HTTP 404. When only a
  // draft is selected (no resolved workspace), we leave the locator empty and
  // let the backend resolve the workspace from `thread_id` alone.
  const queryWorkspace = createMemo(() => props.workspace ?? null);
  const isLocalWorkspace = () => (effectiveWorkspace()?.backend || "local") === "local";
  const effectiveBackend = (): WorkspaceBackendKey => {
    const backend = effectiveWorkspace()?.backend;
    if (backend && isSandboxBackend(backend)) {
      return backend as WorkspaceBackendKey;
    }
    return "local";
  };
  const rootPath = () => workspaceRoot() || "";
  const rootEntries = () => entriesByPath()[rootPath()] || entriesByPath()[""] || [];
  const rootTreeTruncated = () =>
    treeTruncatedByPath()[rootPath()] || treeTruncatedByPath()[""] || false;
  const canQuery = () => Boolean(queryWorkspace()?.locator.trim() || props.threadId);
  const activeFilePath = () => fileTabPath(activeTab());
  const activeFilePayload = () => filePayloads()[activeFilePath()];
  const workingDirDisplayValue = () =>
    isLocalWorkspace()
      ? props.disabled ? formatWorkspaceRoot(draftWorkingDir()) : draftWorkingDir()
      : workspaceDisplayLabel(effectiveWorkspace()) || effectiveWorkspace()?.locator || "";

  const loadTree = async (path = "", targetGeneration = generation) => {
    if (!canQuery()) {
      return;
    }
    setTreeLoadingByPath((current) => ({ ...current, [path]: true }));
    if (!path) {
      setTreeError("");
    }
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceTreePayload>(
        `/dashboard-api/workspace/tree?${query}`,
      );
      if (targetGeneration !== generation) {
        return;
      }
      setEntriesByPath((current) => ({ ...current, [payload.path]: payload.entries }));
      setTreeTruncatedByPath((current) => ({ ...current, [payload.path]: payload.truncated }));
      if (payload.root) {
        setWorkspaceRoot(payload.root);
      }
    } catch (err) {
      if (targetGeneration === generation) {
        setTreeError(err instanceof Error ? err.message : "Failed to load workspace tree");
      }
    } finally {
      if (targetGeneration === generation) {
        setTreeLoadingByPath((current) => ({ ...current, [path]: false }));
      }
    }
  };

  const reloadPath = async (path: string) => {
    const targetGeneration = generation;
    await loadTree(path, targetGeneration);
    if (targetGeneration === generation) {
      await loadChanges(targetGeneration);
    }
  };

  const loadChanges = async (targetGeneration = generation) => {
    if (!canQuery()) {
      return;
    }
    setChangesLoading(true);
    setChangesError("");
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace());
      const payload = await apiFetch<WorkspaceChangesPayload>(
        `/dashboard-api/workspace/changes?${query}`,
      );
      if (targetGeneration !== generation) {
        return;
      }
      setChanges(payload.changes || []);
      setGitMessage(payload.message || "");
      setIsGitRepo(payload.is_git_repo);
    } catch (err) {
      if (targetGeneration === generation) {
        setChangesError(err instanceof Error ? err.message : "Failed to load changes");
      }
    } finally {
      if (targetGeneration === generation) {
        setChangesLoading(false);
      }
    }
  };

  const loadDiff = async (path: string, targetGeneration = generation) => {
    if (!path || !canQuery()) {
      return;
    }
    setExpandedChangePath(path);
    setDiffPayload(null);
    setDiffError("");
    setDiffLoading(true);
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceDiffPayload>(
        `/dashboard-api/workspace/diff?${query}`,
      );
      if (targetGeneration === generation && expandedChangePath() === path) {
        setDiffPayload(payload);
      }
    } catch (err) {
      if (targetGeneration === generation && expandedChangePath() === path) {
        setDiffError(err instanceof Error ? err.message : "Failed to load diff");
      }
    } finally {
      if (targetGeneration === generation && expandedChangePath() === path) {
        setDiffLoading(false);
      }
    }
  };

  const loadFile = async (path: string, targetGeneration = generation) => {
    if (!path || !canQuery()) {
      return;
    }
    setFileLoadingByPath((current) => ({ ...current, [path]: true }));
    setFileErrorByPath((current) => ({ ...current, [path]: "" }));
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceFilePayload>(
        `/dashboard-api/workspace/file?${query}`,
      );
      if (targetGeneration !== generation) {
        return;
      }
      setFilePayloads((current) => ({ ...current, [path]: payload }));
    } catch (err) {
      if (targetGeneration === generation) {
        setFileErrorByPath((current) => ({
          ...current,
          [path]: err instanceof Error ? err.message : "Failed to load file",
        }));
      }
    } finally {
      if (targetGeneration === generation) {
        setFileLoadingByPath((current) => ({ ...current, [path]: false }));
      }
    }
  };

  const refresh = () => {
    generation += 1;
    const targetGeneration = generation;
    const savedExpanded = { ...untrack(expandedByPath) };
    setEntriesByPath({});
    setExpandedByPath({ "": true });
    setTreeTruncatedByPath({});
    setWorkspaceRoot("");
    setExpandedChangePath("");
    setDiffPayload(null);
    setDiffError("");
    setFileTabs([]);
    setFilePayloads({});
    setFileLoadingByPath({});
    setFileErrorByPath({});
    if (untrack(activeTab).startsWith("file:")) {
      setActiveTab("files");
    }
    const reloadWithExpansion = async () => {
      await loadTree("", targetGeneration);
      if (targetGeneration !== generation) return;
      const paths = Object.keys(savedExpanded).filter((p) => p !== "" && savedExpanded[p]);
      for (const path of paths) {
        if (targetGeneration !== generation) return;
        await loadTree(path, targetGeneration);
      }
      if (targetGeneration === generation) {
        setExpandedByPath(savedExpanded);
      }
    };
    void reloadWithExpansion();
    void loadChanges(targetGeneration);
  };

  const applyWorkingDir = () => {
    if (!isLocalWorkspace()) {
      showToast("Working directory is fixed for non-local workspaces.", "warning");
      return;
    }
    props.onWorkingDirChange(draftWorkingDir().trim());
  };

  const reconnectSandboxWorkspace = async () => {
    const ws = effectiveWorkspace();
    if (props.disabled || reconnectingSandbox() || !ws || !isSandboxBackend(ws.backend)) {
      return;
    }
    setReconnectingSandbox(true);
    try {
      const payload = await postJson<WorkspaceResolvePayload>(
        "/dashboard-api/workspace/resolve",
        {
          kind: ws.backend,
          thread_id: props.threadId || null,
        },
      );
      props.onWorkingDirChange(payload.workspace);
      generation += 1;
      const targetGeneration = generation;
      setEntriesByPath({});
      setExpandedByPath({ "": true });
      setTreeTruncatedByPath({});
      setWorkspaceRoot("");
      setExpandedChangePath("");
      setDiffPayload(null);
      setDiffError("");
      setFileTabs([]);
      setFilePayloads({});
      setFileLoadingByPath({});
      setFileErrorByPath({});
      queueMicrotask(() => {
        void loadTree("", targetGeneration);
        void loadChanges(targetGeneration);
      });
      showToast(`${ws.backend} workspace reconnected.`, "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : `Failed to reconnect ${ws.backend} workspace`, "error");
    } finally {
      setReconnectingSandbox(false);
    }
  };

  const toggleDirectory = (path: string) => {
    const isOpen = Boolean(expandedByPath()[path]);
    setExpandedByPath((current) => ({ ...current, [path]: !isOpen }));
    if (isOpen || entriesByPath()[path]) {
      return;
    }
    void loadTree(path);
  };

  const toggleChangeDiff = (path: string) => {
    if (expandedChangePath() === path) {
      setExpandedChangePath("");
      setDiffPayload(null);
      setDiffError("");
      return;
    }
    void loadDiff(path);
  };

   const openFile = (path: string) => {
    setFileTabs((current) => current.includes(path) ? current : [...current, path]);
    setActiveTab(fileTabId(path));
    if (!filePayloads()[path] && !fileLoadingByPath()[path]) {
      void loadFile(path);
    }
  };

  const closeFileTab = (path: string, event: MouseEvent) => {
    event.stopPropagation();
    setFileTabs((current) => current.filter((item) => item !== path));
    if (activeTab() === fileTabId(path)) {
      setActiveTab("files");
    }
  };

  const toggleActionMenu = (path: string, event: MouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
    setActionMenuPath(actionMenuPath() === path ? null : path);
  };

  const closeActionMenu = () => setActionMenuPath(null);

  const requestRename = (entry: WorkspaceTreeEntry, event: MouseEvent) => {
    event.stopPropagation();
    closeActionMenu();
    setRenameTarget(entry);
    setRenameDraft(entry.name);
  };

  const cancelRename = () => {
    setRenameTarget(null);
    setRenameDraft("");
  };
  const confirmRename = async () => {
    const entry = renameTarget();
    if (!entry) {
      return;
    }
    const nextName = renameDraft().trim();
    if (!nextName || nextName === entry.name) {
      cancelRename();
      return;
    }
    setRenaming(true);
    try {
      await postJson("/dashboard-api/workspace/rename", {
        thread_id: props.threadId || null,
        workspace: queryWorkspace(),
        path: entry.path,
        new_name: nextName,
      });
      showToast(`Renamed to ${nextName}`, "success");
      const openedPath = entry.path;
      setFileTabs((current) => current.filter((item) => item !== openedPath));
      setFilePayloads((current) => {
        const copy = { ...current };
        delete copy[openedPath];
        return copy;
      });
      if (activeTab() === fileTabId(openedPath)) {
        setActiveTab("files");
      }
      cancelRename();
      const parentPath = entry.path.split("/").slice(0, -1).join("/") || "";
      await reloadPath(parentPath);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Rename failed", "error");
    } finally {
      setRenaming(false);
    }
  };

  const requestDelete = (entry: WorkspaceTreeEntry, event: MouseEvent) => {
    event.stopPropagation();
    closeActionMenu();
    setDeleteTarget(entry);
  };

  const cancelDelete = () => setDeleteTarget(null);

  const confirmDelete = async () => {
    const entry = deleteTarget();
    if (!entry) {
      return;
    }
    setDeleting(true);
    try {
      await postJson("/dashboard-api/workspace/delete", {
        thread_id: props.threadId || null,
        workspace: queryWorkspace(),
        path: entry.path,
      });
      showToast(`Deleted ${entry.name}`, "success");
      const removedPath = entry.path;
      setFileTabs((current) => current.filter((item) => item !== removedPath));
      setFilePayloads((current) => {
        const copy = { ...current };
        delete copy[removedPath];
        return copy;
      });
      if (activeTab() === fileTabId(removedPath)) {
        setActiveTab("files");
      }
      setDeleteTarget(null);
      const parentPath = entry.path.split("/").slice(0, -1).join("/") || "";
      await reloadPath(parentPath);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
    }
  };

  const copyPath = async (entry: WorkspaceTreeEntry, event: MouseEvent) => {
    event.stopPropagation();
    closeActionMenu();
    try {
      await writeToClipboard(entry.path);
      showToast("Copied path", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Copy failed", "error");
    }
  };

  const handleDocumentClick = (event: MouseEvent) => {
    if (!actionMenuPath()) {
      return;
    }
    const target = event.target as HTMLElement | null;
    if (target && target.closest(".workspace-tree-actions")) {
      return;
    }
    closeActionMenu();
  };

  if (typeof document !== "undefined") {
    document.addEventListener("click", handleDocumentClick);
    onCleanup(() => document.removeEventListener("click", handleDocumentClick));
  }

  createEffect(() => {
    setDraftWorkingDir(props.workingDir || "");
  });

  createEffect(() => {
    props.threadId;
    props.workingDir;
    props.workspace?.backend;
    props.workspace?.locator;
    refresh();
  });

  const TreeEntry = (entryProps: { entry: WorkspaceTreeEntry; depth: number }) => {
    const entry = () => entryProps.entry;
    const children = () => entriesByPath()[entry().path] || [];
    const isOpen = () => Boolean(expandedByPath()[entry().path]);
    const isDirectory = () => entry().kind === "directory";
    const isSelected = () => activeTab() === fileTabId(entry().path);
    const isMenuOpen = () => actionMenuPath() === entry().path;

    return (
      <>
        <div
          class={`workspace-tree-item ${isMenuOpen() ? "menu-open" : ""}`}
          style={`--depth: ${entryProps.depth};`}
        >
          <button
            class={`workspace-tree-row ${isSelected() ? "active" : ""}`}
            type="button"
            title={entry().path}
            onClick={() => {
              if (isDirectory()) {
                toggleDirectory(entry().path);
              } else {
                openFile(entry().path);
              }
            }}
          >
            <span class="workspace-tree-caret">
              <Show when={isDirectory()}>
                <Show when={isOpen()} fallback={<ChevronRight size={13} />}>
                  <ChevronDown size={13} />
                </Show>
              </Show>
            </span>
            <span class="workspace-tree-icon">
              <Show when={isDirectory()} fallback={<File size={14} />}>
                <Folder size={14} />
              </Show>
            </span>
            <span class="workspace-tree-name">{entry().name}</span>
            <span class="workspace-tree-meta">{formatFileSize(entry().size)}</span>
          </button>
          <div class="workspace-tree-actions">
            <button
              class={`workspace-tree-action ${isMenuOpen() ? "active" : ""}`}
              type="button"
              title="File actions"
              aria-label="File actions"
              aria-haspopup="menu"
              aria-expanded={isMenuOpen()}
              onClick={(event) => toggleActionMenu(entry().path, event)}
            >
              <MoreHorizontal size={13} />
            </button>
            <Show when={isMenuOpen()}>
              <div class="workspace-tree-menu" role="menu">
                <button
                  class="workspace-tree-menu-item"
                  type="button"
                  role="menuitem"
                  onClick={(event) => requestRename(entry(), event)}
                >
                  <Pencil size={13} />
                  <span>Rename</span>
                </button>
                <button
                  class="workspace-tree-menu-item"
                  type="button"
                  role="menuitem"
                  onClick={(event) => void copyPath(entry(), event)}
                >
                  <Clipboard size={13} />
                  <span>Copy path</span>
                </button>
                <button
                  class="workspace-tree-menu-item workspace-tree-menu-danger"
                  type="button"
                  role="menuitem"
                  onClick={(event) => requestDelete(entry(), event)}
                >
                  <Trash2 size={13} />
                  <span>Delete</span>
                </button>
              </div>
            </Show>
          </div>
        </div>
        <Show when={isDirectory() && isOpen()}>
          <Show when={treeLoadingByPath()[entry().path]}>
            <div class="workspace-tree-status" style={`--depth: ${entryProps.depth + 1};`}>
              Loading...
            </div>
          </Show>
          <For each={children()}>
            {(child) => <TreeEntry entry={child} depth={entryProps.depth + 1} />}
          </For>
          <Show when={treeTruncatedByPath()[entry().path]}>
            <div class="workspace-tree-status" style={`--depth: ${entryProps.depth + 1};`}>
              Tree truncated
            </div>
          </Show>
        </Show>
      </>
    );
  };

  return (
    <aside class="workspace-explorer">
      <div class="workspace-dir-control">
        <span
          class={`workspace-backend-pill backend-${effectiveBackend()}`}
          title={`Workspace backend: ${effectiveBackend()}`}
          aria-label={`Workspace backend ${effectiveBackend()}`}
        >
          <BackendIcon backend={effectiveBackend()} />
          <span>{effectiveBackend()}</span>
        </span>
        <input
          class="input workspace-dir-input"
          value={workingDirDisplayValue()}
          disabled={props.disabled || !isLocalWorkspace()}
          placeholder="Working directory"
          title={draftWorkingDir()}
          onInput={(event) => setDraftWorkingDir(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              applyWorkingDir();
            }
          }}
        />
        <button class="btn btn-sm" type="button" onClick={refresh} disabled={!canQuery()}>
          <RefreshCw size={13} />
        </button>
      </div>

      <div class="workspace-tabs" role="tablist" aria-label="Workspace views">
        <button
          class={`workspace-tab ${activeTab() === "files" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeTab() === "files"}
          onClick={() => setActiveTab("files")}
        >
          <Folder size={13} />
          <span>Files</span>
        </button>
        <button
          class={`workspace-tab ${activeTab() === "changes" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeTab() === "changes"}
          onClick={() => setActiveTab("changes")}
        >
          <GitCompare size={13} />
          <span>Changes</span>
          <Show when={changes().length > 0}>
            <span class="workspace-tab-count">{changes().length}</span>
          </Show>
        </button>
        <For each={fileTabs()}>
          {(path) => (
            <div class={`workspace-tab workspace-file-tab ${activeTab() === fileTabId(path) ? "active" : ""}`}>
              <button
                class="workspace-tab-main"
                type="button"
                role="tab"
                aria-selected={activeTab() === fileTabId(path)}
                title={path}
                onClick={() => setActiveTab(fileTabId(path))}
              >
                <File size={13} />
                <span>{fileName(path)}</span>
              </button>
              <button
                class="workspace-tab-close"
                type="button"
                title="Close file tab"
                aria-label={`Close ${path}`}
                onClick={(event) => closeFileTab(path, event)}
              >
                <X size={12} />
              </button>
            </div>
          )}
        </For>
      </div>

      <div class="workspace-explorer-body">
        <Show when={activeTab() === "changes"}>
          <section class="workspace-section workspace-tab-panel" role="tabpanel">
            <Show
              when={!changesLoading()}
              fallback={<div class="empty compact">Loading changes...</div>}
            >
              <Show
                when={!changesError()}
                fallback={<div class="empty compact">{changesError()}</div>}
              >
                <Show
                  when={changes().length > 0}
                  fallback={<div class="empty compact">{gitMessage() || "No changed files."}</div>}
                >
                  <div class="workspace-change-list">
                    <For each={changes()}>
                      {(change) => (
                        <div
                          class={`workspace-change-item ${expandedChangePath() === change.path ? "expanded" : ""}`}
                        >
                          <button
                            class={`workspace-change-row ${expandedChangePath() === change.path ? "active" : ""}`}
                            type="button"
                            title={change.path}
                            aria-expanded={expandedChangePath() === change.path}
                            onClick={() => toggleChangeDiff(change.path)}
                          >
                            <span class="workspace-change-caret">
                              <Show
                                when={expandedChangePath() === change.path}
                                fallback={<ChevronRight size={13} />}
                              >
                                <ChevronDown size={13} />
                              </Show>
                            </span>
                            <span class="workspace-change-path">{change.path}</span>
                            <span class="workspace-change-stats">
                              <Show when={change.additions !== undefined}>
                                <span class="workspace-change-additions">
                                  +{change.additions}
                                </span>
                              </Show>
                              <Show when={change.deletions !== undefined}>
                                <span class="workspace-change-deletions">
                                  -{change.deletions}
                                </span>
                              </Show>
                            </span>
                            <span class={`workspace-change-status ${change.status}`}>
                              {statusLabel(change.status)}
                            </span>
                          </button>
                          <Show when={expandedChangePath() === change.path}>
                            <div class="workspace-change-diff">
                              <Show
                                when={!diffLoading()}
                                fallback={<div class="empty compact">Loading diff...</div>}
                              >
                                <Show
                                  when={!diffError()}
                                  fallback={<div class="empty compact">{diffError()}</div>}
                                >
                                  <Show
                                    when={diffPayload()?.diff}
                                    fallback={
                                      <div class="empty compact">
                                        {diffPayload()?.message || "No diff available."}
                                      </div>
                                    }
                                  >
                                    <DiffView
                                      diff={diffPayload()?.diff || ""}
                                      path={diffPayload()?.path || change.path}
                                    />
                                    <Show when={diffPayload()?.truncated}>
                                      <div class="hint workspace-hint">Diff truncated.</div>
                                    </Show>
                                  </Show>
                                </Show>
                              </Show>
                            </div>
                          </Show>
                        </div>
                      )}
                    </For>
                  </div>
                </Show>
              </Show>
            </Show>
            <Show when={!isGitRepo() && !changesLoading()}>
              <div class="hint workspace-hint">Diff requires a Git workspace.</div>
            </Show>
          </section>
        </Show>

        <Show when={activeTab() === "files"}>
          <section class="workspace-section workspace-tree-section workspace-tab-panel" role="tabpanel">
            <Show when={!treeError()} fallback={<div class="empty compact">{treeError()}</div>}>
              <Show
                when={rootEntries().length > 0}
                fallback={<div class="empty compact">No files.</div>}
              >
                <div class="workspace-tree">
                  <For each={rootEntries()}>
                    {(entry) => <TreeEntry entry={entry} depth={0} />}
                  </For>
                  <Show when={rootTreeTruncated()}>
                    <div class="workspace-tree-status" style="--depth: 0;">
                      Tree truncated
                    </div>
                  </Show>
                </div>
              </Show>
            </Show>
          </section>
        </Show>

        <Show when={activeTab().startsWith("file:")}>
          <section class="workspace-section workspace-file-panel workspace-tab-panel" role="tabpanel">
            <Show
              when={!fileLoadingByPath()[activeFilePath()]}
              fallback={<div class="empty compact">Loading file...</div>}
            >
              <Show
                when={!fileErrorByPath()[activeFilePath()]}
                fallback={<div class="empty compact">{fileErrorByPath()[activeFilePath()]}</div>}
              >
                <Show
                  when={activeFilePayload() && !activeFilePayload()?.binary}
                  fallback={
                    <div class="empty compact">
                      {activeFilePayload()?.message || "File preview is not available."}
                    </div>
                  }
                >
                  <FileCodeView
                    content={activeFilePayload()?.content || ""}
                    path={activeFilePath()}
                    dark={dark()}
                  />
                  <Show when={activeFilePayload()?.truncated}>
                    <div class="hint workspace-hint">File truncated.</div>
                  </Show>
                </Show>
              </Show>
            </Show>
          </section>
        </Show>
      </div>

      <Dialog
        open={renameTarget() !== null}
        title="Rename"
        onClose={() => {
          if (!renaming()) {
            cancelRename();
          }
        }}
        footer={
          <div class="row-wrap">
            <button
              class="btn"
              type="button"
              onClick={cancelRename}
              disabled={renaming()}
            >
              Cancel
            </button>
            <button
              class="btn btn-primary"
              type="button"
              onClick={() => void confirmRename()}
              disabled={renaming() || !renameDraft().trim()}
            >
              {renaming() ? "Renaming..." : "Rename"}
            </button>
          </div>
        }
      >
        <p class="muted" style="margin-bottom: 8px;" title={renameTarget()?.path}>
          {renameTarget()?.path}
        </p>
        <input
          class="input"
          value={renameDraft()}
          placeholder="New name"
          disabled={renaming()}
          onInput={(event) => setRenameDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void confirmRename();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              cancelRename();
            }
          }}
        />
      </Dialog>

      <Dialog
        open={deleteTarget() !== null}
        title={deleteTarget()?.kind === "directory" ? "Delete folder" : "Delete file"}
        onClose={() => {
          if (!deleting()) {
            cancelDelete();
          }
        }}
        footer={
          <div class="row-wrap">
            <button
              class="btn"
              type="button"
              onClick={cancelDelete}
              disabled={deleting()}
            >
              Cancel
            </button>
            <button
              class="btn btn-danger"
              type="button"
              onClick={() => void confirmDelete()}
              disabled={deleting()}
            >
              <Trash2 size={14} />
              {deleting() ? "Deleting..." : "Delete"}
            </button>
          </div>
        }
      >
        <p>
          Are you sure you want to delete{" "}
          <span class="mono">{deleteTarget()?.path}</span>?
        </p>
        <Show when={deleteTarget()?.kind === "directory"}>
          <p class="muted" style="margin-top: 8px;">
            All contents inside this folder will be removed.
          </p>
        </Show>
        <p class="muted" style="margin-top: 8px;">This action cannot be undone.</p>
      </Dialog>
    </aside>
  );
}
