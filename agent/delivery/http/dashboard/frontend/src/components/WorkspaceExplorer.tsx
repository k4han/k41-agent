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
import { batch, createEffect, createMemo, createResource, createSelector, createSignal, For, on, onCleanup, Show, Suspense, untrack } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
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

const WORKSPACE_POLL_INTERVAL_MS = 15_000;
// Fallback polling during active tool runs (10-15s).
const WORKSPACE_RUNNING_POLL_INTERVAL_MS = 10_000;
const WORKSPACE_REFRESH_DELAY_MS = 300;

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
  const [cachedHtml, setCachedHtml] = createSignal("");
  const [highlighted] = createResource(
    () => ({ content: props.content, path: props.path, dark: props.dark }),
    async ({ content, path, dark }) => {
      const lang = languageFromPath(path);
      const html = await highlightCode(content, lang, dark);
      setCachedHtml(html);
      return html;
    },
  );
  const displayHtml = () => highlighted() || cachedHtml();

  return (
    <div class="workspace-file-view">
      {/* Keep highlighting loads from suspending the page and resetting chat scroll. */}
      <Suspense fallback={<pre class="workspace-file-view-plain">{props.content}</pre>}>
        <Show
          when={displayHtml()}
          fallback={<pre class="workspace-file-view-plain">{props.content}</pre>}
        >
          <div class="workspace-file-view-shiki" innerHTML={displayHtml()} />
        </Show>
      </Suspense>
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
  visible?: boolean;
  running?: boolean;
  onWorkingDirChange: (value: WorkspaceRef | string | null) => void;
  onCollapse?: () => void;
}) {
  const dark = createDarkMode();
  const { showToast } = useToast();
  const [draftWorkingDir, setDraftWorkingDir] = createSignal(props.workingDir);
  const [entriesByPath, setEntriesByPath] = createStore<Record<string, WorkspaceTreeEntry[]>>({});
  const [expandedByPath, setExpandedByPath] = createStore<Record<string, boolean>>({ "": true });
  const [treeLoadingByPath, setTreeLoadingByPath] = createStore<Record<string, boolean>>({});
  const [treeError, setTreeError] = createSignal("");
  const [treeTruncatedByPath, setTreeTruncatedByPath] = createStore<Record<string, boolean>>({});
  const [changeState, setChangeState] = createStore<{ items: WorkspaceChange[] }>({ items: [] });
  const changes = () => changeState.items;
  const [changesLoading, setChangesLoading] = createSignal(false);
  const [changesLoaded, setChangesLoaded] = createSignal(false);
  const [manualRefreshing, setManualRefreshing] = createSignal(false);
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
  const [filePayloads, setFilePayloads] = createStore<Partial<Record<string, WorkspaceFilePayload>>>({});
  const [fileLoadingByPath, setFileLoadingByPath] = createStore<Record<string, boolean>>({});
  const [fileErrorByPath, setFileErrorByPath] = createStore<Record<string, string>>({});
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
  let requestController = new AbortController();
  const treeRequestIds = new Map<string, number>();
  const fileRequestIds = new Map<string, number>();
  // Retain inactive previews until a workspace refresh invalidates them.
  const fileVersions = new Map<string, number>();
  let workspaceVersion = 0;
  let diffVersion = -1;
  let lastRefreshAt = 0;
  let changesRequestId = 0;
  let diffRequestId = 0;
  let disposed = false;
  let refreshTimer: number | undefined;
  let pollTimer: number | undefined;
  let refreshRunning = false;
  let refreshPending = false;
  const [documentVisible, setDocumentVisible] = createSignal(
    typeof document !== "undefined" && document.visibilityState !== "hidden",
  );
  const canAutoRefresh = () => props.visible !== false && documentVisible();
  const pollInterval = () => props.running ? WORKSPACE_RUNNING_POLL_INTERVAL_MS : WORKSPACE_POLL_INTERVAL_MS;
  const isActiveTab = createSelector(activeTab);
  const isActionMenuOpen = createSelector(actionMenuPath);
  const isChangeExpanded = createSelector(expandedChangePath);

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
  const rootEntries = () => entriesByPath[rootPath()] || entriesByPath[""] || [];
  const rootLoaded = () => entriesByPath[rootPath()] !== undefined || entriesByPath[""] !== undefined;
  const rootTreeTruncated = () =>
    treeTruncatedByPath[rootPath()] || treeTruncatedByPath[""] || false;
  const canQuery = () => Boolean(queryWorkspace()?.locator.trim() || props.threadId);
  const isRefreshing = () =>
    Boolean(
      treeLoadingByPath[rootPath()]
      || treeLoadingByPath[""]
      || changesLoading(),
    );
  const activeFilePath = () => fileTabPath(activeTab());
  const activeFilePayload = () => filePayloads[activeFilePath()];
  const workingDirDisplayValue = () =>
    isLocalWorkspace()
      ? props.disabled ? formatWorkspaceRoot(draftWorkingDir()) : draftWorkingDir()
      : workspaceDisplayLabel(effectiveWorkspace()) || effectiveWorkspace()?.locator || "";

  const loadTree = async (path = "", targetGeneration = generation) => {
    if (disposed || !canQuery()) {
      return;
    }
    const requestId = (treeRequestIds.get(path) || 0) + 1;
    treeRequestIds.set(path, requestId);
    const isCurrent = () => targetGeneration === generation && treeRequestIds.get(path) === requestId;
    setTreeLoadingByPath(path, true);
    if (!path) {
      setTreeError("");
    }
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceTreePayload>(
        `/dashboard-api/workspace/tree?${query}`,
        { signal: requestController.signal },
      );
      if (!isCurrent()) {
        return;
      }
      batch(() => {
        setEntriesByPath(payload.path, reconcile(payload.entries, { key: "path" }));
        setTreeTruncatedByPath(payload.path, payload.truncated);
        if (payload.root) setWorkspaceRoot(payload.root);
      });
    } catch (err) {
      if (isCurrent()) {
        setTreeError(err instanceof Error ? err.message : "Failed to load workspace tree");
      }
    } finally {
      if (isCurrent()) {
        setTreeLoadingByPath(path, false);
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
    if (disposed || !canQuery()) {
      return;
    }
    const requestId = ++changesRequestId;
    const isCurrent = () => targetGeneration === generation && requestId === changesRequestId;
    setChangesLoading(true);
    setChangesError("");
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace());
      const payload = await apiFetch<WorkspaceChangesPayload>(
        `/dashboard-api/workspace/changes?${query}`,
        { signal: requestController.signal },
      );
      if (!isCurrent()) {
        return;
      }
      batch(() => {
        setChangeState("items", reconcile(payload.changes || [], { key: "path" }));
        setGitMessage(payload.message || "");
        setIsGitRepo(payload.is_git_repo);
        setChangesLoaded(true);
      });
    } catch (err) {
      if (isCurrent()) {
        setChangesError(err instanceof Error ? err.message : "Failed to load changes");
      }
    } finally {
      if (isCurrent()) {
        setChangesLoading(false);
      }
    }
  };

  const loadDiff = async (path: string, targetGeneration = generation) => {
    if (disposed || !path || !canQuery()) {
      return;
    }
    if (expandedChangePath() === path && (
      diffLoading() || (diffPayload()?.path === path && diffVersion === workspaceVersion)
    )) return;
    const targetVersion = workspaceVersion;
    const requestId = ++diffRequestId;
    const isCurrent = () => targetGeneration === generation && requestId === diffRequestId;
    batch(() => {
      setExpandedChangePath(path);
      if (diffPayload()?.path !== path) setDiffPayload(null);
      setDiffError("");
      setDiffLoading(true);
    });
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceDiffPayload>(
        `/dashboard-api/workspace/diff?${query}`,
        { signal: requestController.signal },
      );
      if (isCurrent() && expandedChangePath() === path) {
        diffVersion = targetVersion;
        const current = diffPayload();
        if (
          !current
          || current.diff !== payload.diff
          || current.status !== payload.status
          || current.truncated !== payload.truncated
          || current.message !== payload.message
        ) {
          setDiffPayload(payload);
        }
      }
    } catch (err) {
      if (isCurrent() && expandedChangePath() === path) {
        setDiffError(err instanceof Error ? err.message : "Failed to load diff");
      }
    } finally {
      if (isCurrent() && expandedChangePath() === path) {
        setDiffLoading(false);
        if (targetVersion !== workspaceVersion) scheduleRefresh();
      }
    }
  };

  const loadFile = async (path: string, targetGeneration = generation) => {
    if (disposed || !path || !canQuery()) {
      return;
    }
    if (fileLoadingByPath[path] || (filePayloads[path] && fileVersions.get(path) === workspaceVersion)) return;
    const targetVersion = workspaceVersion;
    const requestId = (fileRequestIds.get(path) || 0) + 1;
    fileRequestIds.set(path, requestId);
    const isCurrent = () => targetGeneration === generation && fileRequestIds.get(path) === requestId;
    setFileLoadingByPath(path, true);
    setFileErrorByPath(path, "");
    try {
      const query = workspaceQuery(props.threadId, queryWorkspace(), { path });
      const payload = await apiFetch<WorkspaceFilePayload>(
        `/dashboard-api/workspace/file?${query}`,
        { signal: requestController.signal },
      );
      if (!isCurrent()) {
        return;
      }
      fileVersions.set(path, targetVersion);
      const current = filePayloads[path];
      if (
        !current
        || current.content !== payload.content
        || current.size !== payload.size
        || current.truncated !== payload.truncated
        || current.binary !== payload.binary
        || current.message !== payload.message
      ) {
        setFilePayloads(path, reconcile(payload));
      }
    } catch (err) {
      if (isCurrent()) {
        setFileErrorByPath(path, err instanceof Error ? err.message : "Failed to load file");
      }
    } finally {
      if (isCurrent()) {
        setFileLoadingByPath(path, false);
        if (activeFilePath() === path && targetVersion !== workspaceVersion) scheduleRefresh();
      }
    }
  };

  const hardRefresh = () => {
    generation += 1;
    const targetGeneration = generation;
    requestController.abort();
    requestController = new AbortController();
    treeRequestIds.clear();
    fileRequestIds.clear();
    fileVersions.clear();
    workspaceVersion += 1;
    diffVersion = -1;
    lastRefreshAt = 0;
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
    refreshRunning = false;
    refreshPending = false;
    batch(() => {
      setEntriesByPath(reconcile({}));
      setExpandedByPath(reconcile({ "": true }));
      setTreeLoadingByPath(reconcile({}));
      setTreeTruncatedByPath(reconcile({}));
      setTreeError("");
      setWorkspaceRoot("");
      setChangeState("items", []);
      setChangesLoading(false);
      setChangesLoaded(false);
      setManualRefreshing(false);
      setChangesError("");
      setGitMessage("");
      setIsGitRepo(true);
      setExpandedChangePath("");
      setDiffPayload(null);
      setDiffLoading(false);
      setDiffError("");
      setOpenTabs(["files"]);
      setActiveTab("files");
      setActionMenuPath(null);
      setPlusMenuOpen(false);
      setFilePayloads(reconcile({}));
      setFileLoadingByPath(reconcile({}));
      setFileErrorByPath(reconcile({}));
      if (canAutoRefresh()) {
        void Promise.all([loadTree(), loadChanges()]).then(() => {
          if (!disposed && targetGeneration === generation) lastRefreshAt = Date.now();
        });
      }
    });
    schedulePoll();
  };

  const schedulePoll = () => {
    window.clearTimeout(pollTimer);
    pollTimer = undefined;
    if (disposed || !canQuery() || !canAutoRefresh()) return;
    pollTimer = window.setTimeout(() => {
      pollTimer = undefined;
      void softRefresh();
    }, pollInterval());
  };

  const scheduleRefresh = () => {
    if (disposed || !canQuery() || !canAutoRefresh()) return;
    if (refreshRunning) {
      refreshPending = true;
      return;
    }
    // Keep a bounded delay even when tool results arrive continuously.
    if (refreshTimer !== undefined) return;
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined;
      void softRefresh();
    }, WORKSPACE_REFRESH_DELAY_MS);
  };

  const softRefresh = async () => {
    if (!canQuery() || disposed || !canAutoRefresh()) return;
    if (refreshRunning) {
      refreshPending = true;
      return;
    }
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
    window.clearTimeout(pollTimer);
    pollTimer = undefined;
    refreshRunning = true;
    workspaceVersion += 1;
    const targetGeneration = generation;
    const paths = untrack(() => Object.keys(expandedByPath).filter((path) => path && expandedByPath[path]));
    // Expansion remains user-owned, including changes made during the request.
    const reloadWithExpansion = async () => {
      await loadTree("", targetGeneration);
      for (const path of paths) {
        if (disposed || targetGeneration !== generation || !canAutoRefresh()) return;
        if (expandedByPath[path]) await loadTree(path, targetGeneration);
      }
    };
    const filePath = activeFilePath();
    const diffPath = activeTab() === "changes" ? expandedChangePath() : "";
    try {
      await Promise.all([
        reloadWithExpansion(),
        loadChanges(targetGeneration),
        filePath ? loadFile(filePath, targetGeneration) : undefined,
        diffPath ? loadDiff(diffPath, targetGeneration) : undefined,
      ]);
    } finally {
      if (!disposed && targetGeneration === generation) {
        refreshRunning = false;
        lastRefreshAt = Date.now();
        if (refreshPending) {
          refreshPending = false;
          scheduleRefresh();
        }
        schedulePoll();
      }
    }
  };

  const refresh = async () => {
    if (refreshRunning) {
      refreshPending = true;
      return;
    }
    const targetGeneration = generation;
    setManualRefreshing(true);
    try {
      await softRefresh();
    } finally {
      if (targetGeneration === generation) setManualRefreshing(false);
    }
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
      const targetGeneration = generation;
      queueMicrotask(() => {
        if (!disposed && targetGeneration === generation) hardRefresh();
      });
      showToast(`${ws.backend} workspace reconnected.`, "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : `Failed to reconnect ${ws.backend} workspace`, "error");
    } finally {
      setReconnectingModal(false);
    }
  };

  const toggleDirectory = (path: string) => {
    const isOpen = Boolean(expandedByPath[path]);
    setExpandedByPath(path, !isOpen);
    if (isOpen || entriesByPath[path] || treeLoadingByPath[path]) {
      return;
    }
    void loadTree(path);
  };

  const toggleChangeDiff = (path: string) => {
    if (expandedChangePath() === path) {
      diffRequestId += 1;
      diffVersion = -1;
      setExpandedChangePath("");
      setDiffLoading(false);
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
  };

  const openFile = (path: string) => {
    const tab = fileTabId(path);
    openTab(tab);
  };

  createEffect(on(activeTab, (tab) => {
    const path = fileTabPath(tab);
    if (path) void loadFile(path);
    if (tab === "changes" && expandedChangePath()) void loadDiff(expandedChangePath());
  }, { defer: true }));

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
      setFilePayloads(openedPath, undefined);
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
      setFilePayloads(removedPath, undefined);
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
        setExpandedByPath(target.parentPath, true);
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

  // Tool completion is the primary signal; polling covers external writes.
  if (typeof window !== "undefined") {
    const handleWorkspaceRefreshEvent = (event: Event) => {
      const custom = event as CustomEvent<{ threadId?: string }>;
      const tid = custom.detail?.threadId;
      // Only refresh the explorer that belongs to the affected thread (or
      // the global explorer when no thread filter is present).
      if (tid && tid !== props.threadId) {
        return;
      }
      workspaceVersion += 1;
      scheduleRefresh();
    };
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleWorkspaceRefreshEvent);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleWorkspaceRefreshEvent);
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_CHANGED, handleWorkspaceRefreshEvent);
    const handleVisibilityChange = () => setDocumentVisible(document.visibilityState !== "hidden");
    const handleFocus = () => {
      if (Date.now() - lastRefreshAt >= pollInterval()) scheduleRefresh();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleFocus);
    onCleanup(() => {
      window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleWorkspaceRefreshEvent);
      window.removeEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleWorkspaceRefreshEvent);
      window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_CHANGED, handleWorkspaceRefreshEvent);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleFocus);
    });
  }

  createEffect(on(canAutoRefresh, (visible) => {
    if (visible) {
      scheduleRefresh();
    } else {
      window.clearTimeout(refreshTimer);
      refreshTimer = undefined;
      window.clearTimeout(pollTimer);
      pollTimer = undefined;
    }
  }, { defer: true }));

  createEffect(on(() => props.running, () => schedulePoll(), { defer: true }));

  createEffect(() => {
    setDraftWorkingDir(props.workingDir || "");
  });

  const normalizeWorkspacePath = (p: string | null | undefined): string => {
    if (!p) return "";
    return p.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  };

  const workspaceRefreshKey = createMemo(() => {
    const ws = effectiveWorkspace();
    if (!ws) return "";
    const backend = ws.backend || "local";
    const locator = normalizeWorkspacePath(ws.locator);
    const root = typeof ws.metadata?.root === "string" ? normalizeWorkspacePath(ws.metadata.root) : "";
    return `${backend}:${locator}:${root}`;
  });
  createEffect(on(workspaceRefreshKey, hardRefresh));

  createEffect(on(() => props.threadId, (newTid, prevTid) => {
    // When switching threads within the same workspace, sync changes softly
    // without resetting expanded folders or closing open tabs.
    if (newTid && prevTid && newTid !== prevTid) {
      scheduleRefresh();
    }
  }, { defer: true }));

  onCleanup(() => {
    disposed = true;
    generation += 1;
    requestController.abort();
    window.clearTimeout(refreshTimer);
    window.clearTimeout(pollTimer);
  });

  const TreeEntry = (entryProps: { entry: WorkspaceTreeEntry; depth: number }) => {
    const entry = () => entryProps.entry;
    const children = () => entriesByPath[entry().path] || [];
    const isOpen = () => Boolean(expandedByPath[entry().path]);
    const isDirectory = () => entry().kind === "directory";
    const isSelected = () => isActiveTab(fileTabId(entry().path));
    const isMenuOpen = () => isActionMenuOpen(entry().path);

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
          <Show when={treeLoadingByPath[entry().path] && entriesByPath[entry().path] === undefined}>
            <div class="workspace-tree-status" style={`--depth: ${entryProps.depth + 1};`}>
              Loading...
            </div>
          </Show>
          <For each={children()}>
            {(child) => <TreeEntry entry={child} depth={entryProps.depth + 1} />}
          </For>
          <Show when={treeTruncatedByPath[entry().path]}>
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
          class={`workspace-icon-btn ${manualRefreshing() ? "is-refreshing" : ""}`}
          type="button"
          onClick={refresh}
          disabled={!canQuery() || manualRefreshing()}
          title={manualRefreshing() ? "Refreshing workspace..." : "Refresh workspace"}
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
                  class={`workspace-tab ${isActiveTab(tab) ? "active" : ""}`}
                  role="tab"
                  aria-selected={isActiveTab(tab)}
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
        <Show when={openTabs().includes("changes")}>
          <section class="workspace-section workspace-tab-panel" role="tabpanel" hidden={!isActiveTab("changes")}>
            <Show
              when={!changesLoading() || changesLoaded()}
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
                          class={`workspace-change-item ${isChangeExpanded(change.path) ? "expanded" : ""}`}
                        >
                          <button
                            class={`workspace-change-row ${isChangeExpanded(change.path) ? "active" : ""}`}
                            type="button"
                            title={change.path}
                            aria-expanded={isChangeExpanded(change.path)}
                            onClick={() => toggleChangeDiff(change.path)}
                          >
                            <span class="workspace-change-caret">
                              <Show
                                when={isChangeExpanded(change.path)}
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
                          <Show when={isChangeExpanded(change.path)}>
                            <div class="workspace-change-diff">
                              <Show
                                when={!diffLoading() || Boolean(diffPayload())}
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
            <Show when={!isGitRepo()}>
              <div class="hint workspace-hint">Diff requires a Git workspace.</div>
            </Show>
          </section>
        </Show>

        <Show when={openTabs().includes("files")}>
          <section class="workspace-section workspace-tree-section workspace-tab-panel" role="tabpanel" hidden={!isActiveTab("files")}>
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
                when={rootEntries().length > 0}
                fallback={
                  <Show
                    when={rootLoaded() || (!treeLoadingByPath[rootPath()] && !treeLoadingByPath[""])}
                    fallback={<div class="empty compact">Loading files...</div>}
                  >
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
                  </Show>
                }
              >
                <div class="workspace-tree" aria-busy={isRefreshing()}>
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
              when={!fileLoadingByPath[activeFilePath()] || Boolean(activeFilePayload())}
              fallback={<div class="empty compact">Loading file...</div>}
            >
              <Show
                when={!fileErrorByPath[activeFilePath()]}
                fallback={<div class="empty compact">{fileErrorByPath[activeFilePath()]}</div>}
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
