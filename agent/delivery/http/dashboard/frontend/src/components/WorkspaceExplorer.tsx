import {
  ChevronDown,
  ChevronRight,
  File,
  Folder,
  GitCompare,
  RefreshCw,
} from "lucide-solid";
import { createEffect, createSignal, For, Show } from "solid-js";

import { apiFetch } from "@/lib/api";

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

function diffLineClass(line: string): string {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return "workspace-diff-line added";
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return "workspace-diff-line removed";
  }
  if (line.startsWith("@@")) {
    return "workspace-diff-line hunk";
  }
  return "workspace-diff-line";
}

function statusLabel(status: string): string {
  if (!status) {
    return "diff";
  }
  return status.replace("_", " ");
}

export function WorkspaceExplorer(props: {
  threadId: string;
  workingDir: string;
  disabled?: boolean;
  onWorkingDirChange: (workingDir: string) => void;
}) {
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
  const [selectedPath, setSelectedPath] = createSignal("");
  const [diffPayload, setDiffPayload] = createSignal<WorkspaceDiffPayload | null>(null);
  const [diffLoading, setDiffLoading] = createSignal(false);
  const [diffError, setDiffError] = createSignal("");
  let generation = 0;

  const rootEntries = () => entriesByPath()[""] || [];
  const canQuery = () => Boolean(props.workingDir.trim() || props.threadId);

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
    setSelectedPath(path);
    setDiffPayload(null);
    setDiffError("");
    setDiffLoading(true);
    try {
      const query = workspaceQuery(props.threadId, props.workingDir, { path });
      const payload = await apiFetch<WorkspaceDiffPayload>(
        `/dashboard-api/workspace/diff?${query}`,
      );
      if (targetGeneration === generation && selectedPath() === path) {
        setDiffPayload(payload);
      }
    } catch (err) {
      if (targetGeneration === generation && selectedPath() === path) {
        setDiffError(err instanceof Error ? err.message : "Failed to load diff");
      }
    } finally {
      if (targetGeneration === generation && selectedPath() === path) {
        setDiffLoading(false);
      }
    }
  };

  const refresh = () => {
    generation += 1;
    const targetGeneration = generation;
    setEntriesByPath({});
    setExpandedByPath({ "": true });
    setTreeTruncatedByPath({});
    setSelectedPath("");
    setDiffPayload(null);
    setDiffError("");
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
    const isSelected = () => selectedPath() === entry().path;

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
              void loadDiff(entry().path);
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

      <div class="workspace-explorer-body">
        <section class="workspace-section">
          <div class="workspace-section-title">Changed files</div>
          <Show when={!changesLoading()} fallback={<div class="empty compact">Loading changes...</div>}>
            <Show when={!changesError()} fallback={<div class="empty compact">{changesError()}</div>}>
              <Show
                when={changes().length > 0}
                fallback={<div class="empty compact">{gitMessage() || "No changed files."}</div>}
              >
                <div class="workspace-change-list">
                  <For each={changes()}>
                    {(change) => (
                      <button
                        class={`workspace-change-row ${selectedPath() === change.path ? "active" : ""}`}
                        type="button"
                        title={change.path}
                        onClick={() => void loadDiff(change.path)}
                      >
                        <span class={`workspace-change-status ${change.status}`}>
                          {statusLabel(change.status)}
                        </span>
                        <span class="workspace-change-path">{change.path}</span>
                      </button>
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

        <section class="workspace-section workspace-tree-section">
          <div class="workspace-section-title">Files</div>
          <Show when={!treeError()} fallback={<div class="empty compact">{treeError()}</div>}>
            <Show when={rootEntries().length > 0} fallback={<div class="empty compact">No files.</div>}>
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

        <section class="workspace-section workspace-diff-section">
          <div class="workspace-section-title">Diff</div>
          <Show
            when={selectedPath()}
            fallback={<div class="empty compact">Select a changed file to view diff.</div>}
          >
            <div class="workspace-diff-path" title={selectedPath()}>{selectedPath()}</div>
            <Show when={!diffLoading()} fallback={<div class="empty compact">Loading diff...</div>}>
              <Show when={!diffError()} fallback={<div class="empty compact">{diffError()}</div>}>
                <Show
                  when={diffPayload()?.diff}
                  fallback={<div class="empty compact">{diffPayload()?.message || "No diff available."}</div>}
                >
                  <pre class="workspace-diff">
                    <For each={(diffPayload()?.diff || "").split("\n")}>
                      {(line) => <span class={diffLineClass(line)}>{line || " "}</span>}
                    </For>
                  </pre>
                  <Show when={diffPayload()?.truncated}>
                    <div class="hint workspace-hint">Diff truncated.</div>
                  </Show>
                </Show>
              </Show>
            </Show>
          </Show>
        </section>
      </div>
    </aside>
  );
}
