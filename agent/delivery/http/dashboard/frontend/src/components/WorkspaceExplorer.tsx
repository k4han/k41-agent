import {
  ChevronDown,
  ChevronRight,
  Clipboard,
  File,
  FilePlus,
  Folder,
  FolderPlus,
  GitCompare,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Terminal,
  Trash2,
  X,
  Zap,
} from "lucide-solid";
import { createEffect, createMemo, createResource, createSignal, For, onCleanup, Show, untrack } from "solid-js";
import { Portal } from "solid-js/web";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import { highlightCode, languageFromPath } from "@/lib/codeHighlight";
import { renderUnifiedDiffHtml } from "@/lib/diffView";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
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
  return <>{getBackendIcon(props.backend)()}</>;
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
  onCollapse?: () => void;
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
  const [openTabs, setOpenTabs] = createSignal<WorkspaceTab[]>(["files"]);
  const [activeTab, setActiveTab] = createSignal<WorkspaceTab>("files");
  const [plusMenuOpen, setPlusMenuOpen] = createSignal(false);
  let plusBtnRef: HTMLButtonElement | undefined;
  let plusMenuRef: HTMLDivElement | undefined;
  const [plusMenuPos, setPlusMenuPos] = createSignal({ top: 0, left: 0 });
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
  const [createTarget, setCreateTarget] = createSignal<{
    kind: "file" | "directory";
    parentPath: string;
  } | null>(null);
  const [createName, setCreateName] = createSignal("");
  const [creating, setCreating] = createSignal(false);
  const [workspaceRoot, setWorkspaceRoot] = createSignal("");
  const [reconnectingModal, setReconnectingModal] = createSignal(false);
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
  const isRefreshing = () =>
    Boolean(
      treeLoadingByPath()[rootPath()]
      || treeLoadingByPath()[""]
      || changesLoading(),
    );
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

  const hardRefresh = () => {
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
    setOpenTabs(["files"]);
    setActiveTab("files");
    setFileTabs([]);
    setFilePayloads({});
    setFileLoadingByPath({});
    setFileErrorByPath({});
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

  const softRefresh = () => {
    if (!canQuery()) {
      return;
    }
    generation += 1;
    const targetGeneration = generation;
    const savedExpanded = { ...untrack(expandedByPath) };
    // Keep existing tree entries visible to avoid flicker; only mark root as loading.
    setTreeLoadingByPath((current) => ({ ...current, [""]: true }));
    setTreeError("");
    setChangesLoading(true);
    const reloadWithExpansion = async () => {
      await loadTree("", targetGeneration);
      if (targetGeneration !== generation) return;
      const paths = Object.keys(savedExpanded).filter((p) => p !== "" && savedExpanded[p]);
      for (const path of paths) {
        if (targetGeneration !== generation) return;
        // Skip missing expanded paths (deleted/renamed) silently.
        if (entriesByPath()[path] === undefined && path !== "") {
          // Still attempt to load; tree will stay empty if path gone.
        }
        await loadTree(path, targetGeneration);
      }
      if (targetGeneration === generation) {
        setExpandedByPath(savedExpanded);
      }
    };
    void reloadWithExpansion();
    void loadChanges(targetGeneration);
  };

  const refresh = () => {
    // Keep for external callers (manual refresh button). Use soft refresh to
    // avoid flashing when the workspace hasn't actually changed.
    softRefresh();
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
    if (props.disabled || reconnectingModal() || !ws || !isSandboxBackend(ws.backend)) {
      return;
    }
    setReconnectingModal(true);
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
      setReconnectingModal(false);
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

  const openTab = (tab: WorkspaceTab) => {
    setOpenTabs((current) => (current.includes(tab) ? current : [...current, tab]));
    setActiveTab(tab);
  };

  const closeTab = (tab: WorkspaceTab, event?: MouseEvent) => {
    event?.stopPropagation();
    const current = openTabs();
    const nextTabs = current.filter((t) => t !== tab);
    setOpenTabs(nextTabs);

    if (activeTab() === tab) {
      if (nextTabs.length > 0) {
        const closedIdx = current.indexOf(tab);
        const nextIdx = Math.max(0, closedIdx - 1);
        setActiveTab(nextTabs[nextIdx] || nextTabs[0]);
      } else {
        setActiveTab("" as WorkspaceTab);
      }
    }

    if (tab.startsWith("file:")) {
      const path = fileTabPath(tab);
      setFileTabs((curr) => curr.filter((item) => item !== path));
    }
  };

  const openFile = (path: string) => {
    const tab = fileTabId(path);
    setFileTabs((current) => (current.includes(path) ? current : [...current, path]));
    openTab(tab);
    if (!filePayloads()[path] && !fileLoadingByPath()[path]) {
      void loadFile(path);
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
      closeTab(fileTabId(openedPath));
      setFilePayloads((current) => {
        const copy = { ...current };
        delete copy[openedPath];
        return copy;
      });
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
      closeTab(fileTabId(removedPath));
      setFilePayloads((current) => {
        const copy = { ...current };
        delete copy[removedPath];
        return copy;
      });
      setDeleteTarget(null);
      const parentPath = entry.path.split("/").slice(0, -1).join("/") || "";
      await reloadPath(parentPath);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
    }
  };

  const requestCreate = (
    kind: "file" | "directory",
    parentPath: string = "",
    event?: MouseEvent,
  ) => {
    event?.stopPropagation();
    closeActionMenu();
    setCreateTarget({ kind, parentPath });
    setCreateName("");
  };

  const cancelCreate = () => {
    setCreateTarget(null);
    setCreateName("");
  };

  const confirmCreate = async () => {
    const target = createTarget();
    if (!target) {
      return;
    }
    const cleanName = createName().trim();
    if (!cleanName) {
      cancelCreate();
      return;
    }
    setCreating(true);
    try {
      const parent = target.parentPath.replace(/\\/g, "/").replace(/\/+$/, "");
      const fullPath = parent ? `${parent}/${cleanName}` : cleanName;
      await postJson("/dashboard-api/workspace/create-entry", {
        thread_id: props.threadId || null,
        workspace: queryWorkspace(),
        path: fullPath,
        kind: target.kind,
      });
      showToast(
        `Created ${target.kind === "directory" ? "folder" : "file"} ${cleanName}`,
        "success",
      );
      cancelCreate();
      if (target.parentPath) {
        setExpandedByPath((current) => ({ ...current, [target.parentPath]: true }));
      }
      await reloadPath(target.parentPath);
      if (target.kind === "file") {
        openFile(fullPath);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Creation failed", "error");
    } finally {
      setCreating(false);
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

  const updatePlusMenuPosition = () => {
    if (!plusBtnRef) return;
    const rect = plusBtnRef.getBoundingClientRect();
    const menuWidth = 195;
    let left = rect.left;
    if (left + menuWidth > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menuWidth - 8);
    }
    const spaceBelow = window.innerHeight - rect.bottom;
    let top = rect.bottom + 4;
    if (spaceBelow < 150 && rect.top > spaceBelow) {
      top = Math.max(8, rect.top - 140);
    }
    setPlusMenuPos({ top, left });
  };

  createEffect(() => {
    if (!plusMenuOpen()) return;
    updatePlusMenuPosition();
    const handler = () => updatePlusMenuPosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    onCleanup(() => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    });
  });

  const handleDocumentClick = (event: MouseEvent) => {
    const target = event.target as HTMLElement | null;
    if (actionMenuPath() && !target?.closest(".workspace-tree-actions")) {
      closeActionMenu();
    }
    if (plusMenuOpen()) {
      if (plusBtnRef?.contains(target as Node) || plusMenuRef?.contains(target as Node)) {
        return;
      }
      setPlusMenuOpen(false);
    }
  };

  if (typeof document !== "undefined") {
    document.addEventListener("click", handleDocumentClick);
    onCleanup(() => document.removeEventListener("click", handleDocumentClick));
  }

  // Auto-refresh workspace when the agent finishes a turn (files may have
  // changed). Use soft refresh so expanded folders and open file tabs are
  // preserved and the tree doesn't flash to "No files".
  if (typeof window !== "undefined") {
    const handleWorkspaceRefreshEvent = (event: Event) => {
      const custom = event as CustomEvent<{ threadId?: string }>;
      const tid = custom.detail?.threadId;
      // Only refresh the explorer that belongs to the affected thread (or
      // the global explorer when no thread filter is present).
      if (tid && tid !== props.threadId) {
        return;
      }
      // Defer to next microtask so ChatPage's refreshThread has already
      // updated threadData/workspaceRef if it will.
      queueMicrotask(() => {
        if (!canQuery()) {
          return;
        }
        softRefresh();
      });
    };
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleWorkspaceRefreshEvent);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleWorkspaceRefreshEvent);
    onCleanup(() => {
      window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleWorkspaceRefreshEvent);
      window.removeEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleWorkspaceRefreshEvent);
    });
  }

  createEffect(() => {
    setDraftWorkingDir(props.workingDir || "");
  });

  let lastWorkspaceRefreshKey = "";
  createEffect(() => {
    const ws = props.workspace;
    const root = typeof ws?.metadata?.root === "string" ? ws.metadata.root.trim() : "";
    const key = `${props.threadId}|${props.workingDir}|${ws?.backend || ""}|${ws?.locator || ""}|${root}`;
    if (key === lastWorkspaceRefreshKey) {
      return;
    }
    lastWorkspaceRefreshKey = key;
    // Workspace identity changed (new thread, switched project) — hard reset
    // so stale entries from the previous workspace don't linger.
    if (!key || key.startsWith("|")) {
      // Initial mount or empty workspace: keep hard reset to clear stale state
      // but allow the first load to happen. Use hard path for correctness.
      hardRefresh();
      return;
    }
    hardRefresh();
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
                <Show when={isOpen()} fallback={<ChevronRight size={14} />}>
                  <ChevronDown size={14} />
                </Show>
              </Show>
            </span>
            <span class="workspace-tree-icon">
              <Show when={isDirectory()} fallback={<File size={15} />}>
                <Folder size={15} />
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
              <MoreHorizontal size={14} />
            </button>
            <Show when={isMenuOpen()}>
              <div class="workspace-tree-menu" role="menu">
                <Show when={isDirectory()}>
                  <button
                    class="workspace-tree-menu-item"
                    type="button"
                    role="menuitem"
                    onClick={(event) => requestCreate("file", entry().path, event)}
                  >
                    <FilePlus size={14} />
                    <span>New file</span>
                  </button>
                  <button
                    class="workspace-tree-menu-item"
                    type="button"
                    role="menuitem"
                    onClick={(event) => requestCreate("directory", entry().path, event)}
                  >
                    <FolderPlus size={14} />
                    <span>New folder</span>
                  </button>
                  <div class="workspace-menu-divider" />
                </Show>
                <button
                  class="workspace-tree-menu-item"
                  type="button"
                  role="menuitem"
                  onClick={(event) => requestRename(entry(), event)}
                >
                  <Pencil size={14} />
                  <span>Rename</span>
                </button>
                <button
                  class="workspace-tree-menu-item"
                  type="button"
                  role="menuitem"
                  onClick={(event) => void copyPath(entry(), event)}
                >
                  <Clipboard size={14} />
                  <span>Copy path</span>
                </button>
                <button
                  class="workspace-tree-menu-item workspace-tree-menu-danger"
                  type="button"
                  role="menuitem"
                  onClick={(event) => requestDelete(entry(), event)}
                >
                  <Trash2 size={14} />
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
        <button
          class={`workspace-icon-btn ${isRefreshing() ? "is-refreshing" : ""}`}
          type="button"
          onClick={refresh}
          disabled={!canQuery() || isRefreshing()}
          title={isRefreshing() ? "Refreshing workspace..." : "Refresh workspace"}
          aria-label="Refresh workspace"
        >
          <RefreshCw size={18} />
        </button>
        <Show when={props.onCollapse}>
          <button
            class="workspace-icon-btn workspace-collapse-btn workspace-explorer-close-btn"
            type="button"
            onClick={props.onCollapse}
            title="Close workspace explorer"
            aria-label="Close workspace explorer"
          >
            <X size={18} />
          </button>
        </Show>
      </div>

      <div class="workspace-tabs-bar">
        <div
          class="workspace-tabs"
          role="tablist"
          aria-label="Workspace views"
          onWheel={(event: WheelEvent) => {
            if (event.deltaY !== 0) {
              event.preventDefault();
              const container = event.currentTarget as HTMLElement;
              if (container) {
                container.scrollLeft += event.deltaY;
              }
            }
          }}
        >
          <For each={openTabs()}>
            {(tab) => {
              const isFiles = tab === "files";
              const isChanges = tab === "changes";
              const isFile = tab.startsWith("file:");
              const filePath = isFile ? fileTabPath(tab) : "";

              return (
                <div
                  class={`workspace-tab ${activeTab() === tab ? "active" : ""}`}
                  role="tab"
                  aria-selected={activeTab() === tab}
                  onClick={() => setActiveTab(tab)}
                  onAuxClick={(event) => {
                    if (event.button === 1) {
                      event.preventDefault();
                      closeTab(tab, event);
                    }
                  }}
                  title={isFile ? filePath : isFiles ? "Files Explorer" : "Git Changes"}
                >
                  <div class="workspace-tab-icon-slot">
                    <span class="workspace-tab-icon" aria-hidden="true">
                      <Show when={isFiles}>
                        <Folder size={14} />
                      </Show>
                      <Show when={isChanges}>
                        <GitCompare size={14} />
                      </Show>
                      <Show when={isFile}>
                        <File size={14} />
                      </Show>
                    </span>
                    <button
                      class="workspace-tab-close"
                      type="button"
                      title={`Close ${isFiles ? "Files" : isChanges ? "Changes" : fileName(filePath)}`}
                      aria-label={`Close ${isFiles ? "Files" : isChanges ? "Changes" : fileName(filePath)}`}
                      onClick={(event) => closeTab(tab, event)}
                    >
                      <X size={13} />
                    </button>
                  </div>

                  <span class="workspace-tab-label">
                    {isFiles ? "Files" : isChanges ? "Changes" : fileName(filePath)}
                  </span>

                  <Show when={isChanges && changes().length > 0}>
                    <span class="workspace-tab-count">{changes().length}</span>
                  </Show>
                </div>
              );
            }}
          </For>

          <div class="workspace-plus-control">
            <button
              ref={plusBtnRef}
              class={`workspace-tab-plus ${plusMenuOpen() ? "active" : ""}`}
              type="button"
              title="New tab / Add tool"
              aria-label="New tab"
              aria-haspopup="menu"
              aria-expanded={plusMenuOpen()}
              onClick={(event) => {
                event.stopPropagation();
                if (!plusMenuOpen()) {
                  updatePlusMenuPosition();
                  setPlusMenuOpen(true);
                } else {
                  setPlusMenuOpen(false);
                }
              }}
            >
              <Plus size={15} />
            </button>

            <Show when={plusMenuOpen()}>
              <Portal>
                <div
                  ref={plusMenuRef}
                  class="workspace-plus-menu"
                  style={{
                    position: "fixed",
                    top: `${plusMenuPos().top}px`,
                    left: `${plusMenuPos().left}px`,
                  }}
                  role="menu"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    class={`workspace-plus-menu-item ${openTabs().includes("files") ? "is-open" : ""}`}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      openTab("files");
                      setPlusMenuOpen(false);
                    }}
                  >
                    <Folder size={15} />
                    <span>Files Explorer</span>
                    <Show when={openTabs().includes("files")}>
                      <span class="workspace-menu-pill">Open</span>
                    </Show>
                  </button>
                  <button
                    class={`workspace-plus-menu-item ${openTabs().includes("changes") ? "is-open" : ""}`}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      openTab("changes");
                      setPlusMenuOpen(false);
                    }}
                  >
                    <GitCompare size={15} />
                    <span>Git Changes</span>
                    <Show when={changes().length > 0}>
                      <span class="workspace-tab-count">{changes().length}</span>
                    </Show>
                    <Show when={openTabs().includes("changes")}>
                      <span class="workspace-menu-pill">Open</span>
                    </Show>
                  </button>
                  <div class="workspace-menu-divider" />
                  <div
                    class="workspace-plus-menu-item is-disabled"
                    title="Terminal support coming soon"
                    role="menuitem"
                    aria-disabled="true"
                  >
                    <Terminal size={15} />
                    <span>Terminal</span>
                    <span class="workspace-menu-pill badge-soon">Soon</span>
                  </div>
                </div>
              </Portal>
            </Show>
          </div>
        </div>
      </div>

      <div class="workspace-explorer-body">
        <Show when={openTabs().length === 0}>
          <div class="workspace-empty-view">
            <div class="workspace-empty-icon">
              <Folder size={32} />
            </div>
            <div class="workspace-empty-title">No open tabs</div>
            <div class="workspace-empty-desc">
              Open the file explorer or view git changes to get started.
            </div>
            <div class="workspace-empty-actions">
              <button
                class="btn btn-sm btn-primary"
                type="button"
                onClick={() => openTab("files")}
              >
                <Folder size={14} />
                <span>Files Explorer</span>
              </button>
              <button
                class="btn btn-sm"
                type="button"
                onClick={() => openTab("changes")}
              >
                <GitCompare size={14} />
                <span>Git Changes</span>
                <Show when={changes().length > 0}>
                  <span class="workspace-tab-count">{changes().length}</span>
                </Show>
              </button>
            </div>
          </div>
        </Show>
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
                                fallback={<ChevronRight size={14} />}
                              >
                                <ChevronDown size={14} />
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
            <div class="workspace-tree-toolbar">
              <span class="workspace-tree-toolbar-title">Files</span>
              <div class="workspace-tree-toolbar-actions">
                <button
                  class="workspace-icon-btn workspace-tree-toolbar-btn"
                  type="button"
                  title="New file in workspace root"
                  aria-label="New file"
                  onClick={(event) => requestCreate("file", "", event)}
                >
                  <FilePlus size={15} />
                </button>
                <button
                  class="workspace-icon-btn workspace-tree-toolbar-btn"
                  type="button"
                  title="New folder in workspace root"
                  aria-label="New folder"
                  onClick={(event) => requestCreate("directory", "", event)}
                >
                  <FolderPlus size={15} />
                </button>
              </div>
            </div>
            <Show when={!treeError()} fallback={<div class="empty compact">{treeError()}</div>}>
              <Show
                when={treeLoadingByPath()[rootPath()] || treeLoadingByPath()[""]}
                fallback={
                  <Show
                    when={rootEntries().length > 0}
                    fallback={
                      <div class="empty compact">
                        <span>No files in workspace.</span>
                        <div class="workspace-empty-tree-actions">
                          <button
                            class="btn btn-sm btn-primary"
                            type="button"
                            onClick={(event) => requestCreate("file", "", event)}
                          >
                            <FilePlus size={14} />
                            <span>New file</span>
                          </button>
                          <button
                            class="btn btn-sm"
                            type="button"
                            onClick={(event) => requestCreate("directory", "", event)}
                          >
                            <FolderPlus size={14} />
                            <span>New folder</span>
                          </button>
                        </div>
                      </div>
                    }
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
                }
              >
                <Show
                  when={rootEntries().length > 0}
                  fallback={<div class="empty compact">Loading files...</div>}
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
        size="sm"
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
        size="sm"
        icon={<Trash2 size={17} />}
        iconVariant="danger"
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
              <Trash2 size={15} />
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

      <Dialog
        open={createTarget() !== null}
        size="sm"
        title={createTarget()?.kind === "directory" ? "New folder" : "New file"}
        onClose={() => {
          if (!creating()) {
            cancelCreate();
          }
        }}
        footer={
          <div class="row-wrap">
            <button
              class="btn"
              type="button"
              onClick={cancelCreate}
              disabled={creating()}
            >
              Cancel
            </button>
            <button
              class="btn btn-primary"
              type="button"
              onClick={() => void confirmCreate()}
              disabled={creating() || !createName().trim()}
            >
              {creating()
                ? "Creating..."
                : createTarget()?.kind === "directory"
                ? "Create folder"
                : "Create file"}
            </button>
          </div>
        }
      >
        <p class="muted" style="margin-bottom: 8px;">
          Location: <span class="mono">/{createTarget()?.parentPath ? `${createTarget()?.parentPath}/` : ""}</span>
        </p>
        <input
          class="input"
          value={createName()}
          placeholder={
            createTarget()?.kind === "directory"
              ? "Folder name"
              : "File name (e.g. main.py, index.ts)"
          }
          disabled={creating()}
          autofocus
          onInput={(event) => setCreateName(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void confirmCreate();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              cancelCreate();
            }
          }}
        />
      </Dialog>
    </aside>
  );
}
