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
} from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import {
  daytonaWorkspaceRef,
  formatWorkspaceRoot,
  localWorkspaceRef,
  modalWorkspaceRef,
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

type WorkspaceResolvePayload = {
  kind: string;
  label: string;
  workspace: WorkspaceRef;
};

export interface WorkspaceSelectorProps {
  workingDir: string;
  defaultWorkingDir: string;
  threadId?: string;
  workspace?: WorkspaceRef | null;
  locked: boolean;
  disabled?: boolean;
  onResolved: (workspace: WorkspaceRef) => void;
}

export function WorkspaceSelector(props: WorkspaceSelectorProps) {
  const { showToast } = useToast();
  const [kind, setKind] = createSignal<"local" | "github" | "daytona" | "modal">("local");
  const [localDraft, setLocalDraft] = createSignal(props.defaultWorkingDir);
  const [daytonaSandboxId, setDaytonaSandboxId] = createSignal("");
  const [modalSandboxId, setModalSandboxId] = createSignal("");
  const [repositories, setRepositories] = createSignal<GitHubRepositoryBinding[]>([]);
  const [repositoryId, setRepositoryId] = createSignal("");
  const [repositoriesLoading, setRepositoriesLoading] = createSignal(false);
  const [repositoriesError, setRepositoriesError] = createSignal("");
  const [resolving, setResolving] = createSignal(false);
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
    || workspaceDisplayLabelFromValues(resolvedLabel(), props.workingDir || resolvedLabel()),
  );
  const workspaceStatusTitle = createMemo(() =>
    props.workingDir || resolvedLabel() || workspaceStatusLabel(),
  );

  const resolveDisabled = createMemo(() => {
    if (props.disabled || resolving()) {
      return true;
    }
    if (kind() === "local") {
      return !localDraft().trim();
    }
    if (kind() === "daytona") {
      return false;
    }
    if (kind() === "modal") {
      return false;
    }
    return !repositoryId();
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
      void resolveWorkspace(payload.path);
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

  const resolveWorkspace = async (pathOverride?: string) => {
    const targetPath = pathOverride !== undefined ? pathOverride : localDraft();
    if (props.disabled || resolving()) {
      return;
    }
    if (kind() === "local" && !targetPath.trim()) {
      return;
    }
    setResolving(true);
    try {
      const daytonaWorkspace =
        kind() === "daytona" ? daytonaWorkspaceRef(daytonaSandboxId()) : null;
      const modalWorkspace =
        kind() === "modal" ? modalWorkspaceRef(modalSandboxId()) : null;
      const payload = await postJson<WorkspaceResolvePayload>(
        "/dashboard-api/workspace/resolve",
        kind() === "local"
          ? {
              kind: "local",
              thread_id: props.threadId || null,
              workspace: localWorkspaceRef(targetPath),
            }
          : kind() === "github"
            ? {
                kind: "github",
                thread_id: props.threadId || null,
                repository_id: Number(repositoryId()),
              }
            : {
                kind: kind() === "daytona" ? "daytona" : "modal",
                thread_id: props.threadId || null,
                workspace: daytonaWorkspace || modalWorkspace,
                locator: daytonaWorkspace?.locator || modalWorkspace?.locator || null,
              },
      );
      if (payload.workspace.backend === "daytona") {
        setDaytonaSandboxId(payload.workspace.locator);
      } else if (payload.workspace.backend === "modal") {
        setModalSandboxId(payload.workspace.locator);
      } else {
        setLocalDraft(payload.workspace.locator);
      }
      setResolvedLabel(payload.label || payload.workspace.label || payload.workspace.locator);
      props.onResolved(payload.workspace);
      showToast("Workspace selected.", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Workspace selection failed", "error");
    } finally {
      setResolving(false);
    }
  };

  createEffect(() => {
    if (props.workingDir) {
      setLocalDraft(props.workingDir);
    }
  });

  createEffect(() => {
    const workspace = props.workspace;
    if (!workspace) {
      return;
    }
    if (workspace.backend === "daytona") {
      setKind("daytona");
      setDaytonaSandboxId(workspace.locator);
    } else if (workspace.backend === "modal") {
      setKind("modal");
      setModalSandboxId(workspace.locator);
    } else if (workspace.backend === "github") {
      setKind("github");
    } else {
      setKind("local");
    }
  });

  createEffect(() => {
    if (!props.workingDir && props.defaultWorkingDir && !localDraft()) {
      setLocalDraft(props.defaultWorkingDir);
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
          <div class="workspace-selector-modes" role="tablist" aria-label="Workspace source">
            <button
              class={`workspace-selector-mode ${kind() === "local" ? "active" : ""}`}
              type="button"
              disabled={props.disabled || resolving()}
              onClick={() => setKind("local")}
              aria-selected={kind() === "local"}
              role="tab"
            >
              <FolderOpen size={14} />
              <span>Local path</span>
            </button>
            <button
              class={`workspace-selector-mode ${kind() === "github" ? "active" : ""}`}
              type="button"
              disabled={props.disabled || resolving()}
              onClick={() => setKind("github")}
              aria-selected={kind() === "github"}
              role="tab"
            >
              <GitBranch size={14} />
              <span>GitHub repo</span>
            </button>
            <button
              class={`workspace-selector-mode ${kind() === "daytona" ? "active" : ""}`}
              type="button"
              disabled={props.disabled || resolving()}
              onClick={() => setKind("daytona")}
              aria-selected={kind() === "daytona"}
              role="tab"
            >
              <Cloud size={14} />
              <span>Daytona</span>
            </button>
            <button
              class={`workspace-selector-mode ${kind() === "modal" ? "active" : ""}`}
              type="button"
              disabled={props.disabled || resolving()}
              onClick={() => setKind("modal")}
              aria-selected={kind() === "modal"}
              role="tab"
            >
              <Cloud size={14} />
              <span>Modal</span>
            </button>
          </div>

          <Show
            when={kind() === "github"}
            fallback={
              <Show
                when={kind() === "daytona"}
                fallback={
                  <Show
                    when={kind() === "modal"}
                    fallback={
                      <div class="workspace-selector-row-enhanced">
                <div class="workspace-input-group">
                  <input
                    class="input workspace-selector-input"
                    value={formatWorkspaceRoot(localDraft())}
                    disabled={props.disabled || resolving()}
                    placeholder="Working directory"
                    onInput={(event) => setLocalDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void resolveWorkspace();
                      }
                    }}
                  />
                  <button
                    class="workspace-input-btn-browse"
                    type="button"
                    title="Browse folder"
                    disabled={props.disabled || resolving()}
                    onClick={openBrowser}
                  >
                    <FolderOpen size={14} />
                  </button>
                </div>
                <button
                  class="btn btn-sm btn-primary workspace-use-btn"
                  type="button"
                  disabled={resolveDisabled()}
                  onClick={() => void resolveWorkspace()}
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
                          disabled={props.disabled || resolving()}
                          placeholder="Sandbox ID"
                          onInput={(event) => setModalSandboxId(event.currentTarget.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              void resolveWorkspace();
                            }
                          }}
                        />
                      </div>
                      <button
                        class="btn btn-sm btn-primary workspace-use-btn"
                        type="button"
                        disabled={resolveDisabled()}
                        onClick={() => void resolveWorkspace()}
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
                      disabled={props.disabled || resolving()}
                      placeholder="Sandbox ID"
                      onInput={(event) => setDaytonaSandboxId(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void resolveWorkspace();
                        }
                      }}
                    />
                  </div>
                  <button
                    class="btn btn-sm btn-primary workspace-use-btn"
                    type="button"
                    disabled={resolveDisabled()}
                    onClick={() => void resolveWorkspace()}
                    title={daytonaSandboxId().trim() ? "Attach sandbox" : "Create sandbox"}
                  >
                    <Cloud size={13} />
                    {daytonaSandboxId().trim() ? "Attach" : "Create"}
                  </button>
                </div>
              </Show>
            }
          >
            <div class="workspace-selector-row">
              <SelectControl
                value={repositoryId()}
                options={repositoryOptions()}
                disabled={props.disabled || resolving() || repositoriesLoading() || !repositoryOptions().length}
                onChange={setRepositoryId}
                ariaLabel="GitHub repository"
                title={selectedRepository()?.full_name || "Select repository"}
                icon={<GitBranch size={14} />}
              />
              <button
                class="btn btn-sm"
                type="button"
                disabled={resolveDisabled()}
                onClick={() => void resolveWorkspace()}
              >
                <CheckCircle2 size={13} />
                Use
              </button>
            </div>
            <Show when={repositoriesError() || (!repositoriesLoading() && !repositories().length)}>
              <div class="hint workspace-selector-hint">
                {repositoriesError() || "No synced GitHub repositories."}
              </div>
            </Show>
          </Show>
        </div>
      </Show>
    </div>
  );
}
