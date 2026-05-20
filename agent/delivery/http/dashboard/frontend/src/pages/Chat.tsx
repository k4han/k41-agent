import { useNavigate, useSearchParams } from "@solidjs/router";
import {
  Bot,
  FileText,
  GripVertical,
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
import { apiFetch, readError } from "@/lib/api";
import {
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import type { TranscriptAttachment, TranscriptItem } from "@/components/Transcript";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import type { ActiveSession, AgentCard, AgentsPayload, BackgroundTask } from "@/types";

type ChatTranscriptItem = TranscriptItem & { id: number; key?: string };
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
  working_dir?: string;
  provider?: string;
  model?: string;
  thread_id?: string;
  new_thread?: boolean;
  attachments?: ChatAttachmentPayload[];
};
type BackgroundTaskSnapshot = ThreadMessagesPayload & {
  task?: BackgroundTask | null;
  active_session?: ActiveSession | null;
};
type DefaultWorkspacePayload = {
  working_dir: string;
};

const MAX_ATTACHMENTS = 5;
const MAX_TEXT_ATTACHMENT_BYTES = 100 * 1024;
const MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024;
const DEFAULT_ATTACHMENT_MESSAGE = "Please review the attached file(s).";
const WORKSPACE_STORAGE_KEY = "kaka-dashboard-working-dir";
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

export function ChatPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
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
  const [defaultWorkingDir, setDefaultWorkingDir] = createSignal("");
  const [workspaceExplorerOpen, setWorkspaceExplorerOpen] = createSignal(true);
  const [workspaceExplorerWidth, setWorkspaceExplorerWidth] = createSignal(
    WORKSPACE_EXPLORER_DEFAULT_WIDTH,
  );
  const [workspaceExplorerResizing, setWorkspaceExplorerResizing] = createSignal(false);
  const [prompt, setPrompt] = createSignal("");
  const [items, setItems] = createSignal<ChatTranscriptItem[]>([]);
  const [streaming, setStreaming] = createSignal(false);
  const [controller, setController] = createSignal<AbortController | null>(null);
  const [backgroundTask, setBackgroundTask] = createSignal<BackgroundTask | null>(null);
  const [backgroundLive, setBackgroundLive] = createSignal(false);
  const [backgroundStreamError, setBackgroundStreamError] = createSignal("");
  const [backgroundSession, setBackgroundSession] = createSignal<ActiveSession | null>(null);
  const [composerOptionsOpen, setComposerOptionsOpen] = createSignal(false);
  const [attachments, setAttachments] = createSignal<PendingAttachment[]>([]);
  const { showToast } = useToast();
  let transcriptRef: HTMLDivElement | undefined;
  let chatShellRef: HTMLDivElement | undefined;
  let fileInputRef: HTMLInputElement | undefined;
  let loadedThreadId: string | null = null;
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

  const setWorkspace = (value: string) => {
    const nextValue = value.trim();
    setWorkingDir(nextValue);
    if (nextValue) {
      window.localStorage.setItem(WORKSPACE_STORAGE_KEY, nextValue);
    } else {
      window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
    }
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
      const fallback = payload.working_dir || "";
      setDefaultWorkingDir(fallback);
      if (!currentThreadId() && !workingDir()) {
        setWorkspace(window.localStorage.getItem(WORKSPACE_STORAGE_KEY) || fallback);
      }
    } catch {
      if (!currentThreadId() && !workingDir()) {
        setWorkspace(window.localStorage.getItem(WORKSPACE_STORAGE_KEY) || "");
      }
    }
  };

  const validCards = createMemo(() => (data()?.cards || []).filter((card) => card.valid));
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
      ? `Continue thread ${truncateText(currentThreadId(), 88)}`
      : "Stream an agent response with visible tool calls."
  ));
  const isBackgroundThread = createMemo(() => threadData()?.kind === "background");
  const backgroundTaskActive = createMemo(() => {
    const task = backgroundTask();
    return Boolean(task && ACTIVE_TASK_STATUSES.has(task.status));
  });
  const composerDisabled = createMemo(() => (
    streaming() || threadLoading() || backgroundTaskActive() || backgroundLive()
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

  const scrollToBottom = () => {
    window.setTimeout(() => {
      if (transcriptRef) {
        transcriptRef.scrollTop = transcriptRef.scrollHeight;
      }
    }, 0);
  };

  const appendItem = (item: TranscriptItem): number => {
    const id = nextItemId;
    nextItemId += 1;
    setItems((current) => [...current, { ...item, id } as ChatTranscriptItem]);
    scrollToBottom();
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
    setThreadData(payload);
    setCurrentThreadId(payload.thread_id);
    setWorkspace(
      payload.working_dir
      || window.localStorage.getItem(WORKSPACE_STORAGE_KEY)
      || defaultWorkingDir(),
    );
    setItems(
      toThreadTranscript(payload.messages).map((item) => ({
        ...item,
        id: nextItemId++,
      })),
    );
    scrollToBottom();
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
      working_dir: snapshot.working_dir,
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
    setThreadLoading(true);
    setItems([]);
    closeBackgroundStream();
    setBackgroundTask(null);

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
      }
    }
  };

  createEffect(() => {
    const threadId = String(searchParams.thread || "");
    if (threadId === loadedThreadId) {
      return;
    }

    loadedThreadId = threadId;
    threadLoadRequestId += 1;
    clearAllAttachments();

    if (!threadId) {
      setCurrentThreadId("");
      setThreadData(undefined);
      setThreadError("");
      setThreadLoading(false);
      setItems([]);
      setWorkspace(window.localStorage.getItem(WORKSPACE_STORAGE_KEY) || defaultWorkingDir());
      closeBackgroundStream();
      setBackgroundTask(null);
      return;
    }

    void loadThread(threadId);
  });

  const updateMessage = (id: number, chunk: string) => {
    setItems((current) =>
      current.map((item) =>
        item.id === id && item.type === "message"
          ? { ...item, text: item.text + chunk }
          : item,
      ),
    );
    scrollToBottom();
  };

  const updateToolResult = (toolCallId: string, name: string, result: unknown) => {
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
    });
    scrollToBottom();
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
    if (workingDir().trim()) {
      payload.working_dir = workingDir().trim();
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
  ) {
    if (event.type === "thread_created") {
      const threadId = String(event.thread_id || "");
      if (!threadId) {
        return;
      }
      loadedThreadId = threadId;
      setCurrentThreadId(threadId);
      navigate(`/chat?thread=${encodeURIComponent(threadId)}`, { replace: true });
      return;
    }
    if (event.type === "message") {
      const content = String(event.content || "");
      if (!content) {
        return;
      }
      if (assistantIdRef.id === null) {
        assistantIdRef.id = appendItem({ type: "message", role: "assistant", text: "" });
      }
      streamedRef.received = true;
      updateMessage(assistantIdRef.id, content);
      return;
    }
    if (event.type === "tool_call") {
      appendItem(
        createTranscriptTool({
          toolCallId: String(event.id || ""),
          name: String(event.name || "unknown"),
          args: event.args ?? null,
        }),
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
      );
      return;
    }
    if (event.type === "error") {
      appendItem({
        type: "message",
        role: "error",
        text: String(event.content || event.message || "Chat failed"),
      });
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
        assistantIdRef.id = appendItem({ type: "message", role: "assistant", text: "" });
      }
      updateMessage(assistantIdRef.id, content);
    }
  }

  const sendMessage = async () => {
    const selectedAttachments = attachments();
    const attachedFiles = selectedAttachments.map(toPayloadAttachment);
    const message = prompt().trim() || (
      selectedAttachments.length ? DEFAULT_ATTACHMENT_MESSAGE : ""
    );
    if (!message && !selectedAttachments.length) {
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

    appendItem({
      type: "message",
      role: "user",
      text: message,
      attachments: selectedAttachments.map(toTranscriptAttachment),
    });
    setPrompt("");
    clearAttachments(selectedAttachments);
    const abortController = new AbortController();
    setController(abortController);
    setStreaming(true);
    const assistantIdRef = { id: null as number | null };
    const streamedRef = { received: false };

    try {
      const response = await fetch("/api/chat/events", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(buildPayload(message, attachedFiles)),
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
          handleEvent(JSON.parse(line) as Record<string, unknown>, assistantIdRef, streamedRef);
        }
        if (done) {
          break;
        }
      }
      if (buffer.trim()) {
        handleEvent(JSON.parse(buffer) as Record<string, unknown>, assistantIdRef, streamedRef);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        showToast("Response stopped.", "warning");
      } else {
        appendItem({
          type: "message",
          role: "error",
          text: err instanceof Error ? err.message : "Chat failed",
        });
      }
    } finally {
      setStreaming(false);
      setController(null);
      if (currentThreadId()) {
        window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
      }
    }
  };

  const stopChat = () => controller()?.abort();

  onMount(() => {
    const savedExplorerOpen = window.localStorage.getItem(WORKSPACE_EXPLORER_OPEN_KEY);
    setWorkspaceExplorerOpen(savedExplorerOpen !== "closed");

    const savedExplorerWidth = Number(window.localStorage.getItem(WORKSPACE_EXPLORER_WIDTH_KEY));
    if (Number.isFinite(savedExplorerWidth) && savedExplorerWidth > 0) {
      setWorkspaceExplorerWidth(clampWorkspaceExplorerWidth(savedExplorerWidth));
    }

    void load();
    void loadDefaultWorkspace();
  });
  onCleanup(() => {
    controller()?.abort();
    endWorkspaceResize();
    closeBackgroundStream();
    attachments().forEach(revokeAttachmentPreview);
  });

  return (
    <AppShell
      title={currentThreadId() ? "Thread Chat" : "Agent Chat"}
      subtitle={pageSubtitle()}
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
          <button class="btn" type="button" onClick={load}>
            <RefreshCw size={14} />
            Refresh Options
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
              <Show when={currentThreadId() || threadError()}>
                <div class={`thread-banner ${threadError() ? "thread-banner-error" : ""}`}>
                  <div class="row-wrap">
                    <span class="badge">{threadData()?.platform || "thread"}</span>
                    <Show when={isBackgroundThread()}>
                      <span class="badge badge-info">background</span>
                    </Show>
                    <Show when={backgroundTask()}>
                      {(task) => <span class="badge">{task().status}</span>}
                    </Show>
                    <Show when={backgroundLive()}>
                      <span class="badge badge-info">live</span>
                    </Show>
                    <Show when={backgroundSession()}>
                      {(session) => <span class="badge">{session().elapsed_display}</span>}
                    </Show>
                    <span class="mono">{truncateText(currentThreadId(), 84)}</span>
                  </div>
                  <Show when={threadError()}>
                    <span>{threadError()}</span>
                  </Show>
                  <Show when={backgroundStreamError()}>
                    <span>{backgroundStreamError()}</span>
                  </Show>
                </div>
              </Show>
              <div class="transcript" ref={transcriptRef}>
                <Show
                  when={items().length > 0}
                  fallback={
                    <Show
                      when={threadLoading()}
                      fallback={<div class="empty">Send a message to start a conversation.</div>}
                    >
                      <div class="empty">Loading thread...</div>
                    </Show>
                  }
                >
                  <For each={items()}>
                    {(item) => <TranscriptItemView item={item} />}
                  </For>
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
                <textarea
                  class="chat-prompt-input"
                  rows={4}
                  value={prompt()}
                  disabled={composerDisabled()}
                  placeholder={
                    backgroundTaskActive()
                      ? "Background task is running..."
                      : currentThreadId()
                        ? "Continue this thread..."
                        : "Ask Kaka to build features, fix bugs, or work on your code"
                  }
                  onInput={(event) => setPrompt(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.ctrlKey && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
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
                      disabled={composerDisabled()}
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
                        onClick={sendMessage}
                        disabled={threadLoading() || backgroundTaskActive() || backgroundLive() || (!prompt().trim() && !attachments().length)}
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
                        provider={provider()}
                        model={model()}
                        disabled={composerDisabled()}
                        dropdownPlacement="top"
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
                        <span class="chip">{provider() || selectedCard()?.provider || "default"}</span>
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
                disabled={composerDisabled()}
                onWorkingDirChange={setWorkspace}
              />
            </div>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
