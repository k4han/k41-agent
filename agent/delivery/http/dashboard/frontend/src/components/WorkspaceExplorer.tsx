import {
  ChevronDown,
  ChevronRight,
  File,
  Folder,
  GitCompare,
  RefreshCw,
  X,
} from "lucide-solid";
import { createEffect, createMemo, createResource, createSignal, For, Show, untrack } from "solid-js";

import { apiFetch } from "@/lib/api";
import { highlightCode, languageFromPath } from "@/lib/codeHighlight";
import { renderUnifiedDiffHtml } from "@/lib/diffView";
import { createDarkMode } from "@/lib/theme";

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

type WorkspaceTab = "changes" | "files" | `file:${string}`;

function workspaceQuery(threadId: string, workingDir: string, extra?: Record<string, string>) {
  const params = new URLSearchParams();
  if (threadId) {
    params.set("thread_id", threadId);
  }
  if (workingDir.trim()) {
    params.set("working_dir", workingDir.trim());
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

export function WorkspaceExplorer(props: {
  threadId: string;
  workingDir: string;
  disabled?: boolean;
  onWorkingDirChange: (workingDir: string) => void;
}) {
  const dark = createDarkMode();
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
  const [activeTab, setActiveTab] = createSignal<WorkspaceTab>("changes");
  const [fileTabs, setFileTabs] = createSignal<string[]>([]);
  const [filePayloads, setFilePayloads] = createSignal<Record<string, WorkspaceFilePayload>>({});
  const [fileLoadingByPath, setFileLoadingByPath] = createSignal<Record<string, boolean>>({});
  const [fileErrorByPath, setFileErrorByPath] = createSignal<Record<string, string>>({});
  let generation = 0;

  const rootEntries = () => entriesByPath()[""] || [];
  const canQuery = () => Boolean(props.workingDir.trim() || props.threadId);
  const activeFilePath = () => fileTabPath(activeTab());
  const activeFilePayload = () => filePayloads()[activeFilePath()];

  const loadTree = async (path = "", targetGeneration = generation) => {
    if (!canQuery()) {
      return;
    }
    setTreeLoadingByPath((current) => ({ ...current, [path]: true }));
    if (!path) {
      setTreeError("");
    }
    try {
      const query = workspaceQuery(props.threadId, props.workingDir, { path });
      const payload = await apiFetch<WorkspaceTreePayload>(
        `/dashboard-api/workspace/tree?${query}`,
      );
      if (targetGeneration !== generation) {
        return;
      }
      setEntriesByPath((current) => ({ ...current, [payload.path]: payload.entries }));
      setTreeTruncatedByPath((current) => ({ ...current, [payload.path]: payload.truncated }));
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

  const loadChanges = async (targetGeneration = generation) => {
    if (!canQuery()) {
      return;
    }
    setChangesLoading(true);
    setChangesError("");
    try {
      const query = workspaceQuery(props.threadId, props.workingDir);
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
      const query = workspaceQuery(props.threadId, props.workingDir, { path });
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
      const query = workspaceQuery(props.threadId, props.workingDir, { path });
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
    setEntriesByPath({});
    setExpandedByPath({ "": true });
    setTreeTruncatedByPath({});
    setExpandedChangePath("");
    setDiffPayload(null);
    setDiffError("");
    setFileTabs([]);
    setFilePayloads({});
    setFileLoadingByPath({});
    setFileErrorByPath({});
    if (untrack(activeTab).startsWith("file:")) {
      setActiveTab("changes");
    }
    void loadTree("", targetGeneration);
    void loadChanges(targetGeneration);
  };

  const applyWorkingDir = () => {
    props.onWorkingDirChange(draftWorkingDir().trim());
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

  createEffect(() => {
    setDraftWorkingDir(props.workingDir || "");
  });

  createEffect(() => {
    props.threadId;
    props.workingDir;
    refresh();
  });

  const TreeEntry = (entryProps: { entry: WorkspaceTreeEntry; depth: number }) => {
    const entry = () => entryProps.entry;
    const children = () => entriesByPath()[entry().path] || [];
    const isOpen = () => Boolean(expandedByPath()[entry().path]);
    const isDirectory = () => entry().kind === "directory";
    const isSelected = () => activeTab() === fileTabId(entry().path);

    return (
      <>
        <button
          class={`workspace-tree-row ${isSelected() ? "active" : ""}`}
          type="button"
          style={`--depth: ${entryProps.depth};`}
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
    <aside class="panel workspace-explorer">
      <div class="workspace-explorer-header">
        <div class="workspace-explorer-title">
          <GitCompare size={14} />
          <span>Workspace Explorer</span>
        </div>
        <button
          class="btn btn-icon"
          type="button"
          onClick={refresh}
          title="Refresh workspace"
          aria-label="Refresh workspace"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      <div class="workspace-dir-control">
        <input
          class="input workspace-dir-input"
          value={draftWorkingDir()}
          disabled={props.disabled}
          placeholder="Working directory"
          onInput={(event) => setDraftWorkingDir(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              applyWorkingDir();
            }
          }}
        />
        <button class="btn btn-sm" type="button" onClick={applyWorkingDir} disabled={props.disabled}>
          Apply
        </button>
      </div>

      <div class="workspace-tabs" role="tablist" aria-label="Workspace views">
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
            <div class="workspace-section-title">Changed files</div>
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
            <div class="workspace-section-title">Files</div>
            <Show when={!treeError()} fallback={<div class="empty compact">{treeError()}</div>}>
              <Show
                when={rootEntries().length > 0}
                fallback={<div class="empty compact">No files.</div>}
              >
                <div class="workspace-tree">
                  <For each={rootEntries()}>
                    {(entry) => <TreeEntry entry={entry} depth={0} />}
                  </For>
                  <Show when={treeTruncatedByPath()[""]}>
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
            <div class="workspace-section-title" title={activeFilePath()}>
              {activeFilePath()}
            </div>
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
    </aside>
  );
}
