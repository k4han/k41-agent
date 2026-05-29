import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import {
  ArrowDown,
  ArrowUp,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  FolderOpen,
  GitBranch,
  GripVertical,
  HardDrive,
  Image as ImageIcon,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Send,
  Square,
  X,
} from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { Dialog } from "@/components/Dialog";
import { ModelPicker } from "@/components/ModelPicker";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import {
  createTranscriptTool,
  findTranscriptToolTarget,
  TranscriptItemView,
} from "@/components/Transcript";
import { useToast } from "@/components/Toast";
import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import { apiFetch, postJson, readError } from "@/lib/api";
import {
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import type { TranscriptAttachment, TranscriptItem } from "@/components/Transcript";
import {
  formatWorkspaceRoot,
  localWorkspaceRef,
  workspaceDisplayLabel,
  workspaceDisplayLabelFromValues,
} from "@/lib/workspace";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import type {
  ActiveSession,
  AgentCard,
  AgentsPayload,
  BackgroundTask,
  GitHubPayload,
  GitHubRepositoryBinding,
  WorkspaceRef,
} from "@/types";
import {
  persistedStreams,
  getOrCreateStreamSignals,
  cleanupStreamSignals,
  cleanupStaleStreams,
  type ChatTranscriptItem,
} from "@/lib/chatStreamStore";
type ChatAttachmentKind = "text" | "image";
type ChatAttachmentPayload = {
  name: string;
  mime_type: string;
  size: number;
  kind: ChatAttachmentKind;
  content?: string;
  base64?: string;
};
type PendingAttachment = ChatAttachmentPayload & {
  id: number;
  preview_url?: string;
};
type ChatPayload = {
  message: string;
  user_id: string;
  agent_name: string;
  workspace?: WorkspaceRef;
  provider?: string;
  model?: string;
  thread_id?: string;
  new_thread?: boolean;
  attachments?: ChatAttachmentPayload[];
};
type AppendScrollMode = "bottom" | "turn-start" | "none";
type BackgroundTaskSnapshot = ThreadMessagesPayload & {
  task?: BackgroundTask | null;
  active_session?: ActiveSession | null;
};
type DefaultWorkspacePayload = {
  workspace: WorkspaceRef;
};
type WorkspaceResolvePayload = {
  kind: string;
  label: string;
  workspace: WorkspaceRef;
};
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

const MAX_ATTACHMENTS = 5;
const MAX_TEXT_ATTACHMENT_BYTES = 100 * 1024;
const MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024;
const DEFAULT_ATTACHMENT_MESSAGE = "Please review the attached file(s).";
const WORKSPACE_EXPLORER_OPEN_KEY = "kaka-dashboard-workspace-explorer-open";
const WORKSPACE_EXPLORER_WIDTH_KEY = "kaka-dashboard-workspace-explorer-width";
const WORKSPACE_EXPLORER_DEFAULT_WIDTH = 560;
const WORKSPACE_EXPLORER_MIN_WIDTH = 340;
const WORKSPACE_EXPLORER_MAX_WIDTH = 920;
const ACTIVE_TASK_STATUSES = new Set(["pending", "running"]);
const ATTACHMENT_ACCEPT = [
  "image/*",
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".xml",
  ".csv",
  ".html",
  ".css",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".py",
  ".go",
  ".rs",
  ".java",
  ".c",
  ".cpp",
  ".h",
  ".hpp",
  ".cs",
  ".php",
  ".rb",
  ".swift",
  ".kt",
  ".kts",
  ".dart",
  ".sql",
  ".sh",
  ".ps1",
  ".bat",
  ".env",
  ".gitignore",
  "Dockerfile",
].join(",");
const TEXT_MIME_TYPES = new Set([
  "application/javascript",
  "application/json",
  "application/toml",
  "application/typescript",
  "application/xml",
  "application/x-yaml",
  "text/javascript",
]);
const TEXT_EXTENSIONS = new Set([
  ".bat",
  ".c",
  ".cpp",
  ".cs",
  ".css",
  ".csv",
  ".dart",
  ".env",
  ".gitignore",
  ".go",
  ".h",
  ".hpp",
  ".html",
  ".java",
  ".js",
  ".json",
  ".jsx",
  ".kt",
  ".kts",
  ".md",
  ".markdown",
  ".php",
  ".ps1",
  ".py",
  ".rb",
  ".rs",
  ".sh",
  ".sql",
  ".swift",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
  "dockerfile",
]);

let nextItemId = 1;
let nextAttachmentId = 1;

function fileExtension(fileName: string): string {
  const lowerName = fileName.toLowerCase();
  const dotIndex = lowerName.lastIndexOf(".");
  return dotIndex >= 0 ? lowerName.slice(dotIndex) : lowerName;
}

function attachmentKind(file: File): ChatAttachmentKind | null {
  if (file.type.startsWith("image/")) {
    return "image";
  }
  if (file.type.startsWith("text/") || TEXT_MIME_TYPES.has(file.type)) {
    return "text";
  }
  return TEXT_EXTENSIONS.has(fileExtension(file.name)) ? "text" : null;
}

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

function toTranscriptAttachment(attachment: PendingAttachment): TranscriptAttachment {
  return {
    name: attachment.name,
    mime_type: attachment.mime_type,
    size: attachment.size,
    kind: attachment.kind,
  };
}

function toPayloadAttachment(attachment: PendingAttachment): ChatAttachmentPayload {
  return {
    name: attachment.name,
    mime_type: attachment.mime_type,
    size: attachment.size,
    kind: attachment.kind,
    content: attachment.content,
    base64: attachment.base64,
  };
}

function WorkspaceSelector(props: {
  workingDir: string;
  defaultWorkingDir: string;
  workspace?: WorkspaceRef | null;
  locked: boolean;
  disabled?: boolean;
  onResolved: (workspace: WorkspaceRef) => void;
}) {
  const { showToast } = useToast();
  const [kind, setKind] = createSignal<"local" | "github">("local");
  const [localDraft, setLocalDraft] = createSignal(props.defaultWorkingDir);
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
      const payload = await postJson<WorkspaceResolvePayload>(
        "/dashboard-api/workspace/resolve",
        kind() === "local"
          ? { kind: "local", workspace: localWorkspaceRef(targetPath) }
          : { kind: "github", repository_id: Number(repositoryId()) },
      );
      setLocalDraft(payload.workspace.locator);
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
          </div>

          <Show
            when={kind() === "github"}
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

export function ChatPage() {
  const navigate = useNavigate();
  const params = useParams<{ threadId?: string }>();
  const [searchParams] = useSearchParams();
  const routeThreadId = () => (params.threadId ? decodeURIComponent(params.threadId) : "");
  const [data, setData] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [threadData, setThreadData] = createSignal<ThreadMessagesPayload>();
  const [threadError, setThreadError] = createSignal("");
  const [threadLoading, setThreadLoading] = createSignal(false);
  const [currentThreadId, setCurrentThreadId] = createSignal("");
  const [agentName, setAgentName] = createSignal("");
  const [provider, setProvider] = createSignal("default");
  const [model, setModel] = createSignal("");
  const [workingDir, setWorkingDir] = createSignal("");
  const [workspaceRef, setWorkspaceRef] = createSignal<WorkspaceRef | null>(null);
  const [defaultWorkingDir, setDefaultWorkingDir] = createSignal("");
  const [defaultWorkspace, setDefaultWorkspace] = createSignal<WorkspaceRef | null>(null);
  const [workspaceExplorerOpen, setWorkspaceExplorerOpen] = createSignal(true);
  const [workspaceExplorerWidth, setWorkspaceExplorerWidth] = createSignal(
    WORKSPACE_EXPLORER_DEFAULT_WIDTH,
  );
  const [workspaceExplorerResizing, setWorkspaceExplorerResizing] = createSignal(false);
  const [prompt, setPrompt] = createSignal("");
  const [localItems, setLocalItems] = createSignal<ChatTranscriptItem[]>([]);
  const [localStreaming, setLocalStreaming] = createSignal(false);
  const [localController, setLocalController] = createSignal<AbortController | null>(null);
  const [currentStreamThreadId, setCurrentStreamThreadId] = createSignal<string | null>(null);
  const items = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.items[0]();
    }
    return localItems();
  };
  const setItems = (
    v: ChatTranscriptItem[] | ((prev: ChatTranscriptItem[]) => ChatTranscriptItem[]),
    targetThreadId?: string
  ) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.items[1](v as any);
      return;
    }
    setLocalItems(v as any);
  };
  const streaming = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.streaming[0]();
    }
    return localStreaming();
  };
  const setStreaming = (v: boolean, targetThreadId?: string) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.streaming[1](v);
      return;
    }
    setLocalStreaming(v);
  };
  const controller = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.controller[0]();
    }
    return localController();
  };
  const setController = (v: AbortController | null, targetThreadId?: string) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.controller[1](v);
      return;
    }
    setLocalController(v);
  };
  const [backgroundTask, setBackgroundTask] = createSignal<BackgroundTask | null>(null);
  const [backgroundLive, setBackgroundLive] = createSignal(false);
  const [backgroundStreamError, setBackgroundStreamError] = createSignal("");
  const [backgroundSession, setBackgroundSession] = createSignal<ActiveSession | null>(null);
  const [activeSession, setActiveSession] = createSignal<ActiveSession | null>(null);
  const [composerOptionsOpen, setComposerOptionsOpen] = createSignal(false);
  const [attachments, setAttachments] = createSignal<PendingAttachment[]>([]);
  const [todosExpanded, setTodosExpanded] = createSignal(true);
  const [autoScroll, setAutoScroll] = createSignal(true);
  const [turnAnchorItemId, setTurnAnchorItemId] = createSignal<number | null>(null);
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = createSignal(0);
  const [recursionLimitReached, setRecursionLimitReached] = createSignal(false);
  const { showToast } = useToast();

  const filteredItems = createMemo(() =>
    items().filter((item) => !(item.type === "tool" && item.name === "write_todos"))
  );

  const currentTodos = createMemo(() => {
    const allItems = items();
    for (let i = allItems.length - 1; i >= 0; i--) {
      const item = allItems[i];
      if (item.type === "tool" && item.name === "write_todos") {
        const args = item.args as any;
        if (args && Array.isArray(args.todos)) {
          return args.todos as Array<{ content: string; status: "pending" | "in_progress" | "completed" }>;
        }
      }
    }
    return null;
  });

  const todoProgress = createMemo(() => {
    const list = currentTodos();
    if (!list || list.length === 0) {
      return { current: 0, total: 0, activeText: "", activeStatus: "pending" };
    }

    const total = list.length;
    let activeIdx = list.findIndex((t) => t.status === "in_progress");
    if (activeIdx === -1) {
      activeIdx = list.findIndex((t) => t.status === "pending");
    }

    const current = activeIdx === -1 ? total : activeIdx + 1;
    let activeText = "";
    if (activeIdx !== -1) {
      activeText = list[activeIdx].content;
    } else if (list.length > 0) {
      activeText = list[list.length - 1].content;
    }

    const activeStatus = activeIdx !== -1 ? list[activeIdx].status : "completed";
    return { current, total, activeText, activeStatus };
  });
  let transcriptRef: HTMLDivElement | undefined;
  let chatShellRef: HTMLDivElement | undefined;
  let chatPromptRef: HTMLTextAreaElement | undefined;
  let fileInputRef: HTMLInputElement | undefined;
  let loadedThreadId: string | null = null;
  let isUnmounting = false;
  let threadLoadRequestId = 0;
  let backgroundEventSource: EventSource | null = null;
  let backgroundEventThreadId = "";
  let stopWorkspaceResize: (() => void) | null = null;

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<AgentsPayload>("/dashboard-api/agents"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chat options");
    }
  };

  const setWorkspace = (value: WorkspaceRef | string | null) => {
    if (!value) {
      setWorkingDir("");
      setWorkspaceRef(null);
      return;
    }

    if (typeof value === "string") {
      const nextValue = value.trim();
      setWorkingDir(nextValue);
      setWorkspaceRef(localWorkspaceRef(nextValue));
      return;
    }

    setWorkingDir(value.locator.trim());
    setWorkspaceRef(value);
  };

  const clampWorkspaceExplorerWidth = (
    width: number,
    availableWidth = window.innerWidth,
  ) => {
    const viewportMax = Math.max(
      WORKSPACE_EXPLORER_MIN_WIDTH,
      availableWidth - 436,
    );
    const maxWidth = Math.min(WORKSPACE_EXPLORER_MAX_WIDTH, viewportMax);
    return Math.min(
      maxWidth,
      Math.max(WORKSPACE_EXPLORER_MIN_WIDTH, Math.round(width)),
    );
  };

  const setExplorerOpen = (next: boolean) => {
    setWorkspaceExplorerOpen(next);
    window.localStorage.setItem(WORKSPACE_EXPLORER_OPEN_KEY, next ? "open" : "closed");
  };

  const toggleWorkspaceExplorer = () => {
    setExplorerOpen(!workspaceExplorerOpen());
  };

  const resizeChatPromptInput = () => {
    if (!chatPromptRef) {
      return;
    }
    const computed = window.getComputedStyle(chatPromptRef);
    const maxHeight = Number.parseFloat(computed.maxHeight);
    chatPromptRef.style.height = "auto";
    chatPromptRef.style.height = `${Math.min(
      chatPromptRef.scrollHeight,
      Number.isFinite(maxHeight) ? maxHeight : chatPromptRef.scrollHeight,
    )}px`;
    chatPromptRef.style.overflowY = chatPromptRef.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  const applyWorkspaceExplorerWidth = (width: number, availableWidth?: number) => {
    const nextWidth = clampWorkspaceExplorerWidth(width, availableWidth);
    setWorkspaceExplorerWidth(nextWidth);
    window.localStorage.setItem(WORKSPACE_EXPLORER_WIDTH_KEY, String(nextWidth));
  };

  const endWorkspaceResize = () => {
    stopWorkspaceResize?.();
    stopWorkspaceResize = null;
    setWorkspaceExplorerResizing(false);
    document.body.classList.remove("workspace-resizing");
  };

  const startWorkspaceResize = (event: PointerEvent) => {
    if (!chatShellRef || !workspaceExplorerOpen()) {
      return;
    }
    event.preventDefault();
    setWorkspaceExplorerResizing(true);
    document.body.classList.add("workspace-resizing");

    const move = (moveEvent: PointerEvent) => {
      const shellRect = chatShellRef?.getBoundingClientRect();
      if (!shellRect) {
        return;
      }
      applyWorkspaceExplorerWidth(shellRect.right - moveEvent.clientX, shellRect.width);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      stopWorkspaceResize = null;
      setWorkspaceExplorerResizing(false);
      document.body.classList.remove("workspace-resizing");
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    stopWorkspaceResize = stop;
  };

  const loadDefaultWorkspace = async () => {
    try {
      const payload = await apiFetch<DefaultWorkspacePayload>("/dashboard-api/workspace/default");
      const fallback = payload.workspace?.locator || "";
      setDefaultWorkingDir(fallback);
      setDefaultWorkspace(payload.workspace || null);
    } catch {
      setDefaultWorkingDir("");
      setDefaultWorkspace(null);
    }
  };

  const validCards = createMemo(() => (data()?.cards || []).filter((card) => card.valid && !card.hidden));
  const agentOptions = createMemo(() =>
    validCards().map((card) => ({
      value: card.name,
      label: card.display_name || card.name,
    })),
  );
  const selectedCard = createMemo<AgentCard | undefined>(() =>
    validCards().find((card) => card.name === agentName()),
  );
  const pageSubtitle = createMemo(() => (
    currentThreadId()
      ? "Continue this thread."
      : "Choose a workspace, then stream an agent response with visible tool calls."
  ));
  const isBackgroundThread = createMemo(() => threadData()?.kind === "background");
  const threadStatusVisible = createMemo(() => Boolean(
    threadError()
    || isBackgroundThread()
    || backgroundTask()
    || backgroundLive()
    || backgroundSession()
    || activeSession()
    || backgroundStreamError(),
  ));
  const threadBadgeVisible = createMemo(() => Boolean(
    isBackgroundThread()
    || backgroundTask()
    || backgroundLive()
    || backgroundSession()
    || activeSession(),
  ));
  const backgroundTaskActive = createMemo(() => {
    const task = backgroundTask();
    return Boolean(task && ACTIVE_TASK_STATUSES.has(task.status));
  });
  const conversationBusy = createMemo(() => (
    streaming() || threadLoading() || backgroundTaskActive() || backgroundLive()
  ));
  const workspaceLocked = createMemo(() => Boolean(currentThreadId() && workingDir().trim()));
  const workspaceMissing = createMemo(() => !workingDir().trim());
  const composerDisabled = createMemo(() => (
    conversationBusy() || workspaceMissing()
  ));
  const inputDisabled = createMemo(() => (
    threadLoading() || workspaceMissing()
  ));

  createEffect(() => {
    const payload = data();
    if (!payload || agentName()) {
      return;
    }
    const requested = String(searchParams.agent || "");
    const fallback = validCards().find((card) => card.name === "default") || validCards()[0];
    const next = validCards().find((card) => card.name === requested) || fallback;
    if (next) {
      setAgentName(next.name);
    }
  });

  createEffect(() => {
    const card = selectedCard();
    if (!card) {
      return;
    }
    setProvider(card.provider || "default");
    setModel(card.model || "");
  });

  createEffect(() => {
    prompt();
    resizeChatPromptInput();
  });

  createEffect(() => {
    const defWs = defaultWorkspace();
    if (!currentThreadId() && !workingDir().trim() && defWs) {
      const autoResolve = async () => {
        try {
          const resolved = await postJson<WorkspaceResolvePayload>(
            "/dashboard-api/workspace/resolve",
            { kind: defWs.backend || "local", workspace: defWs },
          );
          setWorkspace(resolved.workspace);
        } catch {
          setWorkspace(defWs);
        }
      };
      void autoResolve();
    }
  });

  const clearTurnAnchor = () => {
    setTurnAnchorItemId(null);
    setTurnAnchorSpacerHeight(0);
  };

  const getTranscriptItemElement = (id: number) =>
    transcriptRef?.querySelector<HTMLElement>(`[data-transcript-item-id="${id}"]`);

  const getTranscriptItemScrollTop = (target: HTMLElement) => {
    if (!transcriptRef) {
      return 0;
    }
    const transcriptRect = transcriptRef.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    return targetRect.top - transcriptRect.top + transcriptRef.scrollTop;
  };

  const scrollToTurnAnchor = (id: number) => {
    window.requestAnimationFrame(() => {
      if (!transcriptRef) {
        return;
      }
      const target = getTranscriptItemElement(id);
      if (!target) {
        return;
      }

      const targetTop = getTranscriptItemScrollTop(target);
      const currentSpacerHeight = turnAnchorSpacerHeight();
      const contentBelowTargetTop =
        transcriptRef.scrollHeight - currentSpacerHeight - targetTop;
      const nextSpacerHeight = Math.max(
        0,
        Math.ceil(transcriptRef.clientHeight - contentBelowTargetTop),
      );

      if (Math.abs(nextSpacerHeight - currentSpacerHeight) > 1) {
        setTurnAnchorSpacerHeight(nextSpacerHeight);
      }

      window.requestAnimationFrame(() => {
        if (!transcriptRef) {
          return;
        }
        const updatedTarget = getTranscriptItemElement(id);
        if (!updatedTarget) {
          return;
        }
        transcriptRef.scrollTop = getTranscriptItemScrollTop(updatedTarget);
      });
    });
  };

  const scrollToBottom = (force = false) => {
    if (!autoScroll() && !force) {
      return;
    }
    window.setTimeout(() => {
      const anchorId = turnAnchorItemId();
      if (anchorId !== null && !force) {
        scrollToTurnAnchor(anchorId);
        return;
      }
      if (force) {
        clearTurnAnchor();
      }
      if (transcriptRef) {
        transcriptRef.scrollTop = transcriptRef.scrollHeight;
      }
    }, 0);
  };

  const handleTranscriptScroll = () => {
    if (!transcriptRef) {
      return;
    }
    const threshold = 50; // px
    const isAtBottom =
      transcriptRef.scrollHeight - transcriptRef.scrollTop - transcriptRef.clientHeight < threshold;
    if (isAtBottom) {
      setAutoScroll(true);
    } else {
      setAutoScroll(false);
    }
  };

  const handleScrollToBottomClick = () => {
    clearTurnAnchor();
    setAutoScroll(true);
    scrollToBottom(true);
  };

  const appendItem = (
    item: TranscriptItem,
    scrollMode: AppendScrollMode = "bottom",
    targetThreadId?: string
  ): number => {
    const id = nextItemId;
    nextItemId += 1;
    setItems((current) => [...current, { ...item, id } as ChatTranscriptItem], targetThreadId);
    
    // Only scroll if this item is for the active thread
    const isCurrent = !isUnmounting && (!targetThreadId || targetThreadId === currentThreadId());
    if (isCurrent) {
      if (scrollMode === "turn-start") {
        setTurnAnchorItemId(id);
        setTurnAnchorSpacerHeight(0);
        setAutoScroll(true);
        scrollToTurnAnchor(id);
      } else if (scrollMode === "bottom") {
        scrollToBottom();
      }
    }
    return id;
  };

  const resetFileInput = () => {
    if (fileInputRef) {
      fileInputRef.value = "";
    }
  };

  const revokeAttachmentPreview = (attachment: PendingAttachment) => {
    if (attachment.preview_url) {
      URL.revokeObjectURL(attachment.preview_url);
    }
  };

  const clearAttachments = (itemsToClear: PendingAttachment[]) => {
    if (!itemsToClear.length) {
      return;
    }
    itemsToClear.forEach(revokeAttachmentPreview);
    setAttachments((current) => current.filter((item) => !itemsToClear.includes(item)));
    resetFileInput();
  };

  const clearAllAttachments = () => {
    const currentAttachments = attachments();
    if (!currentAttachments.length) {
      return;
    }
    currentAttachments.forEach(revokeAttachmentPreview);
    setAttachments([]);
    resetFileInput();
  };

  const removeAttachment = (id: number) => {
    const target = attachments().find((attachment) => attachment.id === id);
    if (!target) {
      return;
    }
    clearAttachments([target]);
  };

  const applyThreadPayload = (payload: ThreadMessagesPayload) => {
    clearTurnAnchor();
    setThreadData(payload);
    setCurrentThreadId(payload.thread_id);
    setWorkspace(payload.workspace || null);
    // If there's an active persisted stream for this thread, don't overwrite items
    if (!persistedStreams.has(payload.thread_id)) {
      setItems(
        toThreadTranscript(payload.messages).map((item) => ({
          ...item,
          id: nextItemId++,
        })),
      );
    }
    setAutoScroll(true);
    scrollToBottom(true);
  };

  const closeBackgroundStream = () => {
    if (backgroundEventSource) {
      backgroundEventSource.close();
    }
    backgroundEventSource = null;
    backgroundEventThreadId = "";
    setBackgroundLive(false);
    setBackgroundStreamError("");
    setBackgroundSession(null);
  };

  const applyBackgroundSnapshot = (snapshot: BackgroundTaskSnapshot) => {
    applyThreadPayload({
      thread_id: snapshot.thread_id,
      messages: snapshot.messages || [],
      platform: snapshot.platform,
      user_id: snapshot.user_id,
      channel_id: snapshot.channel_id,
      agent_name: snapshot.agent_name,
      title: snapshot.title,
      kind: snapshot.kind,
      workspace: snapshot.workspace,
    });
    setBackgroundTask(snapshot.task || null);
    setBackgroundSession(snapshot.active_session || null);
  };

  const openBackgroundStream = (threadId: string) => {
    closeBackgroundStream();
    const assistantIdRef = { id: null as number | null };
    const streamedRef = { received: false };
    const source = new EventSource(
      `/dashboard-api/background-task-events?thread_id=${encodeURIComponent(threadId)}`,
    );
    backgroundEventSource = source;
    backgroundEventThreadId = threadId;
    setBackgroundLive(true);

    source.addEventListener("snapshot", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      assistantIdRef.id = null;
      streamedRef.received = false;
      setBackgroundStreamError("");
      applyBackgroundSnapshot(JSON.parse(event.data) as BackgroundTaskSnapshot);
    });
    source.addEventListener("agent", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      handleEvent(
        JSON.parse(event.data) as Record<string, unknown>,
        assistantIdRef,
        streamedRef,
        { id: threadId, message: "" }
      );
    });
    source.addEventListener("task", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      const payload = JSON.parse(event.data) as { task?: BackgroundTask | null };
      setBackgroundTask(payload.task || null);
      setBackgroundStreamError("");
    });
    source.addEventListener("done", (event) => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      const payload = JSON.parse(event.data) as { task?: BackgroundTask | null };
      setBackgroundTask(payload.task || null);
      setBackgroundLive(false);
      source.close();
      if (backgroundEventSource === source) {
        backgroundEventSource = null;
        backgroundEventThreadId = "";
      }
      window.dispatchEvent(new CustomEvent("kaka:tasks-changed"));
    });
    source.addEventListener("heartbeat", () => {
      if (backgroundEventThreadId === threadId) {
        setBackgroundStreamError("");
      }
    });
    source.onerror = () => {
      if (backgroundEventThreadId !== threadId) {
        return;
      }
      setBackgroundStreamError("Live updates disconnected.");
      if (source.readyState === EventSource.CLOSED) {
        setBackgroundLive(false);
      }
    };
  };

  const loadThread = async (threadId: string) => {
    const requestId = threadLoadRequestId + 1;
    threadLoadRequestId = requestId;
    setCurrentThreadId(threadId);
    setThreadData(undefined);
    setThreadError("");
    setRecursionLimitReached(window.localStorage.getItem(`kaka:recursion-limit-reached:${threadId}`) === "true");
    // Don't clear items if there's a persisted stream for this thread
    if (!persistedStreams.has(threadId)) {
      setThreadLoading(true);
      setItems([]);
    }
    closeBackgroundStream();
    setBackgroundTask(null);
    setAutoScroll(true);

    try {
      const payload = await apiFetch<ThreadMessagesPayload>(threadApiPath(threadId));
      if (requestId !== threadLoadRequestId) {
        return;
      }
      applyThreadPayload(payload);
      if (payload.kind === "background") {
        openBackgroundStream(payload.thread_id);
      } else {
        closeBackgroundStream();
        setBackgroundTask(null);
      }
    } catch (err) {
      if (requestId !== threadLoadRequestId) {
        return;
      }
      setThreadError(err instanceof Error ? err.message : "Failed to load thread");
    } finally {
      if (requestId === threadLoadRequestId) {
        setThreadLoading(false);
        // If we have a persisted stream, ensure streaming state reflects it
        if (persistedStreams.has(threadId)) {
          if (persistedStreams.get(threadId)!.streaming[0]()) {
            setStreaming(true);
          }
        }
      }
    }
  };

  createEffect(() => {
    const threadId = routeThreadId();
    if (threadId === loadedThreadId) {
      return;
    }

    loadedThreadId = threadId;
    threadLoadRequestId += 1;
    clearAllAttachments();
    setRecursionLimitReached(false);

    if (!threadId) {
      setCurrentThreadId("");
      setCurrentStreamThreadId(null);
      setThreadData(undefined);
      setThreadError("");
      setThreadLoading(false);
      setItems([]);
      setWorkspace(null);
      clearTurnAnchor();
      closeBackgroundStream();
      setBackgroundTask(null);
      return;
    }

    // If there's a persisted stream for this thread, reconnect to it
    if (persistedStreams.has(threadId)) {
      setCurrentStreamThreadId(threadId);
      setCurrentThreadId(threadId);
      setThreadLoading(false);
      // Load thread data from API to get metadata (workspace, etc.)
      // but items are already being streamed via persisted signals
      void loadThread(threadId);
      return;
    }

    setCurrentStreamThreadId(null);
    void loadThread(threadId);
  });

  const updateMessage = (id: number, chunk: string, targetThreadId?: string) => {
    setItems((current) =>
      current.map((item) =>
        item.id === id && item.type === "message"
          ? { ...item, text: item.text + chunk }
          : item,
      ),
      targetThreadId
    );
    const isCurrent = !isUnmounting && (!targetThreadId || targetThreadId === currentThreadId());
    if (isCurrent) {
      scrollToBottom();
    }
  };

  const updateToolResult = (
    toolCallId: string,
    name: string,
    result: unknown,
    targetThreadId?: string
  ) => {
    setItems((current) => {
      const target = findTranscriptToolTarget(current, toolCallId, name);
      if (!target) {
        return [
          ...current,
          {
            id: nextItemId++,
            ...createTranscriptTool({ toolCallId, name, result }),
          } satisfies ChatTranscriptItem,
        ];
      }
      return current.map((item) =>
        item.id === target.id && item.type === "tool" ? { ...item, result } : item,
      );
    }, targetThreadId);
    
    const isCurrent = !isUnmounting && (!targetThreadId || targetThreadId === currentThreadId());
    if (isCurrent) {
      scrollToBottom();
    }
  };

  const addFiles = async (fileList: FileList | null) => {
    const files = Array.from(fileList || []);
    if (!files.length) {
      return;
    }

    let nextAttachments = [...attachments()];
    let totalSize = nextAttachments.reduce((sum, attachment) => sum + attachment.size, 0);

    for (const file of files) {
      if (nextAttachments.length >= MAX_ATTACHMENTS) {
        showToast(`Attach up to ${MAX_ATTACHMENTS} files.`, "warning");
        break;
      }

      const kind = attachmentKind(file);
      if (!kind) {
        showToast(`Unsupported file type: ${file.name}`, "warning");
        continue;
      }

      const maxSize = kind === "image" ? MAX_IMAGE_ATTACHMENT_BYTES : MAX_TEXT_ATTACHMENT_BYTES;
      if (file.size > maxSize) {
        showToast(`${file.name} exceeds ${formatBytes(maxSize)}.`, "warning");
        continue;
      }
      if (totalSize + file.size > MAX_TOTAL_ATTACHMENT_BYTES) {
        showToast(`Attached files exceed ${formatBytes(MAX_TOTAL_ATTACHMENT_BYTES)}.`, "warning");
        continue;
      }

      try {
        if (kind === "image") {
          nextAttachments = [
            ...nextAttachments,
            {
              id: nextAttachmentId++,
              name: file.name,
              mime_type: file.type || "image/png",
              size: file.size,
              kind,
              base64: await readFileAsBase64(file),
              preview_url: URL.createObjectURL(file),
            },
          ];
        } else {
          nextAttachments = [
            ...nextAttachments,
            {
              id: nextAttachmentId++,
              name: file.name,
              mime_type: file.type || "text/plain",
              size: file.size,
              kind,
              content: await file.text(),
            },
          ];
        }
        totalSize += file.size;
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : `Failed to read ${file.name}.`,
          "error",
        );
      }
    }

    setAttachments(nextAttachments);
    resetFileInput();
  };

  const buildPayload = (message: string, attachedFiles: ChatAttachmentPayload[]) => {
    const payload: ChatPayload = {
      message,
      user_id: "dashboard",
      agent_name: agentName(),
    };
    if (provider()) {
      payload.provider = provider();
    }
    if (model()) {
      payload.model = model();
    }
    const workspace = workspaceRef() || localWorkspaceRef(workingDir());
    if (workspace) {
      payload.workspace = workspace;
    }
    if (currentThreadId()) {
      payload.thread_id = currentThreadId();
    } else {
      payload.new_thread = true;
    }
    if (attachedFiles.length) {
      payload.attachments = attachedFiles;
    }
    return payload;
  };

  function handleEvent(
    event: Record<string, unknown>,
    assistantIdRef: { id: number | null },
    streamedRef: { received: boolean },
    streamThreadIdRef: { id: string; message: string }
  ) {
    if (event.type === "thread_created") {
      const threadId = String(event.thread_id || "");
      if (!threadId) {
        return;
      }
      
      const oldStreamTid = streamThreadIdRef.id;
      const isViewingPending = !currentThreadId() || currentThreadId() === oldStreamTid;
      
      if (isViewingPending) {
        loadedThreadId = threadId;
        setCurrentThreadId(threadId);
      }

      // Move persisted stream from pending key to real threadId
      if (oldStreamTid && oldStreamTid !== threadId && persistedStreams.has(oldStreamTid)) {
        const signals = persistedStreams.get(oldStreamTid)!;
        persistedStreams.delete(oldStreamTid);
        persistedStreams.set(threadId, signals);
      }
      streamThreadIdRef.id = threadId;
      
      if (currentStreamThreadId() === oldStreamTid || currentStreamThreadId() === threadId) {
        setCurrentStreamThreadId(threadId);
      }

      // Dispatch event to indicate the new thread is running
      if (oldStreamTid !== threadId) {
        window.dispatchEvent(
          new CustomEvent("kaka:thread-start-running", {
            detail: {
              threadId,
              title: streamThreadIdRef.message,
              workspace: workspaceRef() || localWorkspaceRef(workingDir()),
              agent_name: agentName(),
            },
          }),
        );
      }

      if (isViewingPending) {
        navigate(`/c/${encodeURIComponent(threadId)}`, { replace: true });
      }
      return;
    }
    if (event.type === "message") {
      const content = String(event.content || "");
      if (!content) {
        return;
      }
      if (assistantIdRef.id === null) {
        assistantIdRef.id = appendItem({ type: "message", role: "assistant", text: "" }, "bottom", streamThreadIdRef.id);
      }
      streamedRef.received = true;
      updateMessage(assistantIdRef.id, content, streamThreadIdRef.id);
      return;
    }
    if (event.type === "tool_call") {
      appendItem(
        createTranscriptTool({
          toolCallId: String(event.id || ""),
          name: String(event.name || "unknown"),
          args: event.args ?? null,
        }),
        "bottom",
        streamThreadIdRef.id
      );
      assistantIdRef.id = null;
      streamedRef.received = false;
      return;
    }
    if (event.type === "tool_result") {
      updateToolResult(
        String(event.tool_call_id || ""),
        String(event.name || "unknown"),
        event.content ?? null,
        streamThreadIdRef.id
      );
      return;
    }
    if (event.type === "error") {
      appendItem({
        type: "message",
        role: "error",
        text: String(event.content || event.message || "Chat failed"),
      }, "bottom", streamThreadIdRef.id);
      if (event.code === "recursion_limit_reached") {
        setRecursionLimitReached(true);
        if (streamThreadIdRef.id) {
          window.localStorage.setItem(`kaka:recursion-limit-reached:${streamThreadIdRef.id}`, "true");
        }
      }
      return;
    }
    if (event.type === "final") {
      if (streamedRef.received) {
        return;
      }
      const content = String(event.content || "");
      if (!content) {
        return;
      }
      if (assistantIdRef.id === null) {
        assistantIdRef.id = appendItem({ type: "message", role: "assistant", text: "" }, "bottom", streamThreadIdRef.id);
      }
      updateMessage(assistantIdRef.id, content, streamThreadIdRef.id);
    }
  }

  const sendMessage = async (resume = false) => {
    isUnmounting = false;
    cleanupStaleStreams();
    if (streaming()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    const selectedAttachments = attachments();
    const attachedFiles = selectedAttachments.map(toPayloadAttachment);
    const message = resume ? "" : (prompt().trim() || (
      selectedAttachments.length ? DEFAULT_ATTACHMENT_MESSAGE : ""
    ));
    if (!resume && !message && !selectedAttachments.length) {
      showToast("Enter a message or attach a file.", "warning");
      return;
    }
    if (threadLoading()) {
      showToast("Wait for the thread to load.", "warning");
      return;
    }
    if (backgroundTaskActive() || backgroundLive()) {
      showToast("Wait for the background task to finish.", "warning");
      return;
    }
    if (!agentName()) {
      showToast("No valid agent is available.", "error");
      return;
    }
    if (!workingDir().trim()) {
      showToast("Select a workspace before sending.", "warning");
      return;
    }

    setRecursionLimitReached(false);
    const activeTid = currentThreadId();
    if (activeTid) {
      window.localStorage.removeItem(`kaka:recursion-limit-reached:${activeTid}`);
    }

    const streamThreadIdRef = { id: currentThreadId() || `__pending__${Date.now()}`, message };
    
    // Register stream in persisted signals first so that appendItem goes directly to it
    getOrCreateStreamSignals(streamThreadIdRef.id, items());
    setCurrentStreamThreadId(streamThreadIdRef.id);

    if (!resume) {
      appendItem(
        {
          type: "message",
          role: "user",
          text: message,
          attachments: selectedAttachments.map(toTranscriptAttachment),
        },
        "turn-start",
        streamThreadIdRef.id
      );
      setPrompt("");
      clearAttachments(selectedAttachments);
    }
    const abortController = new AbortController();
    setController(abortController, streamThreadIdRef.id);
    setStreaming(true, streamThreadIdRef.id);

    const startTid = streamThreadIdRef.id;
    if (startTid && !startTid.startsWith("__pending__") && !resume) {
      window.dispatchEvent(
        new CustomEvent("kaka:thread-start-running", {
          detail: {
            threadId: startTid,
            title: message,
            workspace: workspaceRef() || localWorkspaceRef(workingDir()),
            agent_name: agentName(),
          },
        }),
      );
    }

    const assistantIdRef = { id: null as number | null };
    const streamedRef = { received: false };

    try {
      const payload = buildPayload(message, resume ? [] : attachedFiles);
      if (resume) {
        (payload as any).resume = true;
        payload.message = "";
      }
      const response = await fetch("/api/chat/events", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
        signal: abortController.signal,
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      if (!response.body) {
        throw new Error("Streaming response is not available.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) {
            continue;
          }
          handleEvent(JSON.parse(line) as Record<string, unknown>, assistantIdRef, streamedRef, streamThreadIdRef);
        }
        if (done) {
          break;
        }
      }
      if (buffer.trim()) {
        handleEvent(JSON.parse(buffer) as Record<string, unknown>, assistantIdRef, streamedRef, streamThreadIdRef);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        if (!isUnmounting) {
          showToast("Response stopped.", "warning");
        }
      } else {
        appendItem({
          type: "message",
          role: "error",
          text: err instanceof Error ? err.message : "Chat failed",
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
      const finishedTid = streamThreadIdRef.id;
      setStreaming(false, finishedTid);
      setController(null, finishedTid);
      
      // Transfer streamed items to local state before cleaning up persisted signals
      if (finishedTid) {
        const isViewing = currentStreamThreadId() === finishedTid;
        if (persistedStreams.has(finishedTid)) {
          if (isViewing && !isUnmounting) {
            setLocalItems(persistedStreams.get(finishedTid)!.items[0]());
          }
          cleanupStreamSignals(finishedTid);
        }
        if (isViewing) {
          setCurrentStreamThreadId(null);
        }
      }
      if (finishedTid) {
        window.dispatchEvent(
          new CustomEvent("kaka:thread-stop-running", {
            detail: { threadId: finishedTid },
          }),
        );
      }
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
    }
  };

  const reconnectStream = async (threadId: string) => {
    isUnmounting = false;
    cleanupStaleStreams();
    if (streaming()) {
      return;
    }

    // Find the last user message and trim all items after it
    const currentItems = items();
    const lastUserIndex = currentItems.map(item => item.type === "message" && item.role === "user").lastIndexOf(true);
    if (lastUserIndex !== -1) {
      setItems(currentItems.slice(0, lastUserIndex + 1));
    }

    const streamThreadIdRef = { id: threadId, message: "" };
    getOrCreateStreamSignals(streamThreadIdRef.id, items());
    setCurrentStreamThreadId(streamThreadIdRef.id);

    const abortController = new AbortController();
    setController(abortController, streamThreadIdRef.id);
    setStreaming(true, streamThreadIdRef.id);

    const assistantIdRef = { id: null as number | null };
    const streamedRef = { received: false };

    try {
      const response = await fetch("/api/chat/events/reconnect", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ thread_id: threadId }),
        signal: abortController.signal,
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      if (!response.body) {
        throw new Error("Streaming response is not available.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) {
            continue;
          }
          handleEvent(JSON.parse(line) as Record<string, unknown>, assistantIdRef, streamedRef, streamThreadIdRef);
        }
        if (done) {
          break;
        }
      }
      if (buffer.trim()) {
        handleEvent(JSON.parse(buffer) as Record<string, unknown>, assistantIdRef, streamedRef, streamThreadIdRef);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // Ignored
      } else {
        appendItem({
          type: "message",
          role: "error",
          text: err instanceof Error ? err.message : "Chat reconnection failed",
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
      const finishedTid = streamThreadIdRef.id;
      setStreaming(false, finishedTid);
      setController(null, finishedTid);

      if (finishedTid) {
        const isViewing = currentStreamThreadId() === finishedTid;
        if (persistedStreams.has(finishedTid)) {
          if (isViewing && !isUnmounting) {
            setLocalItems(persistedStreams.get(finishedTid)!.items[0]());
          }
          cleanupStreamSignals(finishedTid);
        }
        if (isViewing) {
          setCurrentStreamThreadId(null);
        }
      }
      if (finishedTid) {
        window.dispatchEvent(
          new CustomEvent("kaka:thread-stop-running", {
            detail: { threadId: finishedTid },
          }),
        );
      }
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
    }
  };

  const stopChat = () => controller()?.abort();
  const handleResume = () => {
    void sendMessage(true);
  };

  // Listen to centralized session events (event-driven sync)
  const handleSessionStartedOrUpdated = (event: Event) => {
    const customEvent = event as CustomEvent<ActiveSession>;
    const session = customEvent.detail;
    if (session && session.thread_id === currentThreadId()) {
      setActiveSession(session);
    }
  };

  const handleSessionStopped = (event: Event) => {
    const customEvent = event as CustomEvent<{ session_id: string; thread_id: string }>;
    const { thread_id } = customEvent.detail;
    if (thread_id === currentThreadId()) {
      setActiveSession(null);
      void loadThread(thread_id);
    }
  };

  // Fetch initial active session status from API once when viewing a thread (Fixes Issue 6)
  createEffect(() => {
    const threadId = currentThreadId();
    if (threadId) {
      const fetchInitialSession = async () => {
        try {
          const payload = await apiFetch<{ sessions: ActiveSession[] }>("/dashboard-api/sessions");
          const found = payload.sessions.find((s) => s.thread_id === threadId);
          if (threadId === currentThreadId()) {
            setActiveSession(found || null);
            if (found && !streaming()) {
              void reconnectStream(threadId);
            }
          }
        } catch (err) {
          console.error("Failed to fetch initial session details", err);
        }
      };
      void fetchInitialSession();
    } else {
      setActiveSession(null);
    }
  });

  const handleExternalAbort = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string }>;
    if (customEvent.detail.threadId === currentStreamThreadId() || customEvent.detail.threadId === currentThreadId()) {
      stopChat();
    }
  };

  const handleThreadStopRunningExternal = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string }>;
    const stoppedId = customEvent.detail.threadId;
    if (stoppedId === currentThreadId() && !streaming()) {
      void loadThread(stoppedId);
    }
  };

  onMount(() => {
    isUnmounting = false;
    cleanupStaleStreams();
    const savedExplorerOpen = window.localStorage.getItem(WORKSPACE_EXPLORER_OPEN_KEY);
    setWorkspaceExplorerOpen(savedExplorerOpen !== "closed");

    const savedExplorerWidth = Number(window.localStorage.getItem(WORKSPACE_EXPLORER_WIDTH_KEY));
    if (Number.isFinite(savedExplorerWidth) && savedExplorerWidth > 0) {
      setWorkspaceExplorerWidth(clampWorkspaceExplorerWidth(savedExplorerWidth));
    }

    window.addEventListener("kaka:thread-external-abort", handleExternalAbort);
    window.addEventListener("kaka:thread-stop-running", handleThreadStopRunningExternal);
    window.addEventListener("kaka:session-started", handleSessionStartedOrUpdated);
    window.addEventListener("kaka:session-updated", handleSessionStartedOrUpdated);
    window.addEventListener("kaka:session-stopped", handleSessionStopped);

    void load();
    void loadDefaultWorkspace();
  });
  onCleanup(() => {
    isUnmounting = true;
    // Only abort if there's no persisted stream (allows stream to survive navigation)
    const activeTid = currentStreamThreadId();
    if (!activeTid || !persistedStreams.has(activeTid)) {
      controller()?.abort();
    }
    window.removeEventListener("kaka:thread-external-abort", handleExternalAbort);
    window.removeEventListener("kaka:thread-stop-running", handleThreadStopRunningExternal);
    window.removeEventListener("kaka:session-started", handleSessionStartedOrUpdated);
    window.removeEventListener("kaka:session-updated", handleSessionStartedOrUpdated);
    window.removeEventListener("kaka:session-stopped", handleSessionStopped);
    endWorkspaceResize();
    closeBackgroundStream();
    attachments().forEach(revokeAttachmentPreview);
  });

  return (
    <AppShell
      title={currentThreadId() ? "Thread Chat" : "Agent Chat"}
      subtitle={
        <span class="row-wrap" style="gap: 8px; align-items: center; flex-wrap: wrap; display: inline-flex;">
          <span>{pageSubtitle()}</span>
          <Show when={threadBadgeVisible()}>
            <span class="row-wrap" style="gap: 6px; display: inline-flex; align-items: center; margin-left: 8px;">
              <Show when={isBackgroundThread()}>
                <span class="badge badge-info" style="font-size: 10px; padding: 2px 6px;">background</span>
              </Show>
              <Show when={backgroundTask()}>
                {(task) => <span class="badge" style="font-size: 10px; padding: 2px 6px;">{task().status}</span>}
              </Show>
              <Show when={backgroundLive()}>
                <span class="badge badge-info" style="font-size: 10px; padding: 2px 6px;">live</span>
              </Show>
              <Show when={backgroundSession()}>
                {(session) => <span class="badge" style="font-size: 10px; padding: 2px 6px;">{session().elapsed_display}</span>}
              </Show>
              <Show when={activeSession()}>
                {(session) => (
                  <>
                    <span class="badge badge-warning" style="font-size: 10px; padding: 2px 6px;">running</span>
                    <span class="badge" style="font-size: 10px; padding: 2px 6px;" title="Elapsed time">{session().elapsed_display}</span>
                  </>
                )}
              </Show>
            </span>
          </Show>
        </span>
      }
      actions={
        <>
          <button
            class={`btn btn-icon ${workspaceExplorerOpen() ? "active" : ""}`}
            type="button"
            onClick={toggleWorkspaceExplorer}
            title={workspaceExplorerOpen() ? "Hide workspace explorer" : "Show workspace explorer"}
            aria-label={
              workspaceExplorerOpen() ? "Hide workspace explorer" : "Show workspace explorer"
            }
            aria-pressed={workspaceExplorerOpen()}
          >
            <Show when={workspaceExplorerOpen()} fallback={<PanelRightOpen size={15} />}>
              <PanelRightClose size={15} />
            </Show>
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div
            ref={chatShellRef}
            class={`chat-shell chat-shell-resizable ${workspaceExplorerOpen() ? "workspace-open" : "workspace-closed"} ${workspaceExplorerResizing() ? "workspace-resizing" : ""}`}
            style={`--workspace-explorer-width: ${workspaceExplorerWidth()}px;`}
          >
            <section class="panel chat-panel">
              <Show when={threadError() || backgroundStreamError()}>
                <div class="thread-banner thread-banner-error">
                  <Show when={threadError()}>
                    <span>{threadError()}</span>
                  </Show>
                  <Show when={backgroundStreamError()}>
                    <span>{backgroundStreamError()}</span>
                  </Show>
                </div>
              </Show>
              <div class="transcript-container">
                <div class="transcript" ref={transcriptRef} onScroll={handleTranscriptScroll}>
                  <Show
                    when={items().length > 0}
                    fallback={
                      <Show
                        when={threadLoading()}
                        fallback={
                          <Show
                            when={!currentThreadId()}
                            fallback={<div class="empty">Send a message to continue this thread.</div>}
                          >
                            <div class="chat-workspace-empty">
                              <WorkspaceSelector
                                workingDir={workingDir()}
                                defaultWorkingDir={defaultWorkingDir()}
                                workspace={workspaceRef()}
                                locked={false}
                                disabled={conversationBusy()}
                                onResolved={setWorkspace}
                              />
                            </div>
                          </Show>
                        }
                      >
                        <div class="empty">Loading thread...</div>
                      </Show>
                    }
                  >
                    <For each={filteredItems()}>
                      {(item) => (
                        <TranscriptItemView
                          item={item}
                          itemId={item.id}
                          deferMermaid={streaming() || backgroundLive()}
                        />
                      )}
                    </For>
                    <Show when={turnAnchorSpacerHeight() > 0}>
                      <div
                        class="transcript-anchor-spacer"
                        style={`height: ${turnAnchorSpacerHeight()}px;`}
                        aria-hidden="true"
                      />
                    </Show>
                  </Show>
                </div>
                <Show when={!autoScroll()}>
                  <button
                    class="scroll-to-bottom-btn"
                    type="button"
                    onClick={handleScrollToBottomClick}
                    title="Scroll to bottom"
                    aria-label="Scroll to bottom"
                  >
                    <ArrowDown size={18} />
                  </button>
                </Show>
              </div>
              <div class="composer chat-composer">
                <input
                  ref={fileInputRef}
                  class="is-hidden"
                  type="file"
                  multiple
                  accept={ATTACHMENT_ACCEPT}
                  onChange={(event) => void addFiles(event.currentTarget.files)}
                />
                <Show when={currentTodos() && currentTodos()!.length > 0}>
                  <div class="chat-todos-box">
                    <div
                      class="chat-todos-header"
                      onClick={() => setTodosExpanded(!todosExpanded())}
                    >
                      <div class="chat-todos-header-left">
                        <span class="chat-todos-toggle-icon">
                          <Show when={todosExpanded()} fallback={<ChevronRight size={14} />}>
                            <ChevronDown size={14} />
                          </Show>
                        </span>

                        <Show
                          when={todosExpanded()}
                          fallback={
                            <div class="chat-todos-title">
                              <Show when={todoProgress().activeStatus === "completed"}>
                                <span class="todo-status-icon completed">
                                  <svg
                                    viewBox="0 0 24 24"
                                    width="14"
                                    height="14"
                                    stroke="currentColor"
                                    stroke-width="3"
                                    fill="none"
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                  >
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                  </svg>
                                </span>
                              </Show>
                              <Show when={todoProgress().activeStatus === "in_progress"}>
                                <span class="todo-status-icon in-progress">
                                  <span class="todo-status-dot"></span>
                                </span>
                              </Show>
                              <Show when={todoProgress().activeStatus === "pending"}>
                                <span class="todo-status-icon pending"></span>
                              </Show>

                              <span class="chat-todos-collapsed-text">
                                {todoProgress().activeText}
                              </span>
                              <span class="chat-todos-progress">
                                ({todoProgress().current}/{todoProgress().total})
                              </span>
                            </div>
                          }
                        >
                          <span class="chat-todos-collapsed-text" style="font-weight: 600;">
                            Todos ({todoProgress().current}/{todoProgress().total})
                          </span>
                        </Show>
                      </div>

                      <div class="chat-todos-header-right">
                        <svg
                          viewBox="0 0 24 24"
                          width="16"
                          height="16"
                          stroke="currentColor"
                          stroke-width="2"
                          fill="none"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        >
                          <line x1="8" y1="6" x2="21" y2="6"></line>
                          <line x1="8" y1="12" x2="21" y2="12"></line>
                          <line x1="8" y1="18" x2="21" y2="18"></line>
                          <path d="M3 6h.01"></path>
                          <path d="M3 12h.01"></path>
                          <path d="M3 18h.01"></path>
                        </svg>
                      </div>
                    </div>

                    <Show when={todosExpanded()}>
                      <div class="chat-todos-list">
                        <For each={currentTodos()}>
                          {(todo) => (
                            <div class={`chat-todo-item ${todo.status}`}>
                              <Show when={todo.status === "completed"}>
                                <span class="todo-status-icon completed">
                                  <svg
                                    viewBox="0 0 24 24"
                                    width="14"
                                    height="14"
                                    stroke="currentColor"
                                    stroke-width="3"
                                    fill="none"
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                  >
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                  </svg>
                                </span>
                              </Show>
                              <Show when={todo.status === "in_progress"}>
                                <span class="todo-status-icon in-progress">
                                  <span class="todo-status-dot"></span>
                                </span>
                              </Show>
                              <Show when={todo.status === "pending"}>
                                <span class="todo-status-icon pending"></span>
                              </Show>

                              <span>{todo.content}</span>
                            </div>
                          )}
                        </For>
                      </div>
                    </Show>
                  </div>
                </Show>
                <Show when={recursionLimitReached()}>
                  <div class="chat-recursion-warning">
                    <div class="chat-recursion-warning-left">
                      <span style="font-size: 16px;">⚠️</span>
                      <span>Agent đã đạt giới hạn bước xử lý mà chưa hoàn thành nhiệm vụ. Bạn có muốn tiếp tục chạy không?</span>
                    </div>
                    <button 
                      class="chat-recursion-warning-btn" 
                      type="button" 
                      onClick={handleResume} 
                    >
                      Tiếp tục chạy
                    </button>
                  </div>
                </Show>
                <textarea
                  ref={chatPromptRef}
                  class="chat-prompt-input"
                  rows={1}
                  value={prompt()}
                  disabled={inputDisabled()}
                  placeholder={
                    backgroundTaskActive()
                      ? "Background task is running..."
                      : workspaceMissing()
                        ? "Select a workspace before sending..."
                        : currentThreadId()
                          ? "Continue this thread..."
                          : "Ask Kaka to build features, fix bugs, or work on your code"
                  }
                  onInput={(event) => {
                    setPrompt(event.currentTarget.value);
                    resizeChatPromptInput();
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.ctrlKey && !event.shiftKey) {
                      event.preventDefault();
                      if (!composerDisabled()) {
                        void sendMessage();
                      }
                    }
                  }}
                />
                <Show when={attachments().length > 0}>
                  <div class="chat-attachment-list">
                    <For each={attachments()}>
                      {(attachment) => (
                        <div class="chat-attachment-chip">
                          <Show
                            when={attachment.kind === "image" && attachment.preview_url}
                            fallback={
                              <span class="chat-attachment-icon">
                                <Show
                                  when={attachment.kind === "image"}
                                  fallback={<FileText size={14} />}
                                >
                                  <ImageIcon size={14} />
                                </Show>
                              </span>
                            }
                          >
                            <img
                              class="chat-attachment-thumb"
                              src={attachment.preview_url}
                              alt=""
                            />
                          </Show>
                          <span class="chat-attachment-name">{attachment.name}</span>
                          <span class="chat-attachment-size">{formatBytes(attachment.size)}</span>
                          <button
                            class="chat-attachment-remove"
                            type="button"
                            onClick={() => removeAttachment(attachment.id)}
                            disabled={composerDisabled()}
                            title="Remove file"
                            aria-label={`Remove ${attachment.name}`}
                          >
                            <X size={13} />
                          </button>
                        </div>
                      )}
                    </For>
                  </div>
                </Show>
                <div class="chat-composer-toolbar">
                  <div class="chat-composer-actions">
                    <SelectControl
                      class="chat-agent-picker"
                      value={agentName()}
                      options={agentOptions()}
                      disabled={composerDisabled()}
                      onChange={setAgentName}
                      ariaLabel="Agent"
                      title={selectedCard()?.description || "Select agent"}
                      icon={<Bot size={14} />}
                    />
                    <button
                      class="chat-composer-icon"
                      type="button"
                      onClick={() => fileInputRef?.click()}
                      disabled={composerDisabled()}
                      title="Attach files"
                      aria-label="Attach files"
                    >
                      <Plus size={18} />
                    </button>
                    <button
                      class={`chat-composer-icon ${composerOptionsOpen() ? "active" : ""}`}
                      type="button"
                      onClick={() => setComposerOptionsOpen((current) => !current)}
                      disabled={workspaceMissing()}
                      title="Run settings"
                      aria-label="Run settings"
                      aria-expanded={composerOptionsOpen()}
                    >
                      <MoreHorizontal size={18} />
                    </button>
                  </div>
                  <Show
                    when={streaming()}
                    fallback={
                      <button
                        class="chat-composer-icon"
                        type="button"
                        onClick={() => sendMessage()}
                        disabled={composerDisabled() || (!prompt().trim() && !attachments().length)}
                        title="Send"
                        aria-label="Send"
                      >
                        <Send size={16} />
                      </button>
                    }
                  >
                    <button
                      class="chat-composer-icon chat-composer-stop"
                      type="button"
                      onClick={stopChat}
                      title="Stop"
                      aria-label="Stop"
                    >
                      <Square size={15} />
                    </button>
                  </Show>
                </div>
                <Show when={composerOptionsOpen()}>
                  <div class="chat-composer-options">
                    <div class="field">
                      <label>Provider / Model</label>
                      <ModelPicker
                        catalogs={payload.model_catalogs}
                        providerNames={payload.provider_names}
                        defaultProvider={payload.default_provider}
                        defaultModel={payload.default_model}
                        provider={provider()}
                        model={model()}
                        disabled={composerDisabled()}
                        dropdownPlacement="top"
                        resolveDefault={true}
                        onChange={(nextProvider, nextModel) => {
                          setProvider(nextProvider);
                          setModel(nextModel);
                        }}
                      />
                    </div>
                    <div class="chat-agent-summary">
                      <div class="row">
                        <Bot size={14} />
                        <strong>{selectedCard()?.display_name || selectedCard()?.name || "No agent"}</strong>
                      </div>
                      <p class="hint">{selectedCard()?.description || "No description."}</p>
                      <div class="chips">
                        <span class="chip">{selectedCard()?.graph_type || "default"}</span>
                        <span class="chip">
                          {(() => {
                            const activeProvider = provider() || selectedCard()?.provider || "default";
                            const activeModel = model() || selectedCard()?.model || "";
                            const resolvedProv = activeProvider === "default" ? payload.default_provider : activeProvider;
                            const catalog = payload.model_catalogs.find((c) => c.provider === resolvedProv);
                            const resolvedMod = (activeModel === "" || activeModel === "provider default")
                              ? (activeProvider === "default" ? payload.default_model : (catalog?.default_model || "default"))
                              : activeModel;
                            return `${resolvedProv}/${resolvedMod}`;
                          })()}
                        </span>
                      </div>
                    </div>
                  </div>
                </Show>
              </div>
            </section>
            <div class="workspace-drawer" aria-hidden={!workspaceExplorerOpen()}>
              <button
                class="workspace-resize-handle"
                type="button"
                onPointerDown={startWorkspaceResize}
                title="Resize workspace explorer"
                aria-label="Resize workspace explorer"
              >
                <GripVertical size={15} />
              </button>
              <WorkspaceExplorer
                threadId={currentThreadId()}
                workingDir={workingDir()}
                disabled={conversationBusy() || !workingDir().trim() || !currentThreadId() || workspaceLocked()}
                onWorkingDirChange={setWorkspace}
              />
            </div>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
