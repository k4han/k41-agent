import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  FolderOpen,
  GitBranch,
  CheckCircle2,
  ArrowUp,
  HardDrive,
  Plus,
  ChevronRight,
  RefreshCw,
  Cloud,
  Server,
  Cpu,
} from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import {
  formatWorkspaceRoot,
  isGitHubWorkspace,
  workspaceDisplayLabel,
  workspaceDisplayLabelFromValues,
} from "@/lib/workspace";
import type {
  GitHubPayload,
  GitHubRepositoryBinding,
  WorkspaceRef,
} from "@/types";

type WorkspaceBrowseEntry = {
  name: string;
  path: string;
};

type WorkspaceBrowsePayload = {
  path: string;
  parent: string;
  entries: WorkspaceBrowseEntry[];
  roots: WorkspaceBrowseEntry[];
  truncated: boolean;
};

export type WorkspaceBackendKey = "local" | "daytona" | "modal";
export type WorkspaceSourceKey = "path" | "sandbox" | "github";

export type WorkspaceSelectionDraft = {
  backend: WorkspaceBackendKey;
  source: WorkspaceSourceKey;
  localPath: string;
  daytonaSandboxId: string;
  modalSandboxId: string;
  repositoryId: number | null;
  repositoryFullName: string;
  label: string;
};

export interface WorkspaceSelectorProps {
  workingDir: string;
  defaultWorkingDir: string;
  workspace?: WorkspaceRef | null;
  selection?: WorkspaceSelectionDraft | null;
  locked: boolean;
  disabled?: boolean;
  onSelectionChange: (selection: WorkspaceSelectionDraft) => void;
}

function sourceForBackend(backend: WorkspaceBackendKey): WorkspaceSourceKey[] {
  if (backend === "local") {
    return ["path", "github"];
  }
  return ["sandbox", "github"];
}

function defaultSourceForBackend(backend: WorkspaceBackendKey): WorkspaceSourceKey {
  if (backend === "local") {
    return "path";
  }
  return "sandbox";
}

function backendFromWorkspace(workspace: WorkspaceRef | null | undefined): WorkspaceBackendKey {
  if (workspace?.backend === "daytona" || workspace?.backend === "modal") {
    return workspace.backend;
  }
  return "local";
}

function sourceFromWorkspace(
  backend: WorkspaceBackendKey,
  workspace: WorkspaceRef | null | undefined,
): WorkspaceSourceKey {
  if (isGitHubWorkspace(workspace)) {
    return "github";
  }
  return defaultSourceForBackend(backend);
}

export function WorkspaceSelector(props: WorkspaceSelectorProps) {
  const { showToast } = useToast();
  const [backend, setBackend] = createSignal<WorkspaceBackendKey>("local");
  const [source, setSource] = createSignal<WorkspaceSourceKey>("path");
  const [localDraft, setLocalDraft] = createSignal(props.defaultWorkingDir);
  const [daytonaSandboxId, setDaytonaSandboxId] = createSignal("");
  const [modalSandboxId, setModalSandboxId] = createSignal("");
  const [repositories, setRepositories] = createSignal<GitHubRepositoryBinding[]>([]);
  const [repositoryId, setRepositoryId] = createSignal("");
  const [repositoriesLoading, setRepositoriesLoading] = createSignal(false);
  const [repositoriesError, setRepositoriesError] = createSignal("");
  const [resolvedLabel, setResolvedLabel] = createSignal("");
  const [browserOpen, setBrowserOpen] = createSignal(false);
  const [browsePayload, setBrowsePayload] = createSignal<WorkspaceBrowsePayload | null>(null);
  const [browseLoading, setBrowseLoading] = createSignal(false);
  const [browseError, setBrowseError] = createSignal("");
  const [createFolderOpen, setCreateFolderOpen] = createSignal(false);
  const [newFolderName, setNewFolderName] = createSignal("");
  const [createFolderResolving, setCreateFolderResolving] = createSignal(false);

  const pathSegments = createMemo(() => {
    const currentPath = browsePayload()?.path || localDraft() || "";
    if (!currentPath) return [];

    const isWindows = currentPath.includes("\\") || Boolean(currentPath.match(/^[a-zA-Z]:/));
    const separator = isWindows ? "\\" : "/";
    const parts = currentPath.split(/[\\/]/).filter(Boolean);
    const segments: { name: string; path: string }[] = [];

    let accumulated = "";
    if (isWindows && currentPath.match(/^[a-zA-Z]:/)) {
      const drive = currentPath.split(/[\\/]/)[0];
      accumulated = drive + separator;
      segments.push({ name: drive, path: accumulated });

      for (let i = 1; i < parts.length; i++) {
        accumulated += parts[i] + separator;
        segments.push({ name: parts[i], path: accumulated });
      }
    } else {
      accumulated = "";
      for (let i = 0; i < parts.length; i++) {
        accumulated += "/" + parts[i];
        segments.push({ name: parts[i], path: accumulated });
      }
    }

    if (props.defaultWorkingDir) {
      const normalize = (p: string) => {
        let cleaned = p.replace(/[\\/]+/g, "/");
        if (cleaned.endsWith("/")) {
          cleaned = cleaned.slice(0, -1);
        }
        return isWindows ? cleaned.toLowerCase() : cleaned;
      };

      const rootPathNormalized = normalize(props.defaultWorkingDir);
      const filtered = segments.filter((seg) => {
        const segNormalized = normalize(seg.path);
        return segNormalized.startsWith(rootPathNormalized);
      });

      if (filtered.length > 0) {
        return filtered;
      }
    }

    return segments;
  });

  const filteredEntries = createMemo(() => {
    const entries = browsePayload()?.entries || [];
    return entries.filter(
      (entry) =>
        !entry.name.startsWith(".") &&
        entry.name !== "__pycache__" &&
        entry.name !== "node_modules" &&
        entry.name !== "venv"
    );
  });

  const repositoryOptions = createMemo(() =>
    repositories().map((repository) => ({
      value: String(repository.repository_id),
      label: repository.full_name,
    })),
  );

  const selectedRepository = createMemo(() =>
    repositories().find((repository) => String(repository.repository_id) === repositoryId()),
  );
  const workspaceStatusLabel = createMemo(() =>
    workspaceDisplayLabel(props.workspace)
    || props.selection?.label
    || workspaceDisplayLabelFromValues(resolvedLabel(), props.workingDir || resolvedLabel()),
  );
  const workspaceStatusTitle = createMemo(() =>
    props.workingDir || props.selection?.label || resolvedLabel() || workspaceStatusLabel(),
  );

  const resolveDisabled = createMemo(() => {
    if (props.disabled) {
      return true;
    }
    const src = source();
    if (src === "path") {
      return !localDraft().trim();
    }
    if (src === "sandbox") {
      if (backend() === "daytona") {
        return false;
      }
      if (backend() === "modal") {
        return false;
      }
      return true;
    }
    if (src === "github") {
      return !repositoryId();
    }
    return true;
  });

  const loadRepositories = async () => {
    setRepositoriesLoading(true);
    setRepositoriesError("");
    try {
      const payload = await apiFetch<GitHubPayload>("/dashboard-api/github");
      setRepositories(payload.repositories || []);
      if (!repositoryId() && payload.repositories.length) {
        setRepositoryId(String(payload.repositories[0].repository_id));
      }
    } catch (err) {
      setRepositoriesError(err instanceof Error ? err.message : "Failed to load repositories");
    } finally {
      setRepositoriesLoading(false);
    }
  };

  const loadBrowsePath = async (path?: string) => {
    setBrowseLoading(true);
    setBrowseError("");
    try {
      const query = path?.trim() ? `?path=${encodeURIComponent(path.trim())}` : "";
      const payload = await apiFetch<WorkspaceBrowsePayload>(
        `/dashboard-api/workspace/browse${query}`,
      );
      setBrowsePayload(payload);
      setLocalDraft(payload.path);
    } catch (err) {
      setBrowseError(err instanceof Error ? err.message : "Failed to browse directories");
    } finally {
      setBrowseLoading(false);
    }
  };

  const openBrowser = () => {
    setBrowserOpen(true);
    void loadBrowsePath(localDraft().trim() || props.defaultWorkingDir);
  };

  const closeBrowser = () => {
    setBrowserOpen(false);
    setBrowseError("");
  };

  const chooseCurrentBrowsePath = () => {
    const payload = browsePayload();
    if (payload?.path) {
      setLocalDraft(payload.path);
      closeBrowser();
      commitSelection(payload.path);
    }
  };

  const handleCreateFolderSubmit = async () => {
    const currentPath = browsePayload()?.path || localDraft();
    const folderName = newFolderName().trim();
    if (!currentPath || !folderName) {
      return;
    }
    setCreateFolderResolving(true);
    try {
      const response = await postJson<{ success: boolean; path: string; name: string }>(
        "/dashboard-api/workspace/create-dir",
        { parent_path: currentPath, name: folderName },
      );
      showToast(`Folder "${response.name}" created successfully.`, "success");
      setCreateFolderOpen(false);
      setNewFolderName("");
      void loadBrowsePath(currentPath);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create folder", "error");
    } finally {
      setCreateFolderResolving(false);
    }
  };

  const buildSelection = (targetPath: string): WorkspaceSelectionDraft => {
    const src = source();
    const back = backend();
    const repository = selectedRepository();
    const sandboxId = back === "daytona" ? daytonaSandboxId().trim() : modalSandboxId().trim();
    let label = "";
    if (src === "github") {
      label = repository?.full_name || "GitHub repository";
    } else if (src === "path") {
      label = workspaceDisplayLabelFromValues("", targetPath);
    } else if (sandboxId) {
      label = `${back}:${sandboxId}`;
    } else {
      label = `${back === "daytona" ? "Daytona" : "Modal"} sandbox (new)`;
    }

    return {
      backend: back,
      source: src,
      localPath: targetPath.trim(),
      daytonaSandboxId: daytonaSandboxId().trim(),
      modalSandboxId: modalSandboxId().trim(),
      repositoryId: repositoryId() ? Number(repositoryId()) : null,
      repositoryFullName: repository?.full_name || "",
      label,
    };
  };

  const commitSelection = (pathOverride?: string) => {
    const targetPath = pathOverride !== undefined ? pathOverride : localDraft();
    if (props.disabled) {
      return;
    }
    if (source() === "path" && !targetPath.trim()) {
      return;
    }
    if (source() === "github" && !repositoryId()) {
      return;
    }
    const selection = buildSelection(targetPath);
    setResolvedLabel(selection.label);
    props.onSelectionChange(selection);
    showToast("Workspace selected.", "success");
  };

  createEffect(() => {
    const workspace = props.workspace;
    if (workspace && workspace.backend !== "local") {
      // Sandbox roots (e.g. /workspace/repo) are not valid local paths and
      // must not leak into the path-source draft.
      return;
    }
    if (props.workingDir) {
      setLocalDraft(props.workingDir);
    }
  });

  createEffect(() => {
    const workspace = props.workspace;
    if (!workspace || props.selection) {
      return;
    }
    const back = backendFromWorkspace(workspace);
    setBackend(back);
    setSource(sourceFromWorkspace(back, workspace));
    if (workspace.backend === "daytona") {
      setDaytonaSandboxId(workspace.locator);
    } else if (workspace.backend === "modal") {
      setModalSandboxId(workspace.locator);
    }
  });

  createEffect(() => {
    const selection = props.selection;
    if (!selection) {
      return;
    }
    setBackend(selection.backend);
    setSource(selection.source);
    setLocalDraft(selection.localPath);
    setDaytonaSandboxId(selection.daytonaSandboxId);
    setModalSandboxId(selection.modalSandboxId);
    setRepositoryId(selection.repositoryId === null ? "" : String(selection.repositoryId));
    setResolvedLabel(selection.label);
  });

  createEffect(() => {
    if (!props.workingDir && props.defaultWorkingDir && !localDraft()) {
      setLocalDraft(props.defaultWorkingDir);
    }
  });

  createEffect(() => {
    const allowed = sourceForBackend(backend());
    if (!allowed.includes(source())) {
      setSource(defaultSourceForBackend(backend()));
    }
  });

  onMount(() => {
    void loadRepositories();
  });

  return (
    <div class={`workspace-selector ${props.locked ? "locked" : ""}`}>
      <div class="workspace-selector-status">
        <Show when={workspaceStatusLabel()} fallback={<FolderOpen size={14} />}>
          <CheckCircle2 size={14} />
        </Show>
        <span title={workspaceStatusTitle()}>
          {workspaceStatusLabel() || "Select a workspace to start"}
        </span>
      </div>

      <Show when={!props.locked}>
        <div class="workspace-selector-controls">
          <div class="workspace-selector-backends" role="tablist" aria-label="Workspace backend">
            <button
              class={`workspace-selector-mode ${backend() === "local" ? "active" : ""}`}
              type="button"
              disabled={props.disabled}
              onClick={() => setBackend("local")}
              aria-selected={backend() === "local"}
              role="tab"
            >
              <HardDrive size={14} />
              <span>Local</span>
            </button>
            <button
              class={`workspace-selector-mode ${backend() === "daytona" ? "active" : ""}`}
              type="button"
              disabled={props.disabled}
              onClick={() => setBackend("daytona")}
              aria-selected={backend() === "daytona"}
              role="tab"
            >
              <Server size={14} />
              <span>Daytona</span>
            </button>
            <button
              class={`workspace-selector-mode ${backend() === "modal" ? "active" : ""}`}
              type="button"
              disabled={props.disabled}
              onClick={() => setBackend("modal")}
              aria-selected={backend() === "modal"}
              role="tab"
            >
              <Cpu size={14} />
              <span>Modal</span>
            </button>
          </div>

          <div class="workspace-selector-sources" role="tablist" aria-label="Workspace source">
            <For each={sourceForBackend(backend())}>
              {(src) => (
                <button
                  class={`workspace-selector-source ${source() === src ? "active" : ""}`}
                  type="button"
                  disabled={props.disabled}
                  onClick={() => setSource(src)}
                  aria-selected={source() === src}
                  role="tab"
                >
                  <Show
                    when={src === "github"}
                    fallback={
                      <Show
                        when={src === "sandbox"}
                        fallback={<FolderOpen size={13} />}
                      >
                        <Cloud size={13} />
                      </Show>
                    }
                  >
                    <GitBranch size={13} />
                  </Show>
                  <span>
                    {src === "path"
                      ? "Local path"
                      : src === "sandbox"
                        ? backend() === "local"
                          ? "Local path"
                          : `${backend() === "daytona" ? "Daytona" : "Modal"} sandbox`
                        : "GitHub repo"}
                  </span>
                </button>
              )}
            </For>
          </div>

          <Show
            when={source() === "github"}
            fallback={
              <Show
                when={source() === "sandbox" && backend() === "modal"}
                fallback={
                  <Show
                    when={source() === "sandbox" && backend() === "daytona"}
                    fallback={
                      <div class="workspace-selector-row-enhanced">
                <div class="workspace-input-group">
                  <input
                    class="input workspace-selector-input"
                    value={formatWorkspaceRoot(localDraft())}
                    disabled={props.disabled}
                    placeholder="Working directory"
                    onInput={(event) => setLocalDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        commitSelection();
                      }
                    }}
                  />
                  <button
                    class="workspace-input-btn-browse"
                    type="button"
                    title="Browse folder"
                    disabled={props.disabled}
                    onClick={openBrowser}
                  >
                    <FolderOpen size={14} />
                  </button>
                </div>
                <button
                  class="btn btn-sm btn-primary workspace-use-btn"
                  type="button"
                  disabled={resolveDisabled()}
                  onClick={() => commitSelection()}
                >
                  <CheckCircle2 size={13} />
                  Use
                </button>
                <Show when={browserOpen()}>
                  <div class="workspace-browser">
                    <div class="workspace-browser-header">
                      <button
                        class="btn btn-icon"
                        type="button"
                        disabled={
                          browseLoading() ||
                          !browsePayload()?.parent ||
                          (() => {
                            if (!props.defaultWorkingDir) return false;
                            const current = browsePayload()?.path || "";
                            const isWindows = current.includes("\\") || Boolean(current.match(/^[a-zA-Z]:/));
                            const normalize = (p: string) => {
                              let cleaned = p.replace(/[\\/]+/g, "/");
                              if (cleaned.endsWith("/")) cleaned = cleaned.slice(0, -1);
                              return isWindows ? cleaned.toLowerCase() : cleaned;
                            };
                            return normalize(current) === normalize(props.defaultWorkingDir);
                          })()
                        }
                        title="Parent directory"
                        aria-label="Parent directory"
                        onClick={() => void loadBrowsePath(browsePayload()?.parent)}
                      >
                        <ArrowUp size={14} />
                      </button>
                      <div class="workspace-browser-breadcrumbs">
                        <For each={pathSegments()}>
                          {(segment, index) => (
                            <>
                              <Show when={index() > 0}>
                                <span class="breadcrumb-separator">/</span>
                              </Show>
                              <button
                                class="breadcrumb-btn"
                                type="button"
                                disabled={browseLoading()}
                                onClick={() => void loadBrowsePath(segment.path)}
                                title={segment.path}
                              >
                                {segment.name}
                              </button>
                            </>
                          )}
                        </For>
                      </div>
                      <button
                        class="btn btn-icon"
                        type="button"
                        disabled={browseLoading()}
                        title="Refresh directories"
                        aria-label="Refresh directories"
                        onClick={() => void loadBrowsePath(browsePayload()?.path || localDraft())}
                      >
                        <RefreshCw size={14} />
                      </button>
                    </div>
                    <div class="workspace-browser-roots" style="display: flex; align-items: center; justify-content: space-between; overflow: hidden; gap: 8px;">
                      <div style="display: flex; gap: 6px; overflow-x: auto; flex: 1;">
                        <For each={browsePayload()?.roots || []}>
                          {(root) => (
                            <button
                              class="workspace-browser-root"
                              type="button"
                              disabled={browseLoading()}
                              onClick={() => void loadBrowsePath(root.path)}
                              title={root.path}
                            >
                              <HardDrive size={13} />
                              <span>{root.name}</span>
                            </button>
                          )}
                        </For>
                      </div>
                      <button
                        class="btn btn-icon btn-sm"
                        type="button"
                        style="flex: 0 0 auto;"
                        disabled={browseLoading() || !(browsePayload()?.path || localDraft())}
                        title="Create new folder"
                        aria-label="Create new folder"
                        onClick={() => setCreateFolderOpen(true)}
                      >
                        <Plus size={14} />
                      </button>
                    </div>
                    <div class="workspace-browser-list">
                      <Show
                        when={!browseLoading()}
                        fallback={<div class="workspace-browser-state">Loading directories...</div>}
                      >
                        <Show
                          when={!browseError()}
                          fallback={<div class="workspace-browser-state error">{browseError()}</div>}
                        >
                          <For
                            each={filteredEntries()}
                            fallback={<div class="workspace-browser-state">No child directories.</div>}
                          >
                            {(entry) => (
                              <button
                                class="workspace-browser-item"
                                type="button"
                                onClick={() => void loadBrowsePath(entry.path)}
                                title={entry.path}
                              >
                                <FolderOpen size={14} />
                                <span>{entry.name}</span>
                                <ChevronRight size={13} />
                              </button>
                            )}
                          </For>
                          <Show when={browsePayload()?.truncated}>
                            <div class="workspace-browser-state">Directory list truncated.</div>
                          </Show>
                        </Show>
                      </Show>
                    </div>
                    <div class="workspace-browser-footer">
                      <button class="btn btn-sm" type="button" onClick={closeBrowser}>
                        Cancel
                      </button>
                      <button
                        class="btn btn-sm btn-primary"
                        type="button"
                        disabled={!browsePayload()?.path}
                        onClick={chooseCurrentBrowsePath}
                      >
                        <CheckCircle2 size={13} />
                        Choose folder
                      </button>
                    </div>
                    <Dialog
                      open={createFolderOpen()}
                      title="Create New Folder"
                      onClose={() => {
                        setCreateFolderOpen(false);
                        setNewFolderName("");
                      }}
                      footer={
                        <div class="row-wrap" style="justify-content: flex-end; gap: 8px;">
                          <button
                            class="btn"
                            type="button"
                            disabled={createFolderResolving()}
                            onClick={() => {
                              setCreateFolderOpen(false);
                              setNewFolderName("");
                            }}
                          >
                            Cancel
                          </button>
                          <button
                            class="btn btn-primary"
                            type="button"
                            disabled={createFolderResolving() || !newFolderName().trim()}
                            onClick={handleCreateFolderSubmit}
                          >
                            {createFolderResolving() ? "Creating..." : "Create"}
                          </button>
                        </div>
                      }
                    >
                      <div class="field" style="display: flex; flex-direction: column; gap: 8px;">
                        <label style="font-size: 12px; font-weight: 600; color: var(--muted);">Folder Name</label>
                        <input
                          class="input"
                          value={newFolderName()}
                          disabled={createFolderResolving()}
                          placeholder="Enter folder name"
                          onInput={(event) => setNewFolderName(event.currentTarget.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" && newFolderName().trim() && !createFolderResolving()) {
                              event.preventDefault();
                              void handleCreateFolderSubmit();
                            }
                          }}
                          ref={(el) => setTimeout(() => el?.focus(), 50)}
                        />
                      </div>
                    </Dialog>
                  </div>
                </Show>
              </div>
                    }
                  >
                    <div class="workspace-selector-row-enhanced">
                      <div class="workspace-input-group">
                        <input
                          class="input workspace-selector-input"
                          value={modalSandboxId()}
                          disabled={props.disabled}
                          placeholder="sandbox ID (leave empty to create new)"
                          onInput={(event) => setModalSandboxId(event.currentTarget.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              commitSelection();
                            }
                          }}
                        />
                      </div>
                      <button
                        class="btn btn-sm btn-primary workspace-use-btn"
                        type="button"
                        disabled={resolveDisabled()}
                        onClick={() => commitSelection()}
                        title={modalSandboxId().trim() ? "Attach sandbox" : "Create sandbox"}
                      >
                        <Cloud size={13} />
                        {modalSandboxId().trim() ? "Attach" : "Create"}
                      </button>
                    </div>
                  </Show>
                }
              >
                <div class="workspace-selector-row-enhanced">
                  <div class="workspace-input-group">
                    <input
                      class="input workspace-selector-input"
                      value={daytonaSandboxId()}
                      disabled={props.disabled}
                      placeholder="sandbox ID (leave empty to create new)"
                      onInput={(event) => setDaytonaSandboxId(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitSelection();
                        }
                      }}
                    />
                  </div>
                  <button
                    class="btn btn-sm btn-primary workspace-use-btn"
                    type="button"
                    disabled={resolveDisabled()}
                    onClick={() => commitSelection()}
                    title={daytonaSandboxId().trim() ? "Attach sandbox" : "Create sandbox"}
                  >
                    <Cloud size={13} />
                    {daytonaSandboxId().trim() ? "Attach" : "Create"}
                  </button>
                </div>
              </Show>
            }
          >
            <Show
              when={backend() !== "local"}
              fallback={
                <div class="workspace-selector-row">
                  <SelectControl
                    value={repositoryId()}
                    options={repositoryOptions()}
                    disabled={props.disabled || repositoriesLoading() || !repositoryOptions().length}
                    onChange={setRepositoryId}
                    ariaLabel="GitHub repository"
                    title={selectedRepository()?.full_name || "Select repository"}
                    icon={<GitBranch size={14} />}
                  />
                  <button
                    class="btn btn-sm btn-primary"
                    type="button"
                    disabled={resolveDisabled()}
                    onClick={() => commitSelection()}
                  >
                    <CheckCircle2 size={13} />
                    Use
                  </button>
                </div>
              }
            >
              <div class="workspace-selector-row-enhanced">
                <div class="workspace-input-group">
                  <input
                    class="input workspace-selector-input"
                    value={backend() === "daytona" ? daytonaSandboxId() : modalSandboxId()}
                    disabled={props.disabled}
                    placeholder={`${backend() === "daytona" ? "Daytona" : "Modal"} sandbox ID (leave empty to create new)`}
                    onInput={(event) => {
                      if (backend() === "daytona") {
                        setDaytonaSandboxId(event.currentTarget.value);
                      } else {
                        setModalSandboxId(event.currentTarget.value);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        commitSelection();
                      }
                    }}
                  />
                </div>
              </div>
              <div class="workspace-selector-row">
                <SelectControl
                  value={repositoryId()}
                  options={repositoryOptions()}
                  disabled={props.disabled || repositoriesLoading() || !repositoryOptions().length}
                  onChange={setRepositoryId}
                  ariaLabel="GitHub repository"
                  title={selectedRepository()?.full_name || "Select repository"}
                  icon={<GitBranch size={14} />}
                />
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  disabled={resolveDisabled()}
                  onClick={() => commitSelection()}
                >
                  <CheckCircle2 size={13} />
                  {(backend() === "daytona" ? daytonaSandboxId() : modalSandboxId()).trim()
                    ? "Attach & clone"
                    : "Create & clone"}
                </button>
              </div>
            </Show>
              <Show when={repositoriesError() || (!repositoriesLoading() && !repositories().length)}>
                <div class="hint workspace-selector-hint">
                  {repositoriesError() || "No synced GitHub repositories."}
                </div>
              </Show>
              <Show when={backend() !== "local"}>
                <div class="hint workspace-selector-hint">
                  Repository will be cloned inside the {backend() === "daytona" ? "Daytona" : "Modal"} sandbox. The agent runs against the cloned copy; pushing back to GitHub still happens from the local webhook flow.
                </div>
              </Show>
            </Show>
        </div>
      </Show>
    </div>
  );
}
