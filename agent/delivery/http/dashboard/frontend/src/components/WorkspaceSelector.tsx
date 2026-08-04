import { createEffect, createMemo, createSignal, For, Match, Show, Switch } from "solid-js";
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
  Sparkles,
} from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import {
  getBackends,
  getBackendDisplayName,
  getEnabledBackends,
  isBackendEnabled,
} from "@/lib/catalogStore";
import { getBackendIcon } from "@/lib/iconRegistry";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import {
  isGitHubWorkspace,
  isTempWorkspace,
  workspaceDisplayLabel,
  workspaceDisplayLabelFromValues,
} from "@/lib/workspace";
import type {
  GitHubPayload,
  GitHubRepositoryBinding,
  SandboxListPayload,
  WorkspaceBackendKey,
  WorkspaceRef,
} from "@/types";
import { isSandboxBackend } from "@/types";

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

export type WorkspaceSourceKey = "path" | "sandbox" | "github" | "temp";

export type WorkspaceSelectionDraft = {
  backend: WorkspaceBackendKey;
  source: WorkspaceSourceKey;
  localPath: string;
  sandboxId: string;
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

function sourceForBackend(backendName: string): WorkspaceSourceKey[] {
  if (!isSandboxBackend(backendName)) {
    return ["path", "temp", "github"];
  }
  const info = getBackends().find((b) => b.name === backendName);
  const caps = new Set(info?.capabilities ?? []);
  const sources: WorkspaceSourceKey[] = [];
  if (info && isBackendEnabled(info.name) && caps.has("sandbox_inventory")) {
    sources.push("sandbox");
  }
  sources.push("github");
  return sources;
}

function defaultSourceForBackend(backendName: string): WorkspaceSourceKey {
  return sourceForBackend(backendName)[0] ?? "github";
}

function backendFromWorkspace(workspace: WorkspaceRef | null | undefined): WorkspaceBackendKey {
  if (workspace && isSandboxBackend(workspace.backend) && isBackendEnabled(workspace.backend)) {
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
  if (isTempWorkspace(workspace)) {
    return "temp";
  }
  return defaultSourceForBackend(backend);
}

function sourceIcon(source: WorkspaceSourceKey) {
  switch (source) {
    case "github":
      return <GitBranch size={13} />;
    case "sandbox":
      return <Cloud size={13} />;
    case "temp":
      return <Sparkles size={13} />;
    default:
      return <FolderOpen size={13} />;
  }
}

function sourceTitle(source: WorkspaceSourceKey): string {
  switch (source) {
    case "github":
      return "GitHub";
    case "sandbox":
      return "Sandbox";
    case "temp":
      return "Temp";
    default:
      return "Path";
  }
}

export function WorkspaceSelector(props: WorkspaceSelectorProps) {
  const { showToast } = useToast();
  const [backend, setBackend] = createSignal<WorkspaceBackendKey>("local");
  const [source, setSource] = createSignal<WorkspaceSourceKey>(
    props.workspace || props.selection
      ? defaultSourceForBackend(backendFromWorkspace(props.workspace))
      : "temp",
  );
  const [localDraft, setLocalDraft] = createSignal(props.defaultWorkingDir);
  const [sandboxId, setSandboxId] = createSignal("");
  const [repositories, setRepositories] = createSignal<GitHubRepositoryBinding[]>([]);
  const [repositoryId, setRepositoryId] = createSignal("");
  const [repositoriesLoading, setRepositoriesLoading] = createSignal(false);
  const [repositoriesError, setRepositoriesError] = createSignal("");
  const [sandboxes, setSandboxes] = createSignal<SandboxListPayload["sandboxes"]>([]);
  const [sandboxesLoading, setSandboxesLoading] = createSignal(false);
  const [sandboxesError, setSandboxesError] = createSignal("");
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

  const backendOptions = createMemo(() =>
    getEnabledBackends().map((b) => ({
      value: b.name,
      label: b.title,
    })),
  );

  const selectedBackendIcon = createMemo(() => {
    const iconFn = getBackendIcon(backend());
    return iconFn();
  });

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

  const subNote = createMemo(() => {
    const src = source();
    if (src === "path") {
      return "The agent reads and writes files directly in this folder.";
    }
    if (src === "temp") {
      return "Isolated scratch space. Session files are removed when the thread is deleted.";
    }
    if (src === "github") {
      return isSandboxBackend(backend())
        ? `Repository will be cloned inside the ${getBackendDisplayName(backend())} sandbox.`
        : "Repository will be cloned to a local cache folder.";
    }
    return "";
  });

  const resolveDisabled = createMemo(() => {
    if (props.disabled) {
      return true;
    }
    const src = source();
    if (src === "path") {
      return !localDraft().trim();
    }
    if (src === "sandbox") {
      return !isSandboxBackend(backend()) || !isBackendEnabled(backend());
    }
    if (src === "github") {
      return !repositoryId();
    }
    if (src === "temp") {
      return false;
    }
    return true;
  });

  const loadRepositories = async () => {
    setRepositoriesLoading(true);
    setRepositoriesError("");
    try {
      const payload = await apiFetch<GitHubPayload>(API_PATHS.github);
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

  // Monotonic id guarding against stale sandbox-list responses: only the
  // latest request is allowed to write state.
  let sandboxesRequestId = 0;

  const loadSandboxes = async (backendName: string) => {
    const requestId = ++sandboxesRequestId;
    setSandboxesLoading(true);
    setSandboxesError("");
    try {
      const params = new URLSearchParams({ backend: backendName, include_all: "true" });
      const payload = await apiFetch<SandboxListPayload>(
        `${API_PATHS.sandboxes}?${params.toString()}`,
      );
      if (requestId === sandboxesRequestId) {
        setSandboxes(payload.sandboxes || []);
      }
    } catch (err) {
      if (requestId === sandboxesRequestId) {
        setSandboxes([]);
        setSandboxesError(err instanceof Error ? err.message : "Failed to load sandboxes");
      }
    } finally {
      if (requestId === sandboxesRequestId) {
        setSandboxesLoading(false);
      }
    }
  };

  const loadBrowsePath = async (path?: string) => {
    setBrowseLoading(true);
    setBrowseError("");
    try {
      const query = path?.trim() ? `?path=${encodeURIComponent(path.trim())}` : "";
      const payload = await apiFetch<WorkspaceBrowsePayload>(
        `${API_PATHS.workspaceBrowse}${query}`,
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
    setCreateFolderOpen(false);
    setNewFolderName("");
    setBrowserOpen(true);
    void loadBrowsePath(localDraft().trim() || props.defaultWorkingDir);
  };

  const closeBrowser = () => {
    setBrowserOpen(false);
    setBrowseError("");
    setCreateFolderOpen(false);
    setNewFolderName("");
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
    // GitHub clones into a sandbox backend may target an existing sandbox,
    // so the sandbox locator is preserved for both "sandbox" and "github".
    const sid = (src === "sandbox" || src === "github") && isSandboxBackend(back)
      ? sandboxId().trim()
      : "";
    let label = "";
    if (src === "temp") {
      label = "Temporary workspace";
    } else if (src === "github") {
      label = repository?.full_name || "GitHub repository";
    } else if (src === "path") {
      label = workspaceDisplayLabelFromValues("", targetPath);
    } else if (sid) {
      label = `${back}:${sid}`;
    } else {
      label = `${back} sandbox (new)`;
    }

    return {
      backend: back,
      source: src,
      localPath: targetPath.trim(),
      sandboxId: sid,
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
    if (isSandboxBackend(workspace.backend)) {
      setSandboxId(workspace.locator);
    }
  });

  createEffect(() => {
    const selection = props.selection;
    if (!selection) {
      return;
    }
    const nextBackend = isBackendEnabled(selection.backend) ? selection.backend : "local";
    const allowedSources = sourceForBackend(nextBackend);
    setBackend(nextBackend);
    setSource(
      allowedSources.includes(selection.source)
        ? selection.source
        : defaultSourceForBackend(nextBackend),
    );
    setLocalDraft(selection.localPath);
    setSandboxId(isSandboxBackend(nextBackend) ? selection.sandboxId : "");
    setRepositoryId(selection.repositoryId === null ? "" : String(selection.repositoryId));
    setResolvedLabel(selection.label);
  });

  createEffect(() => {
    const options = backendOptions();
    if (options.length && !options.some((option) => option.value === backend())) {
      setBackend(options[0].value as WorkspaceBackendKey);
    }
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

  createEffect(() => {
    const back = backend();
    if (source() === "sandbox" && isSandboxBackend(back) && isBackendEnabled(back)) {
      void loadSandboxes(back);
    } else {
      // Invalidate any in-flight sandbox request before resetting state.
      sandboxesRequestId += 1;
      setSandboxes([]);
      setSandboxesError("");
      setSandboxesLoading(false);
    }
  });

  useCatalogAndLoad(async () => {
    await loadRepositories();
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
          <div class="workspace-selector-toolbar">
            <div class="workspace-selector-backends">
              <SelectControl
                value={backend()}
                options={backendOptions()}
                disabled={props.disabled}
                onChange={(value) => setBackend(value as WorkspaceBackendKey)}
                ariaLabel="Workspace backend"
                title={backendOptions().find((b) => b.value === backend())?.label || backend()}
                icon={selectedBackendIcon()}
              />
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
                    {sourceIcon(src)}
                    <span>{sourceTitle(src)}</span>
                  </button>
                )}
              </For>
            </div>
          </div>

          <Switch>
            <Match when={source() === "path"}>
              <div class="workspace-selector-row-enhanced">
                <div class="workspace-input-group">
                  <input
                    class="input workspace-selector-input has-browse"
                    value={localDraft()}
                    disabled={props.disabled}
                    placeholder="Working directory"
                    spellcheck={false}
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
              </div>
            </Match>

            <Match when={source() === "temp"}>
              <div class="workspace-selector-row-enhanced">
                <button
                  class="btn btn-sm btn-primary workspace-use-btn"
                  type="button"
                  disabled={resolveDisabled()}
                  onClick={() => commitSelection()}
                  title="Use temporary workspace"
                >
                  <Sparkles size={13} />
                  Use temporary workspace
                </button>
              </div>
            </Match>

            <Match when={source() === "github"}>
              <Show when={isSandboxBackend(backend())}>
                <div class="workspace-selector-row-enhanced">
                  <div class="workspace-input-group">
                    <input
                      class="input workspace-selector-input"
                      value={sandboxId()}
                      disabled={props.disabled}
                      placeholder="sandbox ID (leave empty to create new)"
                      spellcheck={false}
                      onInput={(event) => setSandboxId(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitSelection();
                        }
                      }}
                    />
                  </div>
                </div>
              </Show>
              <div class="workspace-selector-row-enhanced workspace-selector-row-top">
                <div class="workspace-option-list">
                  <Show
                    when={!repositoriesLoading()}
                    fallback={<div class="workspace-option-state">Loading repositories...</div>}
                  >
                    <Show
                      when={!repositoriesError()}
                      fallback={<div class="workspace-option-state error">{repositoriesError()}</div>}
                    >
                      <For
                        each={repositories()}
                        fallback={<div class="workspace-option-state">No synced GitHub repositories.</div>}
                      >
                        {(repository) => (
                          <button
                            class={`workspace-option-row ${
                              repositoryId() === String(repository.repository_id) ? "active" : ""
                            }`}
                            type="button"
                            disabled={props.disabled}
                            onClick={() => setRepositoryId(String(repository.repository_id))}
                          >
                            <GitBranch size={13} />
                            <span class="grow">{repository.full_name}</span>
                            <span class="sub">{repository.default_branch || "main"}</span>
                          </button>
                        )}
                      </For>
                    </Show>
                  </Show>
                </div>
                <button
                  class="btn btn-sm btn-primary workspace-use-btn"
                  type="button"
                  disabled={resolveDisabled()}
                  onClick={() => commitSelection()}
                >
                  <CheckCircle2 size={13} />
                  {isSandboxBackend(backend())
                    ? sandboxId().trim()
                      ? "Attach & clone"
                      : "Clone"
                    : "Use"}
                </button>
              </div>
            </Match>

            <Match when={source() === "sandbox"}>
              <div class="workspace-selector-row-enhanced">
                <div class="workspace-input-group">
                  <input
                    class="input workspace-selector-input"
                    value={sandboxId()}
                    disabled={props.disabled}
                    placeholder="sandbox ID (leave empty to create new)"
                    spellcheck={false}
                    onInput={(event) => setSandboxId(event.currentTarget.value)}
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
                  title={sandboxId().trim() ? "Attach sandbox" : "Create sandbox"}
                >
                  <Cloud size={13} />
                  {sandboxId().trim() ? "Attach" : "Create"}
                </button>
              </div>
              <div class="workspace-option-list">
                <Show
                  when={!sandboxesLoading()}
                  fallback={<div class="workspace-option-state">Loading sandboxes...</div>}
                >
                  <Show
                    when={!sandboxesError()}
                    fallback={<div class="workspace-option-state error">{sandboxesError()}</div>}
                  >
                    <For
                      each={sandboxes()}
                      fallback={
                        <div class="workspace-option-state">
                          No existing sandboxes. Leave the ID empty to create a new one.
                        </div>
                      }
                    >
                      {(sandbox) => (
                        <button
                          class={`workspace-option-row ${sandboxId() === sandbox.sandbox_id ? "active" : ""}`}
                          type="button"
                          disabled={props.disabled}
                          onClick={() => setSandboxId(sandbox.sandbox_id)}
                          title={sandbox.sandbox_id}
                        >
                          <Cloud size={13} />
                          <span class="grow">{sandbox.sandbox_id}</span>
                          <span class="sub">
                            {[sandbox.label, sandbox.root].filter((part) => part && part.trim()).join(" · ")}
                          </span>
                        </button>
                      )}
                    </For>
                  </Show>
                </Show>
              </div>
            </Match>
          </Switch>

          <Show when={subNote()}>
            <p class="workspace-sub-note">{subNote()}</p>
          </Show>
        </div>
      </Show>

      <Dialog
        open={browserOpen()}
        title="Choose folder"
        onClose={closeBrowser}
        footer={
          <>
            <div class="workspace-new-folder">
              <Show when={createFolderOpen()}>
                <input
                  class="input"
                  value={newFolderName()}
                  disabled={createFolderResolving()}
                  placeholder="New folder name"
                  spellcheck={false}
                  onInput={(event) => setNewFolderName(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && newFolderName().trim() && !createFolderResolving()) {
                      event.preventDefault();
                      void handleCreateFolderSubmit();
                    }
                  }}
                  ref={(el) => setTimeout(() => el?.focus(), 50)}
                />
                <button
                  class="btn btn-sm"
                  type="button"
                  disabled={createFolderResolving() || !newFolderName().trim()}
                  onClick={() => void handleCreateFolderSubmit()}
                >
                  {createFolderResolving() ? "Creating..." : "Create"}
                </button>
              </Show>
            </div>
            <div class="workspace-browser-actions">
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
                Choose this folder
              </button>
            </div>
          </>
        }
      >
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
          <div class="workspace-browser-roots">
            <div class="workspace-browser-roots-scroll">
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
              disabled={browseLoading() || !(browsePayload()?.path || localDraft())}
              title="Create new folder"
              aria-label="Create new folder"
              onClick={() => setCreateFolderOpen((open) => !open)}
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
        </div>
      </Dialog>
    </div>
  );
}
