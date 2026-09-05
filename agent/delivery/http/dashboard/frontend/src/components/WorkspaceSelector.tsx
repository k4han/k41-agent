import { createEffect, createMemo, createSignal, For, Match, Show, Switch } from "solid-js";
import {
  FolderOpen,
  Folder,
  GitBranch,
  CheckCircle2,
  Check,
  ArrowUp,
  HardDrive,
  Plus,
  ChevronRight,
  RefreshCw,
  Cloud,
  Sparkles,
  Search,
  Copy,
  X,
  Lock,
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
      return <GitBranch size={14} />;
    case "sandbox":
      return <Cloud size={14} />;
    case "temp":
      return <Sparkles size={14} />;
    default:
      return <FolderOpen size={14} />;
  }
}

function sourceTitle(source: WorkspaceSourceKey): string {
  switch (source) {
    case "github":
      return "GitHub";
    case "sandbox":
      return "Cloud Sandbox";
    case "temp":
      return "Temporary";
    default:
      return "Local Folder";
  }
}

function sourceBadgeLabel(source: WorkspaceSourceKey): string {
  switch (source) {
    case "github":
      return "GitHub Repository";
    case "sandbox":
      return "Cloud Sandbox";
    case "temp":
      return "Temporary Scratchpad";
    default:
      return "Local Directory";
  }
}

function getTailPath(fullPath: string): string {
  if (!fullPath) return "";
  const parts = fullPath.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : fullPath;
}

export function WorkspaceSelector(props: WorkspaceSelectorProps) {
  const { showToast } = useToast();
  const [backend, setBackend] = createSignal<WorkspaceBackendKey>("local");
  const [source, setSource] = createSignal<WorkspaceSourceKey>(
    props.workspace || props.selection
      ? defaultSourceForBackend(backendFromWorkspace(props.workspace))
      : "temp",
  );
  const [localDraft, setLocalDraft] = createSignal(props.defaultWorkingDir || props.workingDir || "");
  const [sandboxId, setSandboxId] = createSignal("");
  const [repositories, setRepositories] = createSignal<GitHubRepositoryBinding[]>([]);
  const [repositoryId, setRepositoryId] = createSignal("");
  const [repositoriesLoading, setRepositoriesLoading] = createSignal(false);
  const [repositoriesError, setRepositoriesError] = createSignal("");
  const [repoSearch, setRepoSearch] = createSignal("");
  const [sandboxes, setSandboxes] = createSignal<SandboxListPayload["sandboxes"]>([]);
  const [sandboxesLoading, setSandboxesLoading] = createSignal(false);
  const [sandboxesError, setSandboxesError] = createSignal("");
  const [sandboxSearch, setSandboxSearch] = createSignal("");
  const [resolvedLabel, setResolvedLabel] = createSignal("");
  const [browserOpen, setBrowserOpen] = createSignal(false);
  const [browsePayload, setBrowsePayload] = createSignal<WorkspaceBrowsePayload | null>(null);
  const [browseLoading, setBrowseLoading] = createSignal(false);
  const [browseError, setBrowseError] = createSignal("");
  const [folderFilter, setFolderFilter] = createSignal("");
  const [createFolderOpen, setCreateFolderOpen] = createSignal(false);
  const [newFolderName, setNewFolderName] = createSignal("");
  const [createFolderResolving, setCreateFolderResolving] = createSignal(false);
  const [copiedPath, setCopiedPath] = createSignal(false);

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
        entry.name !== "venv",
    );
  });

  const displayedFolderEntries = createMemo(() => {
    const query = folderFilter().trim().toLowerCase();
    const entries = filteredEntries();
    if (!query) return entries;
    return entries.filter((e) => e.name.toLowerCase().includes(query));
  });

  const filteredRepositories = createMemo(() => {
    const query = repoSearch().trim().toLowerCase();
    const list = repositories();
    if (!query) return list;
    return list.filter((r) => r.full_name.toLowerCase().includes(query));
  });

  const filteredSandboxes = createMemo(() => {
    const query = sandboxSearch().trim().toLowerCase();
    const list = sandboxes();
    if (!query) return list;
    return list.filter(
      (s) =>
        s.sandbox_id.toLowerCase().includes(query) ||
        (s.label && s.label.toLowerCase().includes(query)),
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

  const activeSource = createMemo<WorkspaceSourceKey>(() => {
    if (props.workspace) {
      return sourceFromWorkspace(backendFromWorkspace(props.workspace), props.workspace);
    }
    if (props.selection) {
      return props.selection.source;
    }
    return source();
  });

  const workspaceStatusLabel = createMemo(() => {
    if (props.workspace) {
      return workspaceDisplayLabel(props.workspace);
    }
    if (props.selection?.label) {
      return props.selection.label;
    }
    if (resolvedLabel()) {
      return resolvedLabel();
    }
    if (source() === "temp") {
      return "Temporary workspace (auto-provisions on first message)";
    }
    if (props.workingDir) {
      return workspaceDisplayLabelFromValues("", props.workingDir);
    }
    return "Temporary workspace (default)";
  });

  const workspaceStatusTitle = createMemo(() =>
    props.workingDir || props.selection?.label || resolvedLabel() || workspaceStatusLabel(),
  );

  const isCurrentPathActive = createMemo(() => {
    if (source() !== "path") return false;
    const draft = localDraft().trim();
    if (!draft) return false;
    if (props.workspace?.backend === "local" && props.workspace?.locator === draft) return true;
    if (props.selection?.source === "path" && props.selection?.localPath === draft) return true;
    return false;
  });

  const isTempActive = createMemo(() => {
    return props.selection?.source === "temp" || (!props.selection && !props.workspace);
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
    setFolderFilter("");
    setBrowserOpen(true);
    void loadBrowsePath(localDraft().trim() || props.defaultWorkingDir);
  };

  const closeBrowser = () => {
    setBrowserOpen(false);
    setBrowseError("");
    setCreateFolderOpen(false);
    setNewFolderName("");
    setFolderFilter("");
  };

  const chooseCurrentBrowsePath = () => {
    const payload = browsePayload();
    if (payload?.path) {
      setLocalDraft(payload.path);
      closeBrowser();
      commitSelection(payload.path);
    }
  };

  const copyPathToClipboard = async () => {
    const target = browsePayload()?.path || localDraft();
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target);
      setCopiedPath(true);
      setTimeout(() => setCopiedPath(false), 1500);
    } catch {
      // ignore
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
    showToast(`Workspace set to ${selection.label}.`, "success");
  };

  createEffect(() => {
    const workspace = props.workspace;
    if (workspace && workspace.backend !== "local") {
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
      <Show when={props.locked}>
        <div class="workspace-selector-locked-status">
          <Lock size={13} />
          <span title={workspaceStatusTitle()}>
            {workspaceStatusLabel()}
          </span>
        </div>
      </Show>

      {/* CONFIGURATION CONTROLS */}
      <Show when={!props.locked}>
        <div class="workspace-selector-body">
          {/* TAB SELECTION & BACKEND ROW */}
          <div class="workspace-nav-bar">
            <div class="workspace-tabs-list" role="tablist" aria-label="Workspace source">
              <For each={sourceForBackend(backend())}>
                {(src) => (
                  <button
                    class={`workspace-tab-btn ${source() === src ? "active" : ""}`}
                    type="button"
                    disabled={props.disabled}
                    onClick={() => {
                      setSource(src);
                      if (src === "temp") {
                        commitSelection();
                      }
                    }}
                    aria-selected={source() === src}
                    role="tab"
                  >
                    {sourceIcon(src)}
                    <span>{sourceTitle(src)}</span>
                  </button>
                )}
              </For>
            </div>

            <Show when={backendOptions().length > 1}>
              <div class="workspace-backend-control">
                <span class="workspace-backend-label">Backend:</span>
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
            </Show>
          </div>

          {/* TAB PANELS */}
          <div class="workspace-tab-content">
            <Switch>
              {/* LOCAL FOLDER */}
              <Match when={source() === "path"}>
                <div class="workspace-panel-path">
                  <div class="workspace-input-row">
                    <div class="workspace-path-input-container">
                      <FolderOpen class="workspace-input-leading-icon" size={15} />
                      <input
                        class="input workspace-path-input"
                        value={localDraft()}
                        disabled={props.disabled}
                        placeholder="Path to project directory..."
                        spellcheck={false}
                        onInput={(event) => setLocalDraft(event.currentTarget.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitSelection();
                          }
                        }}
                      />
                      <Show when={localDraft().trim()}>
                        <button
                          class="workspace-input-clear-btn"
                          type="button"
                          title="Clear input"
                          disabled={props.disabled}
                          onClick={() => setLocalDraft("")}
                        >
                          <X size={13} />
                        </button>
                      </Show>
                    </div>

                    <button
                      class="btn workspace-browse-btn"
                      type="button"
                      disabled={props.disabled}
                      onClick={openBrowser}
                      title="Browse directory"
                    >
                      <FolderOpen size={14} />
                      <span>Browse</span>
                    </button>

                    <button
                      class="btn btn-primary workspace-apply-btn"
                      type="button"
                      disabled={resolveDisabled() || isCurrentPathActive()}
                      onClick={() => commitSelection()}
                    >
                      <Check size={14} />
                      <span>{isCurrentPathActive() ? "Current" : "Use Folder"}</span>
                    </button>
                  </div>

                  {/* QUICK JUMP CHIP */}
                  <Show when={props.defaultWorkingDir && props.defaultWorkingDir !== localDraft()}>
                    <div class="workspace-quick-chip-row">
                      <span class="workspace-quick-chip-label">Default:</span>
                      <button
                        class="workspace-quick-chip"
                        type="button"
                        disabled={props.disabled}
                        onClick={() => {
                          setLocalDraft(props.defaultWorkingDir);
                          commitSelection(props.defaultWorkingDir);
                        }}
                        title={props.defaultWorkingDir}
                      >
                        <HardDrive size={12} />
                        <span>{getTailPath(props.defaultWorkingDir) || props.defaultWorkingDir}</span>
                      </button>
                    </div>
                  </Show>

                  <p class="workspace-panel-hint">
                    The agent reads and modifies code files directly inside this folder.
                  </p>
                </div>
              </Match>

              {/* TEMPORARY */}
              <Match when={source() === "temp"}>
                <div class="workspace-panel-temp">
                  <div class="workspace-temp-card">
                    <div class="workspace-temp-icon-container">
                      <Sparkles size={20} />
                    </div>
                    <div class="workspace-temp-info">
                      <div class="workspace-temp-title">Isolated Scratchpad</div>
                      <p class="workspace-temp-text">
                        Start coding immediately. The agent creates files in an ephemeral session folder.
                        No local files will be modified. All session data is discarded when the thread is deleted.
                      </p>
                    </div>
                    <div class="workspace-temp-action">
                      <Show
                        when={isTempActive()}
                        fallback={
                          <button
                            class="btn btn-primary workspace-temp-btn"
                            type="button"
                            disabled={props.disabled}
                            onClick={() => commitSelection()}
                          >
                            <Sparkles size={14} />
                            <span>Activate</span>
                          </button>
                        }
                      >
                        <span class="workspace-active-tag">
                          <CheckCircle2 size={13} />
                          <span>Active</span>
                        </span>
                      </Show>
                    </div>
                  </div>
                </div>
              </Match>

              {/* GITHUB */}
              <Match when={source() === "github"}>
                <div class="workspace-panel-github">
                  <Show when={isSandboxBackend(backend())}>
                    <div class="workspace-sandbox-sub-row">
                      <label class="workspace-field-label">Target Sandbox ID (optional):</label>
                      <input
                        class="input workspace-sandbox-input"
                        value={sandboxId()}
                        disabled={props.disabled}
                        placeholder="Leave empty to create a new sandbox automatically"
                        spellcheck={false}
                        onInput={(event) => setSandboxId(event.currentTarget.value)}
                      />
                    </div>
                  </Show>

                  <div class="workspace-search-bar">
                    <div class="workspace-search-input-wrap">
                      <Search size={14} class="workspace-search-icon" />
                      <input
                        class="input workspace-search-field"
                        value={repoSearch()}
                        disabled={props.disabled || repositoriesLoading()}
                        placeholder="Filter synced repositories..."
                        spellcheck={false}
                        onInput={(event) => setRepoSearch(event.currentTarget.value)}
                      />
                      <Show when={repoSearch()}>
                        <button
                          class="workspace-input-clear-btn"
                          type="button"
                          onClick={() => setRepoSearch("")}
                        >
                          <X size={13} />
                        </button>
                      </Show>
                    </div>
                    <button
                      class="btn btn-icon workspace-refresh-icon-btn"
                      type="button"
                      disabled={repositoriesLoading()}
                      title="Reload repositories"
                      onClick={() => void loadRepositories()}
                    >
                      <RefreshCw size={13} class={repositoriesLoading() ? "workspace-spinning" : ""} />
                    </button>
                  </div>

                  <div class="workspace-options-scroll">
                    <Show
                      when={!repositoriesLoading()}
                      fallback={<div class="workspace-status-message">Loading synced repositories...</div>}
                    >
                      <Show
                        when={!repositoriesError()}
                        fallback={<div class="workspace-status-message error">{repositoriesError()}</div>}
                      >
                        <For
                          each={filteredRepositories()}
                          fallback={
                            <div class="workspace-status-message">
                              {repoSearch()
                                ? "No repositories match your filter."
                                : "No synced GitHub repositories. Connect in Settings > Channels."}
                            </div>
                          }
                        >
                          {(repository) => (
                            <button
                              class={`workspace-item-card ${
                                repositoryId() === String(repository.repository_id) ? "selected" : ""
                              }`}
                              type="button"
                              disabled={props.disabled}
                              onClick={() => {
                                setRepositoryId(String(repository.repository_id));
                                commitSelection();
                              }}
                            >
                              <div class="workspace-item-card-left">
                                <GitBranch size={15} class="workspace-item-icon" />
                                <div class="workspace-item-card-text">
                                  <span class="workspace-item-name">{repository.full_name}</span>
                                  <span class="workspace-item-meta">{repository.default_branch || "main"}</span>
                                </div>
                              </div>
                              <Show when={repositoryId() === String(repository.repository_id)}>
                                <span class="workspace-item-check">
                                  <Check size={13} />
                                </span>
                              </Show>
                            </button>
                          )}
                        </For>
                      </Show>
                    </Show>
                  </div>

                  <p class="workspace-panel-hint">
                    {isSandboxBackend(backend())
                      ? `Repository will be cloned inside the ${getBackendDisplayName(backend())} sandbox.`
                      : "Repository will be cloned to the local caching directory."}
                  </p>
                </div>
              </Match>

              {/* SANDBOX */}
              <Match when={source() === "sandbox"}>
                <div class="workspace-panel-sandbox">
                  <div class="workspace-input-row">
                    <div class="workspace-path-input-container">
                      <Cloud class="workspace-input-leading-icon" size={15} />
                      <input
                        class="input workspace-path-input"
                        value={sandboxId()}
                        disabled={props.disabled}
                        placeholder="Existing sandbox ID (or leave blank to create new)..."
                        spellcheck={false}
                        onInput={(event) => setSandboxId(event.currentTarget.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitSelection();
                          }
                        }}
                      />
                      <Show when={sandboxId().trim()}>
                        <button
                          class="workspace-input-clear-btn"
                          type="button"
                          onClick={() => setSandboxId("")}
                        >
                          <X size={13} />
                        </button>
                      </Show>
                    </div>

                    <button
                      class="btn btn-primary workspace-apply-btn"
                      type="button"
                      disabled={resolveDisabled()}
                      onClick={() => commitSelection()}
                    >
                      <Cloud size={14} />
                      <span>{sandboxId().trim() ? "Attach" : "Create New"}</span>
                    </button>
                  </div>

                  {/* SANDBOX SEARCH */}
                  <div class="workspace-search-bar">
                    <div class="workspace-search-input-wrap">
                      <Search size={14} class="workspace-search-icon" />
                      <input
                        class="input workspace-search-field"
                        value={sandboxSearch()}
                        disabled={props.disabled || sandboxesLoading()}
                        placeholder="Filter active sandboxes..."
                        spellcheck={false}
                        onInput={(event) => setSandboxSearch(event.currentTarget.value)}
                      />
                      <Show when={sandboxSearch()}>
                        <button
                          class="workspace-input-clear-btn"
                          type="button"
                          onClick={() => setSandboxSearch("")}
                        >
                          <X size={13} />
                        </button>
                      </Show>
                    </div>
                  </div>

                  <div class="workspace-options-scroll">
                    <Show
                      when={!sandboxesLoading()}
                      fallback={<div class="workspace-status-message">Loading sandboxes...</div>}
                    >
                      <Show
                        when={!sandboxesError()}
                        fallback={<div class="workspace-status-message error">{sandboxesError()}</div>}
                      >
                        <For
                          each={filteredSandboxes()}
                          fallback={
                            <div class="workspace-status-message">
                              {sandboxSearch()
                                ? "No sandboxes match your filter."
                                : "No active sandboxes. Leave the ID blank to create one."}
                            </div>
                          }
                        >
                          {(sandbox) => (
                            <button
                              class={`workspace-item-card ${
                                sandboxId() === sandbox.sandbox_id ? "selected" : ""
                              }`}
                              type="button"
                              disabled={props.disabled}
                              onClick={() => {
                                setSandboxId(sandbox.sandbox_id);
                                commitSelection();
                              }}
                              title={sandbox.sandbox_id}
                            >
                              <div class="workspace-item-card-left">
                                <Cloud size={15} class="workspace-item-icon" />
                                <div class="workspace-item-card-text">
                                  <span class="workspace-item-name">{sandbox.sandbox_id}</span>
                                  <span class="workspace-item-meta">
                                    {[sandbox.label, sandbox.root].filter((part) => part && part.trim()).join(" · ")}
                                  </span>
                                </div>
                              </div>
                              <Show when={sandboxId() === sandbox.sandbox_id}>
                                <span class="workspace-item-check">
                                  <Check size={13} />
                                </span>
                              </Show>
                            </button>
                          )}
                        </For>
                      </Show>
                    </Show>
                  </div>
                </div>
              </Match>
            </Switch>
          </div>
        </div>
      </Show>

      {/* 3. FOLDER BROWSER DIALOG */}
      <Dialog
        open={browserOpen()}
        size="lg"
        title="Choose Local Folder"
        onClose={closeBrowser}
        footer={
          <div class="workspace-browser-footer">
            <div class="workspace-browser-current-path" title={browsePayload()?.path || ""}>
              <span class="workspace-browser-path-label">Selected:</span>
              <span class="workspace-browser-path-val">{browsePayload()?.path || "(none)"}</span>
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
                <Check size={13} />
                <span>Choose This Folder</span>
              </button>
            </div>
          </div>
        }
      >
        <div class="workspace-browser">
          {/* HEADER NAVIGATION */}
          <div class="workspace-browser-header">
            <button
              class="btn btn-icon workspace-browser-nav-btn"
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
              title="Parent directory (Up)"
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

            <div class="workspace-browser-header-tools">
              <button
                class="btn btn-icon workspace-browser-nav-btn"
                type="button"
                title={copiedPath() ? "Copied!" : "Copy current path"}
                aria-label="Copy current path"
                onClick={() => void copyPathToClipboard()}
              >
                <Show when={copiedPath()} fallback={<Copy size={13} />}>
                  <Check size={13} />
                </Show>
              </button>
              <button
                class="btn btn-icon workspace-browser-nav-btn"
                type="button"
                disabled={browseLoading()}
                title="Refresh folder listing"
                aria-label="Refresh folder listing"
                onClick={() => void loadBrowsePath(browsePayload()?.path || localDraft())}
              >
                <RefreshCw size={13} class={browseLoading() ? "workspace-spinning" : ""} />
              </button>
            </div>
          </div>

          {/* DRIVE ROOTS & FILTER BAR */}
          <div class="workspace-browser-subbar">
            <Show when={(browsePayload()?.roots || []).length > 0}>
              <div class="workspace-browser-roots-scroll">
                <For each={browsePayload()?.roots || []}>
                  {(root) => (
                    <button
                      class={`workspace-browser-root ${
                        browsePayload()?.path?.toLowerCase().startsWith(root.path.toLowerCase()) ? "active" : ""
                      }`}
                      type="button"
                      disabled={browseLoading()}
                      onClick={() => void loadBrowsePath(root.path)}
                      title={root.path}
                    >
                      <HardDrive size={12} />
                      <span>{root.name}</span>
                    </button>
                  )}
                </For>
              </div>
            </Show>

            <div class="workspace-browser-filter-wrap">
              <Search size={13} class="workspace-browser-filter-icon" />
              <input
                class="input workspace-browser-filter-input"
                value={folderFilter()}
                placeholder="Filter subfolders..."
                spellcheck={false}
                onInput={(event) => setFolderFilter(event.currentTarget.value)}
              />
              <Show when={folderFilter()}>
                <button
                  class="workspace-input-clear-btn"
                  type="button"
                  onClick={() => setFolderFilter("")}
                >
                  <X size={12} />
                </button>
              </Show>
            </div>

            <button
              class={`btn btn-sm workspace-browser-new-folder-toggle ${createFolderOpen() ? "btn-primary" : ""}`}
              type="button"
              disabled={browseLoading() || !(browsePayload()?.path || localDraft())}
              title="Create new folder"
              onClick={() => setCreateFolderOpen((open) => !open)}
            >
              <Plus size={13} />
              <span>New Folder</span>
            </button>
          </div>

          {/* INLINE NEW FOLDER CREATOR */}
          <Show when={createFolderOpen()}>
            <div class="workspace-browser-new-folder-form">
              <Folder size={14} class="workspace-new-folder-icon" />
              <input
                class="input workspace-new-folder-input"
                value={newFolderName()}
                disabled={createFolderResolving()}
                placeholder="New folder name..."
                spellcheck={false}
                onInput={(event) => setNewFolderName(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && newFolderName().trim() && !createFolderResolving()) {
                    event.preventDefault();
                    void handleCreateFolderSubmit();
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setCreateFolderOpen(false);
                    setNewFolderName("");
                  }
                }}
                ref={(el) => setTimeout(() => el?.focus(), 50)}
              />
              <button
                class="btn btn-sm btn-primary"
                type="button"
                disabled={createFolderResolving() || !newFolderName().trim()}
                onClick={() => void handleCreateFolderSubmit()}
              >
                {createFolderResolving() ? "Creating..." : "Create"}
              </button>
              <button
                class="btn btn-sm"
                type="button"
                disabled={createFolderResolving()}
                onClick={() => {
                  setCreateFolderOpen(false);
                  setNewFolderName("");
                }}
              >
                Cancel
              </button>
            </div>
          </Show>

          {/* DIRECTORIES LIST */}
          <div class="workspace-browser-list">
            <Show
              when={!browseLoading()}
              fallback={<div class="workspace-browser-state">Loading subdirectories...</div>}
            >
              <Show
                when={!browseError()}
                fallback={<div class="workspace-browser-state error">{browseError()}</div>}
              >
                <For
                  each={displayedFolderEntries()}
                  fallback={
                    <div class="workspace-browser-state">
                      {folderFilter() ? "No subfolders match your search." : "No child directories found."}
                    </div>
                  }
                >
                  {(entry) => (
                    <button
                      class="workspace-browser-item"
                      type="button"
                      onClick={() => void loadBrowsePath(entry.path)}
                      title={entry.path}
                    >
                      <Folder size={15} class="workspace-browser-item-icon" />
                      <span class="workspace-browser-item-name">{entry.name}</span>
                      <ChevronRight size={13} class="workspace-browser-item-arrow" />
                    </button>
                  )}
                </For>
                <Show when={browsePayload()?.truncated}>
                  <div class="workspace-browser-state">Directory listing truncated.</div>
                </Show>
              </Show>
            </Show>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
