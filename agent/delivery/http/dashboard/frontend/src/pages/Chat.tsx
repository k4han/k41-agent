import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import {
  Menu,
  PanelRightClose,
  PanelRightOpen,
  X,
} from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { ChatComposer } from "@/components/ChatComposer";
import { Dialog } from "@/components/Dialog";
import { Markdown } from "@/components/Markdown";
import { ChatThreadBadges } from "@/components/ChatThreadBadges";
import { ChatTranscript } from "@/components/ChatTranscript";
import { PLAN_MODE_TOOL_NAME, createTranscriptUserInputRequest, type TranscriptAttachment, type TranscriptRole, type TranscriptUserInputRequest } from "@/components/Transcript";
import type { UserInputRequestSubmitPayload } from "@/components/UserInputRequestCard";
import type { WorkspaceSelectionDraft } from "@/components/WorkspaceSelector";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import type { TodoProgress } from "@/components/ChatTodos";
import { apiFetch, fetchWithCsrf, postJson, readError } from "@/lib/api";
import { fetchAgentChatOptions } from "@/lib/agents";
import {
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import {
  localWorkspaceRef,
  sandboxWorkspaceRef,
  resolveWorkspaceWorkingDir,
  workspaceExecution,
} from "@/lib/workspace";
import type {
  ActiveSession,
  AgentCard,
  AgentChatPayload,
  ModelOption,
  SandboxBackendKey,
  WorkspaceRef,
} from "@/types";
import { isSandboxBackend } from "@/types";
import {
  persistedStreams,
  getOrCreateStreamSignals,
  cleanupStreamSignals,
  cleanupStaleStreams,
  allocItemId,
} from "@/lib/chatStreamStore";
import {
  toTranscriptAttachment,
  toPayloadAttachment,
} from "@/lib/chatAttachments";
import {
  handleStreamEvent,
  readNDJSONStream,
  type StreamCallbacks,
  type AssistantIdRef,
  type StreamedRef,
  type StreamThreadIdRef,
} from "@/lib/chatStreamHandler";
import { CUSTOM_DOM_EVENTS, recursionLimitStorageKey } from "@/lib/eventConstants";
import { API_PATHS } from "@/lib/endpoints";
import { GENERATE_IMAGE_TOOL_NAME } from "@/lib/generatedImages";
import {
  type ChatAttachmentPayload,
  type ChatPayload,
  type DefaultWorkspacePayload,
  type ChatResumePayload,
  type WorkspaceResolvePayload,
  ATTACHMENT_ACCEPT,
  DEFAULT_ATTACHMENT_MESSAGE,
} from "@/lib/chatTypes";
import { ACTIVE_TASK_STATUSES, DRAWER_MEDIA_QUERY, MOBILE_MEDIA_QUERY } from "@/lib/uiConstants";
import { createMediaQuery } from "@/lib/useMediaQuery";
import { useChatScroll } from "@/lib/useChatScroll";
import { useChatStreams } from "@/lib/useChatStreams";
import { useWorkspaceExplorer } from "@/lib/useWorkspaceExplorer";
import { useChatAttachments } from "@/lib/useChatAttachments";
import { useContextWindow } from "@/lib/useContextWindow";
import { useBackgroundStream } from "@/lib/useBackgroundStream";
import { ASK_USER_TOOL_NAME, normalizeUserInputRequest } from "@/lib/userInputRequest";
import {
  INITIALIZING_ENVIRONMENT_TEXT,
  THINKING_TEXT,
} from "@/lib/chatStatus";

const DAYTONA_STATUS_STARTED = "started";

type WorkspaceResolveRequest = {
  kind: string;
  thread_id: string | null;
  repository_id?: number;
  backend?: string;
  workspace?: WorkspaceRef | null;
  locator?: string | null;
};

function workspaceSelectionReady(selection: WorkspaceSelectionDraft | null): boolean {
  if (!selection) {
    return false;
  }
  if (selection.source === "temp") {
    return true;
  }
  if (selection.source === "path") {
    return Boolean(selection.localPath.trim());
  }
  if (selection.source === "github") {
    return selection.repositoryId !== null;
  }
  return isSandboxBackend(selection.backend);
}

function workspaceSelectionLocator(selection: WorkspaceSelectionDraft): string {
  if (selection.source === "temp") {
    return "";
  }
  if (selection.source === "path") {
    return selection.localPath.trim();
  }
  if (selection.source === "github") {
    return selection.repositoryFullName || selection.label;
  }
  return selection.sandboxId || selection.label;
}

function workspaceEnvironmentKey(workspace: WorkspaceRef | null | undefined): string {
  if (!workspace) {
    return "";
  }
  const root = typeof workspace.metadata?.root === "string"
    ? workspace.metadata.root.trim()
    : "";
  return `${workspace.backend}:${workspace.locator}:${root}`;
}

function workspaceNeedsEnvironmentInitialization(
  workspace: WorkspaceRef | null | undefined,
  initializedWorkspaces: Set<string>,
): boolean {
  if (!workspace) {
    return false;
  }
  if (!isSandboxBackend(workspace.backend)) {
    return false;
  }

  const key = workspaceEnvironmentKey(workspace);

  if (workspace.backend === "daytona") {
    const status = typeof workspace.metadata?.status === "string"
      ? workspace.metadata.status.trim().toLowerCase()
      : "";
    if (status === DAYTONA_STATUS_STARTED) {
      return false;
    }
    return !initializedWorkspaces.has(key);
  }

  return !initializedWorkspaces.has(key);
}

export function ChatPage() {
  const navigate = useNavigate();
  const params = useParams<{ threadId?: string }>();
  const [searchParams] = useSearchParams();
  const routeThreadId = () => (params.threadId ? decodeURIComponent(params.threadId) : "");

  const { showToast } = useToast();

  const [data, setData] = createSignal<AgentChatPayload>();
  const [error, setError] = createSignal("");
  const [threadData, setThreadData] = createSignal<ThreadMessagesPayload>();
  const [threadError, setThreadError] = createSignal("");
  const [threadLoading, setThreadLoading] = createSignal(false);
  const [currentThreadId, setCurrentThreadId] = createSignal("");
  const [activeCheckpointId, setActiveCheckpointId] = createSignal("");
  const [threadTitleOverride, setThreadTitleOverride] = createSignal("");
  const isDrawerActive = createMediaQuery(DRAWER_MEDIA_QUERY);
  const isMobileViewport = createMediaQuery(MOBILE_MEDIA_QUERY);

  const [agentName, setAgentName] = createSignal("");
  const [provider, setProvider] = createSignal("default");
  const [model, setModel] = createSignal("");
  const [workingDir, setWorkingDir] = createSignal("");
  const [workspaceRef, setWorkspaceRef] = createSignal<WorkspaceRef | null>(null);
  const [workspaceSelection, setWorkspaceSelection] = createSignal<WorkspaceSelectionDraft | null>(null);
  const [defaultWorkingDir, setDefaultWorkingDir] = createSignal("");
  const [prompt, setPrompt] = createSignal("");
  const [todosExpanded, setTodosExpanded] = createSignal(true);
  const [recursionLimitReached, setRecursionLimitReached] = createSignal(false);
  const [activeSession, setActiveSession] = createSignal<ActiveSession | null>(null);
  const [initializedWorkspaces, setInitializedWorkspaces] = createSignal(new Set<string>());
  const [viewingMessage, setViewingMessage] = createSignal<{ text: string; role: TranscriptRole; attachments?: TranscriptAttachment[] } | null>(null);

  let transcriptRef: HTMLDivElement | undefined;
  let chatShellRef: HTMLDivElement | undefined;
  let chatPromptRef: HTMLTextAreaElement | undefined;

  const handleSelectPrompt = (promptText: string) => {
    setPrompt(promptText);
    chatPromptRef?.focus();
  };
  let loadedThreadId: string | null = null;
  let isUnmounting = false;
  let threadLoadRequestId = 0;
  let latestLocalStreamFinish: { threadId: string; finishedAt: number } | null = null;

  const scroll = useChatScroll(() => transcriptRef);
  const {
    autoScroll,
    handleTranscriptScroll,
    handleScrollToBottomClick,
    turnAnchorSpacerHeight,
  } = scroll;

  const explorer = useWorkspaceExplorer(() => chatShellRef);

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

  const modelSelectionAvailable = (nextProvider: string, nextModel: string): boolean => {
    const payload = data();
    if (!payload) {
      return false;
    }
    const providerName = (nextProvider || "default").trim();
    const resolvedProvider = providerName === "default"
      ? payload.default_provider
      : providerName;
    if (!resolvedProvider) {
      return providerName === "default";
    }
    const catalog = payload.model_catalogs?.find((item) => item.provider === resolvedProvider);
    const providerKnown = providerName === "default"
      || payload.provider_names.includes(resolvedProvider)
      || Boolean(catalog);
    if (!providerKnown) {
      return false;
    }
    const modelName = (nextModel || "").trim();
    if (!modelName || modelName === "provider default") {
      return true;
    }
    return Boolean(
      catalog?.default_model === modelName
      || catalog?.models?.some((item) => item.id === modelName),
    );
  };

  const fallbackModelSelection = (card: AgentCard): { provider: string; model: string } => ({
    provider: card.provider || "default",
    model: card.model || "",
  });

  const restoredModelSelection = (
    thread: ThreadMessagesPayload,
    card: AgentCard,
  ): { provider: string; model: string } => {
    const threadProvider = String(thread.provider || "").trim();
    const threadModel = String(thread.model || "").trim();
    if (threadProvider && modelSelectionAvailable(threadProvider, threadModel)) {
      return { provider: threadProvider, model: threadModel };
    }
    const cardSelection = fallbackModelSelection(card);
    if (modelSelectionAvailable(cardSelection.provider, cardSelection.model)) {
      return cardSelection;
    }
    return { provider: "default", model: "" };
  };

  const selectedModelOption = createMemo<ModelOption | undefined>(() => {
    const payload = data();
    if (!payload) return undefined;
    const card = selectedCard();
    const activeProvider = provider() || card?.provider || "default";
    const activeModel = model() || card?.model || "";
    const resolvedProv = activeProvider === "default" ? payload.default_provider : activeProvider;
    const catalog = payload.model_catalogs?.find((c) => c.provider === resolvedProv);
    const resolvedMod = (activeModel === "" || activeModel === "provider default")
      ? (activeProvider === "default" ? payload.default_model : (catalog?.default_model || "default"))
      : activeModel;
    return catalog?.models?.find((m) => m.id === resolvedMod);
  });

  const modelSupportsImage = createMemo(() => {
    const option = selectedModelOption();
    if (!option || option.input_types == null) return true;
    return option.input_types.includes("image");
  });

  const attachmentAccept = createMemo(() =>
    modelSupportsImage()
      ? ATTACHMENT_ACCEPT
      : ATTACHMENT_ACCEPT.split(",")
          .filter((entry) => entry.trim() !== "image/*")
          .join(","),
  );

  const streams = useChatStreams({
    scroll,
    getCurrentThreadId: currentThreadId,
    getIsUnmounting: () => isUnmounting,
  });
  const {
    items,
    setItems,
    streaming,
    setStreaming,
    controller,
    setController,
    currentStreamThreadId,
    setCurrentStreamThreadId,
    setLocalItems,
    appendItem,
    updateMessage,
    replaceMessage,
    removeItem,
    updateToolResult,
    updatePlanReview,
    updatePlanReviewResult,
    updateUserInputRequest,
    updateUserInputRequestResult,
    flushPendingMessageChunks,
  } = streams;

  const attach = useChatAttachments({
    getModelSupportsImage: modelSupportsImage,
    showToast,
  });
  const { attachments, addFiles, addTextContent, removeAttachment, clearAttachments, clearAllAttachments } = attach;

  const { contextWindowData, refreshThreadUsage, updateContextTokens } = useContextWindow({
    getCurrentThreadId: currentThreadId,
    getStreaming: streaming,
    getSelectedCard: selectedCard,
    getData: data,
    getProvider: provider,
    getModel: model,
    getAttachments: attachments,
    getItems: items,
  });

  const [compacting, setCompacting] = createSignal(false);

  const filteredItems = createMemo(() =>
    items().filter(
      (item) =>
        !(
          item.type === "tool" &&
          (
            item.name === "write_todos"
            || item.name === PLAN_MODE_TOOL_NAME
            || item.name === ASK_USER_TOOL_NAME
            || item.name === GENERATE_IMAGE_TOOL_NAME
          )
        ) && item.type !== "user_input_request",
    )
  );

  const pendingUserInputRequest = createMemo<TranscriptUserInputRequest | null>(() => {
    const allItems = items();
    for (let index = allItems.length - 1; index >= 0; index -= 1) {
      const item = allItems[index];
      if (item.type === "user_input_request" && item.status === "pending") {
        return item;
      }
    }
    return null;
  });

  const pendingPlanReview = createMemo(() => {
    const allItems = items();
    for (let index = allItems.length - 1; index >= 0; index -= 1) {
      const item = allItems[index];
      if (item.type === "plan_review" && item.status === "pending") {
        return item;
      }
    }
    return null;
  });

  const hasPendingInterrupt = createMemo(() => (
    Boolean(pendingUserInputRequest()) || Boolean(pendingPlanReview())
  ));

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

  const todoProgress = createMemo<TodoProgress>(() => {
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

    const activeStatus = (activeIdx !== -1 ? list[activeIdx].status : "completed") as "completed" | "pending" | "in_progress";
    return { current, total, activeText, activeStatus };
  });

  const load = async () => {
    setError("");
    try {
      setData(await fetchAgentChatOptions());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chat options");
    }
  };

  const setWorkspace = (value: WorkspaceRef | string | null) => {
    setWorkspaceSelection(null);
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

    setWorkingDir(resolveWorkspaceWorkingDir(value));
    setWorkspaceRef(value);
  };

  const setWorkspaceDraft = (selection: WorkspaceSelectionDraft) => {
    setWorkspaceSelection(selection);
    setWorkspaceRef(null);
    setWorkingDir(workspaceSelectionLocator(selection));
  };

  const buildWorkspaceResolvePayload = (
    selection: WorkspaceSelectionDraft,
  ): WorkspaceResolveRequest => {
    const threadId = currentThreadId() || null;
    if (selection.source === "github") {
      return {
        kind: "github",
        thread_id: threadId,
        repository_id: selection.repositoryId ?? undefined,
        backend: selection.backend,
        workspace:
          isSandboxBackend(selection.backend)
            ? sandboxWorkspaceRef(selection.backend as SandboxBackendKey, selection.sandboxId)
            : localWorkspaceRef(selection.localPath),
        locator:
          isSandboxBackend(selection.backend)
            ? selection.sandboxId || null
            : selection.localPath || null,
      };
    }
    if (!isSandboxBackend(selection.backend)) {
      return {
        kind: "local",
        thread_id: threadId,
        workspace: localWorkspaceRef(selection.localPath),
      };
    }
    return {
      kind: selection.backend,
      thread_id: threadId,
      workspace: sandboxWorkspaceRef(selection.backend as SandboxBackendKey, selection.sandboxId),
      locator: selection.sandboxId || null,
    };
  };

  const resolveWorkspaceForSend = async (): Promise<WorkspaceRef | null> => {
    const currentWorkspace = workspaceRef();
    if (currentWorkspace) {
      return currentWorkspace;
    }

    const selection = workspaceSelection();
    if (selection?.source === "temp") {
      // Explicitly opted into a temporary workspace. The backend provisions a
      // hidden temporary workspace for the thread automatically.
      return null;
    }
    let payload: WorkspaceResolveRequest | null = null;
    if (workspaceSelectionReady(selection)) {
      payload = buildWorkspaceResolvePayload(selection!);
    } else {
      const localWorkspace = localWorkspaceRef(workingDir());
      if (localWorkspace) {
        payload = {
          kind: "local",
          thread_id: currentThreadId() || null,
          workspace: localWorkspace,
        };
      }
    }
    if (!payload) {
      // No explicit workspace chosen. The backend provisions a hidden
      // temporary workspace for the thread automatically.
      return null;
    }

    const response = await postJson<WorkspaceResolvePayload>(
      "/dashboard-api/workspace/resolve",
      payload,
    );
    const execution = workspaceExecution(response.workspace);
    if (!execution) {
      throw new Error("Resolved workspace is missing its execution reference.");
    }
    setWorkspace(execution);
    return execution;
  };

  const loadDefaultWorkspace = async () => {
    try {
      const payload = await apiFetch<DefaultWorkspacePayload>("/dashboard-api/workspace/default");
      const execution = workspaceExecution(payload.workspace);
      setDefaultWorkingDir(execution?.locator || "");
    } catch {
      setDefaultWorkingDir("");
    }
  };

  const applyThreadPayload = (payload: ThreadMessagesPayload) => {
    scroll.clearTurnAnchor();
    setThreadData(payload);
    setThreadTitleOverride(payload.title || "");
    setCurrentThreadId(payload.thread_id);
    setActiveCheckpointId(payload.active_checkpoint_id || "");
    setWorkspace(workspaceExecution(payload.workspace) || null);
    if (!persistedStreams.has(payload.thread_id)) {
      setItems(
        [...toThreadTranscript(payload.messages, payload.thread_id), ...(payload.pending_requests || []).map((value) => {
          const request = normalizeUserInputRequest(value);
          return createTranscriptUserInputRequest({ toolCallId: request.tool_call_id, interruptId: request.interrupt_id,
            title: request.title, questions: request.questions, submitLabel: request.submit_label });
        })].map((item) => ({
          ...item,
          id: allocItemId(),
        })),
      );
    }
    scroll.setAutoScroll(true);
    scroll.scrollToBottom(true);
  };

  const onThreadCreated = (threadId: string, streamThreadIdRef: StreamThreadIdRef) => {
    // Apply buffered deltas before the persisted stream entry is re-keyed below.
    flushPendingMessageChunks();
    const oldStreamTid = streamThreadIdRef.id;
    const isViewingPending = !currentThreadId() || currentThreadId() === oldStreamTid;

    if (isViewingPending) {
      loadedThreadId = threadId;
      setCurrentThreadId(threadId);
    }

    if (oldStreamTid && oldStreamTid !== threadId && persistedStreams.has(oldStreamTid)) {
      const signals = persistedStreams.get(oldStreamTid)!;
      persistedStreams.delete(oldStreamTid);
      persistedStreams.set(threadId, signals);
    }
    streamThreadIdRef.id = threadId;

    if (currentStreamThreadId() === oldStreamTid || currentStreamThreadId() === threadId) {
      setCurrentStreamThreadId(threadId);
    }

    if (oldStreamTid !== threadId) {
      window.dispatchEvent(
        new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
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
  };

  const streamCallbacks: StreamCallbacks = {
    appendItem,
    updateMessage,
    replaceMessage,
    removeItem,
    updateToolResult,
    updatePlanReviewResult,
    updateUserInputRequestResult,
    onError: (message) => showToast(message, "error"),
    setRecursionLimitReached: (v) => setRecursionLimitReached(v),
    onThreadCreated,
    onThreadTitle: (threadId, title) => {
      if (threadId === currentThreadId()) {
        setThreadTitleOverride(title);
      }
      window.dispatchEvent(
        new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, {
          detail: { threadId, title },
        }),
      );
    },
  };

  const background = useBackgroundStream({ applyThreadPayload, streamCallbacks });
  const {
    backgroundTask,
    setBackgroundTask,
    backgroundLive,
    backgroundStreamError,
    backgroundSession,
    openBackgroundStream,
    closeBackgroundStream,
  } = background;

  const displayThreadTitle = createMemo(() => {
    const title = threadTitleOverride() || threadData()?.title;
    if (title && title.trim()) {
      return title.trim();
    }
    if (currentThreadId()) {
      return currentThreadId();
    }
    return "New Chat";
  });

  const fullThreadTitle = createMemo(() => {
    const title = threadTitleOverride() || threadData()?.title;
    if (title && title.trim()) {
      return currentThreadId() ? `${title.trim()} (${currentThreadId()})` : title.trim();
    }
    return currentThreadId() || "New Chat";
  });

  const pageSubtitle = createMemo(() => (
    currentThreadId()
      ? "Continue this thread."
      : "Start a conversation — a temporary workspace is created automatically when you send a message. Link a project if you want to work with real files."
  ));
  const isBackgroundThread = createMemo(() => threadData()?.kind === "background");
  const backgroundTaskActive = createMemo(() => {
    const task = backgroundTask();
    return Boolean(task && ACTIVE_TASK_STATUSES.has(task.status));
  });
  const conversationBusy = createMemo(() => (
    streaming() || threadLoading() || backgroundTaskActive() || backgroundLive() || Boolean(activeSession())
  ));
  const stopActive = createMemo(() => (
    streaming() || backgroundTaskActive() || backgroundLive() || Boolean(activeSession())
  ));
  const workspaceLocked = createMemo(() => Boolean(currentThreadId() && workingDir().trim()));
  const composerDisabled = createMemo(() => (
    conversationBusy() || hasPendingInterrupt() || compacting()
  ));
  const inputDisabled = createMemo(() => (
    threadLoading() || hasPendingInterrupt() || compacting()
  ));
  const userInputRequestDisabled = createMemo(() => conversationBusy() || compacting());

  const loadThread = async (threadId: string, checkpointId = "") => {
    const requestId = threadLoadRequestId + 1;
    threadLoadRequestId = requestId;
    setCurrentThreadId(threadId);
    setThreadData(undefined);
    setThreadError("");
    setRecursionLimitReached(window.localStorage.getItem(recursionLimitStorageKey(threadId)) === "true");
    if (!persistedStreams.has(threadId)) {
      setThreadLoading(true);
      setItems([]);
    }
    closeBackgroundStream();
    setBackgroundTask(null);
    scroll.setAutoScroll(true);

    try {
      const payload = await apiFetch<ThreadMessagesPayload>(
        threadApiPath(threadId, checkpointId),
      );
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
        if (persistedStreams.has(threadId)) {
          if (persistedStreams.get(threadId)!.streaming[0]()) {
            setStreaming(true);
          }
        }
      }
    }
  };

  const refreshThread = async (threadId: string, checkpointId = "") => {
    try {
      const payload = await apiFetch<ThreadMessagesPayload>(
        threadApiPath(threadId, checkpointId),
      );
      if (payload.thread_id === currentThreadId()) {
        applyThreadPayload(payload);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to refresh thread", "error");
    }
  };

  const updateCurrentThreadAgent = (nextAgentName: string) => {
    const threadId = currentThreadId();
    setThreadData((current) => (
      current
        ? { ...current, agent_name: nextAgentName }
        : current
    ));
    if (!threadId) {
      return;
    }
    window.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
        detail: {
          threadId,
          workspace: workspaceRef() || localWorkspaceRef(workingDir()),
          agent_name: nextAgentName,
        },
      }),
    );
  };

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

  let restoredThreadAgentKey = "";
  createEffect(() => {
    const td = threadData();
    const cards = validCards();
    if (!td || cards.length === 0) {
      return;
    }
    const threadKey = `${td.thread_id}\u001f${td.agent_name || ""}`;
    if (restoredThreadAgentKey === threadKey) {
      return;
    }
    const threadAgent = String(td.agent_name || "").trim();
    if (!threadAgent) {
      return;
    }
    const match = cards.find((card) => card.name === threadAgent);
    const fallback = cards.find((card) => card.name === "default") || cards[0];
    const next = match || fallback;
    if (next) {
      restoredThreadAgentKey = threadKey;
      setAgentName(next.name);
    }
  });

  const handleAgentChange = (nextAgentName: string) => {
    setAgentName(nextAgentName);
    updateCurrentThreadAgent(nextAgentName);
    const td = threadData();
    if (td) {
      restoredThreadAgentKey = `${td.thread_id}\u001f${nextAgentName}`;
    }
  };

  let restoredThreadModelKey = "";
  let lastDefaultedAgentName = "";

  createEffect(() => {
    const card = selectedCard();
    if (!card) {
      return;
    }
    const td = threadData();
    const threadKey = td
      ? [
          td.thread_id,
          td.agent_name || "",
          td.provider || "",
          td.model || "",
        ].join("\u001f")
      : "";
    if (td && threadKey && restoredThreadModelKey !== threadKey) {
      const selection = restoredModelSelection(td, card);
      setProvider(selection.provider);
      setModel(selection.model);
      restoredThreadModelKey = threadKey;
      lastDefaultedAgentName = card.name;
      return;
    }
    if (lastDefaultedAgentName !== card.name) {
      const selection = fallbackModelSelection(card);
      setProvider(selection.provider);
      setModel(selection.model);
      lastDefaultedAgentName = card.name;
    }
  });

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
      setThreadTitleOverride("");
      setCurrentStreamThreadId(null);
      setActiveCheckpointId("");
      setThreadData(undefined);
      restoredThreadModelKey = "";
      restoredThreadAgentKey = "";
      setThreadError("");
      setThreadLoading(false);
      setItems([]);
      setWorkspace(null);
      scroll.clearTurnAnchor();
      closeBackgroundStream();
      setBackgroundTask(null);
      return;
    }

    if (persistedStreams.has(threadId)) {
      setCurrentStreamThreadId(threadId);
      setCurrentThreadId(threadId);
      setThreadLoading(false);
      void loadThread(threadId);
      return;
    }

    setCurrentStreamThreadId(null);
    void loadThread(threadId);
  });

  const buildPayload = (
    message: string,
    attachedFiles: ChatAttachmentPayload[],
    resolvedWorkspace?: WorkspaceRef | null,
    agentNameOverride?: string,
  ) => {
    const payload: ChatPayload = {
      message,
      user_id: "dashboard",
      agent_name: agentNameOverride || agentName(),
    };
    if (provider()) {
      payload.provider = provider();
    }
    if (model()) {
      payload.model = model();
    }
    const workspace = resolvedWorkspace || workspaceRef() || localWorkspaceRef(workingDir());
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

  const makeEventHandler = (
    assistantIdRef: AssistantIdRef,
    streamedRef: StreamedRef,
    streamThreadIdRef: StreamThreadIdRef,
  ) => (event: Record<string, unknown>) => {
    handleStreamEvent(event, assistantIdRef, streamedRef, streamThreadIdRef, streamCallbacks);
  };

  const markLocalStreamFinished = (threadId: string) => {
    latestLocalStreamFinish = { threadId, finishedAt: Date.now() };
  };

  const shouldSkipStopReload = (threadId: string) => {
    if (streaming() || currentStreamThreadId() === threadId) {
      return true;
    }
    if (!latestLocalStreamFinish || latestLocalStreamFinish.threadId !== threadId) {
      return false;
    }
    return Date.now() - latestLocalStreamFinish.finishedAt < 5000;
  };

  const sendMessage = async (
    resume = false,
    resumePayload?: ChatResumePayload,
    agentNameOverride?: string,
  ) => {
    isUnmounting = false;
    cleanupStaleStreams();
    if (streaming()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    if (compacting()) {
      showToast("Wait for compaction to finish.", "warning");
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
    const effectiveAgentName = agentNameOverride || agentName();
    if (!effectiveAgentName) {
      showToast("No valid agent is available.", "error");
      return;
    }

    setRecursionLimitReached(false);
    const activeTid = currentThreadId();
    if (activeTid) {
      window.localStorage.removeItem(recursionLimitStorageKey(activeTid));
    }

    const streamThreadIdRef: StreamThreadIdRef = { id: currentThreadId() || `__pending__${Date.now()}`, message };

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
        new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
          detail: {
            threadId: startTid,
            title: message,
            workspace: workspaceRef() || localWorkspaceRef(workingDir()),
            agent_name: agentName(),
          },
        }),
      );
    }

    const currentWorkspace = workspaceRef();
    const needsWorkspaceResolution = !resume && !currentWorkspace;
    const needsEnvironmentInitialization = !resume && (
      needsWorkspaceResolution || workspaceNeedsEnvironmentInitialization(currentWorkspace, initializedWorkspaces())
    );
    const statusMessageId = !resume
      ? appendItem(
          {
            type: "message",
            role: "assistant",
            text: needsEnvironmentInitialization ? INITIALIZING_ENVIRONMENT_TEXT : THINKING_TEXT,
          },
          "bottom",
          streamThreadIdRef.id,
        )
      : null;
    const assistantIdRef: AssistantIdRef = { id: statusMessageId };
    const streamedRef: StreamedRef = { received: false };
    const removePendingAssistantStatus = () => {
      if (assistantIdRef.id !== null && !streamedRef.received) {
        removeItem(assistantIdRef.id, streamThreadIdRef.id);
        assistantIdRef.id = null;
      }
    };

    try {
      const resolvedWorkspace = resume
        ? workspaceRef() || localWorkspaceRef(workingDir())
        : await resolveWorkspaceForSend();
      if (statusMessageId !== null && !needsEnvironmentInitialization) {
        replaceMessage(statusMessageId, THINKING_TEXT, streamThreadIdRef.id);
      }

      const payload = buildPayload(
        message,
        resume ? [] : attachedFiles,
        resolvedWorkspace,
        effectiveAgentName,
      );
      if (resume) {
        payload.resume = true;
        payload.message = "";
        if (resumePayload) {
          payload.resume_payload = resumePayload;
        }
        // Do not send checkpoint_id when resuming an interrupt; let the
        // backend resume from the latest checkpoint that holds the pending
        // interrupt. A stale checkpoint_id would cause the plan approval
        // to resume from the wrong state and appear as stopped after
        // switching to the target agent.
      } else if (currentThreadId() && activeCheckpointId()) {
        payload.checkpoint_id = activeCheckpointId();
      }
      const response = await fetchWithCsrf(API_PATHS.chatEvents, {
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
      if (!resume && resolvedWorkspace) {
        const key = workspaceEnvironmentKey(resolvedWorkspace);
        if (key && !initializedWorkspaces().has(key)) {
          const next = new Set(initializedWorkspaces());
          next.add(key);
          setInitializedWorkspaces(next);
        }
      }
      if (statusMessageId !== null && needsEnvironmentInitialization && !streamedRef.received) {
        replaceMessage(statusMessageId, THINKING_TEXT, streamThreadIdRef.id);
      }

      await readNDJSONStream(
        response.body,
        makeEventHandler(assistantIdRef, streamedRef, streamThreadIdRef),
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        removePendingAssistantStatus();
        if (!isUnmounting) {
          showToast("Response stopped.", "warning");
        }
      } else {
        removePendingAssistantStatus();
        appendItem({
          type: "message",
          role: "error",
          text: err instanceof Error ? err.message : "Chat failed",
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
      flushPendingMessageChunks();
      const finishedTid = streamThreadIdRef.id;
      markLocalStreamFinished(finishedTid);
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
          new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, {
            detail: { threadId: finishedTid },
          }),
        );
      }
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
      if (finishedTid && !isUnmounting && currentThreadId() === finishedTid) {
        void refreshThread(finishedTid);
      }
    }
  };

  const reconnectStream = async (threadId: string) => {
    isUnmounting = false;
    cleanupStaleStreams();
    if (streaming()) {
      return;
    }

    const currentItems = items();
    const lastUserIndex = currentItems.map(item => item.type === "message" && item.role === "user").lastIndexOf(true);
    if (lastUserIndex !== -1) {
      setItems(currentItems.slice(0, lastUserIndex + 1));
    }

    const streamThreadIdRef: StreamThreadIdRef = { id: threadId, message: "" };
    getOrCreateStreamSignals(streamThreadIdRef.id, items());
    setCurrentStreamThreadId(streamThreadIdRef.id);

    const abortController = new AbortController();
    setController(abortController, streamThreadIdRef.id);
    setStreaming(true, streamThreadIdRef.id);

    const assistantIdRef: AssistantIdRef = { id: null };
    const streamedRef: StreamedRef = { received: false };

    try {
      const response = await fetchWithCsrf(API_PATHS.chatEventsReconnect, {
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

      await readNDJSONStream(
        response.body,
        makeEventHandler(assistantIdRef, streamedRef, streamThreadIdRef),
      );
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
      flushPendingMessageChunks();
      const finishedTid = streamThreadIdRef.id;
      markLocalStreamFinished(finishedTid);
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
          new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, {
            detail: { threadId: finishedTid },
          }),
        );
      }
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
    }
  };

  const stopChat = async () => {
    if (streaming()) {
      const tid = currentStreamThreadId() || currentThreadId();
      // Abort the local fetch first so UI stops rendering immediately.
      controller()?.abort();
      // Also request server-side cancellation so the LLM stream actually stops.
      // Abort alone only disconnects the client; the background ChatStreamSession
      // survives client disconnects (for F5 reconnection) and must be cancelled
      // explicitly via the sessions/stop endpoint.
      if (tid && !tid.startsWith("__pending__")) {
        const requests: Promise<unknown>[] = [postJson(API_PATHS.sessionsStop, { thread_id: tid })];
        const taskId = backgroundTask()?.task_id;
        if (taskId) {
          requests.push(postJson(API_PATHS.taskCancel(taskId), {}));
        }
        // Fire-and-forget is not enough for reliable UX; await so server confirms,
        // but swallow errors because the local AbortError toast already informs the user.
        try {
          await Promise.allSettled(requests);
        } catch {}
      }
      return;
    }
    const tid = currentThreadId();
    if (!tid) {
      return;
    }
    const requests: Promise<unknown>[] = [postJson(API_PATHS.sessionsStop, { thread_id: tid })];
    const taskId = backgroundTask()?.task_id;
    if (taskId) {
      requests.push(postJson(API_PATHS.taskCancel(taskId), {}));
    }
    const results = await Promise.allSettled(requests);
    if (results.some((result) => result.status === "fulfilled")) {
      showToast("Session stop requested.");
    } else {
      const rejected = results.find((result) => result.status === "rejected") as
        PromiseRejectedResult | undefined;
      showToast(
        rejected?.reason instanceof Error
          ? rejected.reason.message
          : "Failed to stop session",
        "error",
      );
    }
  };
  const handleResume = () => {
    void sendMessage(true);
  };
  const handlePasteAsAttachment = (text: string) => {
    addTextContent(text);
    showToast("Long content attached as a file.", "success");
  };
  const handleApprovePlanReview = (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    targetAgent: string;
  }) => {
    const targetAgent = payload.targetAgent.trim();
    if (!targetAgent) {
      showToast("Select a target agent.", "warning");
      return;
    }
    if (conversationBusy() || compacting()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    updatePlanReview(payload.toolCallId, {
      status: "approved",
      targetAgent,
    });
    updateCurrentThreadAgent(targetAgent);
    setAgentName(targetAgent);
    // Sync provider/model to the target agent's defaults before resuming.
    // Otherwise the stale planner provider/model would override the target
    // and the resume could use the wrong model, appearing as stopped.
    const targetCard = validCards().find((card) => card.name === targetAgent);
    if (targetCard) {
      const selection = fallbackModelSelection(targetCard);
      // Only update if the current selection is not already the target's
      // fallback; this avoids clobbering an explicit user choice when the
      // provider/model are already compatible, but ensures a stale planner
      // selection does not poison the act agent's run.
      if (selection.provider !== provider() || selection.model !== model()) {
        setProvider(selection.provider);
        setModel(selection.model);
      }
    }
    void sendMessage(
      true,
      {
        action: "approve",
        target_agent: targetAgent,
      },
      targetAgent,
    );
  };
  const handleRevisePlanReview = (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    feedback: string;
  }) => {
    const feedback = payload.feedback.trim();
    if (!feedback) {
      showToast("Enter feedback before sending.", "warning");
      return;
    }
    if (conversationBusy() || compacting()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    updatePlanReview(payload.toolCallId, {
      status: "revision_requested",
      feedback,
    });
    void sendMessage(
      true,
      {
        action: "revise",
        feedback,
      },
    );
  };

  const handleSubmitUserInputRequest = (payload: UserInputRequestSubmitPayload) => {
    if (conversationBusy() || compacting()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    const request = pendingUserInputRequest();
    if (!request || request.tool_call_id !== payload.toolCallId) {
      showToast("The input request is no longer active.", "warning");
      return;
    }
    const permission = request.questions.find((question) => question.id.startsWith("permission:"));
    const decision = permission ? payload.answers.find((answer) => answer.question_id === permission.id)?.selected_option_ids[0] : undefined;
    if (permission && decision !== "allow_once" && decision !== "allow_thread" && decision !== "deny") {
      showToast("Choose a permission decision.", "warning");
      return;
    }
    updateUserInputRequest(payload.toolCallId, {
      status: "answered",
      answers: payload.answers,
      summary: payload.summary,
    });
    appendItem(
      {
        type: "message",
        role: "user",
        text: payload.summary,
      },
      "turn-start",
    );
    if (permission && (decision === "allow_once" || decision === "allow_thread" || decision === "deny")) {
      void sendMessage(true, { action: "permission", request_id: permission.id.slice("permission:".length), decision });
    } else {
      void sendMessage(true, { action: "answer", answers: payload.answers, summary: payload.summary });
    }
  };

  const handleBranchSelect = (checkpointId: string) => {
    const threadId = currentThreadId();
    if (!threadId || !checkpointId || checkpointId === activeCheckpointId() || conversationBusy() || compacting()) {
      return;
    }
    void loadThread(threadId, checkpointId);
  };

  const handleEditMessage = async (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => {
    const threadId = currentThreadId();
    const nextText = payload.text.trim();
    if (!threadId || !nextText) {
      return;
    }
    if (conversationBusy() || compacting()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }
    if (!agentName()) {
      showToast("No valid agent is available.", "error");
      return;
    }

    isUnmounting = false;
    cleanupStaleStreams();
    setRecursionLimitReached(false);
    window.localStorage.removeItem(recursionLimitStorageKey(threadId));

    const currentItems = items();
    const itemIndex = currentItems.findIndex((item) => item.id === payload.itemId);
    const nextItems = itemIndex === -1
      ? currentItems
      : [
          ...currentItems.slice(0, itemIndex),
          {
            ...currentItems[itemIndex],
            text: nextText,
          },
        ];
    setItems(nextItems);

    const streamThreadIdRef: StreamThreadIdRef = { id: threadId, message: nextText };
    getOrCreateStreamSignals(streamThreadIdRef.id, nextItems);
    setCurrentStreamThreadId(streamThreadIdRef.id);

    const abortController = new AbortController();
    setController(abortController, streamThreadIdRef.id);
    setStreaming(true, streamThreadIdRef.id);

    window.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
        detail: {
          threadId,
          title: nextText,
          workspace: workspaceRef() || localWorkspaceRef(workingDir()),
          agent_name: agentName(),
        },
      }),
    );

    const assistantIdRef: AssistantIdRef = { id: null };
    const streamedRef: StreamedRef = { received: false };

    try {
      const response = await fetchWithCsrf(API_PATHS.chatEventsEdit, {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          message: nextText,
          user_id: "dashboard",
          thread_id: threadId,
          message_index: payload.messageIndex,
          source_checkpoint_id: payload.sourceCheckpointId,
          agent_name: agentName(),
          provider: provider(),
          model: model(),
          workspace: workspaceRef() || localWorkspaceRef(workingDir()),
        }),
        signal: abortController.signal,
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      if (!response.body) {
        throw new Error("Streaming response is not available.");
      }

      await readNDJSONStream(
        response.body,
        makeEventHandler(assistantIdRef, streamedRef, streamThreadIdRef),
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        if (!isUnmounting) {
          showToast("Response stopped.", "warning");
        }
      } else {
        appendItem({
          type: "message",
          role: "error",
          text: err instanceof Error ? err.message : "Chat edit failed",
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
      flushPendingMessageChunks();
      const finishedTid = streamThreadIdRef.id;
      markLocalStreamFinished(finishedTid);
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
      window.dispatchEvent(
        new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, {
          detail: { threadId: finishedTid },
        }),
      );
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
      if (finishedTid && !isUnmounting && currentThreadId() === finishedTid) {
        void refreshThread(finishedTid);
      }
    }
  };

  const handleRegenerateMessage = (payload?: {
    itemId?: number;
    messageIndex?: number;
    sourceCheckpointId?: string;
    text?: string;
  }) => {
    if (conversationBusy() || compacting()) {
      showToast("Wait for the current response to finish.", "warning");
      return;
    }

    if (payload?.sourceCheckpointId && payload.messageIndex !== undefined && typeof payload.text === "string") {
      void handleEditMessage({
        itemId: payload.itemId,
        messageIndex: payload.messageIndex,
        sourceCheckpointId: payload.sourceCheckpointId,
        text: payload.text,
      });
      return;
    }

    const currentItems = items();
    let targetIndex = -1;
    if (payload?.itemId !== undefined) {
      targetIndex = currentItems.findIndex((it) => it.id === payload.itemId);
    } else if (payload?.messageIndex !== undefined) {
      targetIndex = currentItems.findIndex(
        (it) => it.type === "message" && it.role === "assistant" && it.messageIndex === payload.messageIndex
      );
    }

    const searchSlice = targetIndex >= 0 ? currentItems.slice(0, targetIndex) : currentItems;
    for (let i = searchSlice.length - 1; i >= 0; i--) {
      const it = searchSlice[i];
      if (it.type === "message" && it.role === "user" && it.messageIndex !== undefined && it.sourceCheckpointId) {
        void handleEditMessage({
          itemId: it.id,
          messageIndex: it.messageIndex,
          sourceCheckpointId: it.sourceCheckpointId,
          text: it.text || "",
        });
        return;
      }
    }
    showToast("No earlier user prompt found to regenerate from.", "warning");
  };

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
      if (shouldSkipStopReload(thread_id)) {
        return;
      }
      void loadThread(thread_id);
    }
  };

  createEffect(() => {
    const threadId = currentThreadId();
    const backgroundThread = isBackgroundThread();
    if (threadId) {
      const fetchInitialSession = async () => {
        try {
          const payload = await apiFetch<{ sessions: ActiveSession[] }>("/dashboard-api/sessions");
          const found = payload.sessions.find((s) => s.thread_id === threadId);
          if (threadId === currentThreadId()) {
            setActiveSession(found || null);
            if (found && !streaming() && threadData() && !backgroundThread) {
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

  const handleTranscriptToolToggle = () => {
    scroll.pauseAutoScroll();
  };

  const handleExternalAbort = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string }>;
    if (customEvent.detail.threadId === currentStreamThreadId() || customEvent.detail.threadId === currentThreadId()) {
      stopChat();
    }
  };

  const handleThreadStopRunningExternal = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string }>;
    const stoppedId = customEvent.detail.threadId;
    if (stoppedId === currentThreadId() && !shouldSkipStopReload(stoppedId)) {
      void loadThread(stoppedId);
    }
  };

  const handleThreadTitleUpdated = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string; title: string }>;
    if (customEvent.detail?.threadId === currentThreadId()) {
      setThreadTitleOverride(customEvent.detail.title || "");
    }
  };

  onMount(() => {
    isUnmounting = false;
    cleanupStaleStreams();

    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_EXTERNAL_ABORT, handleExternalAbort);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunningExternal);
    window.addEventListener(CUSTOM_DOM_EVENTS.SESSION_STARTED, handleSessionStartedOrUpdated);
    window.addEventListener(CUSTOM_DOM_EVENTS.SESSION_UPDATED, handleSessionStartedOrUpdated);
    window.addEventListener(CUSTOM_DOM_EVENTS.SESSION_STOPPED, handleSessionStopped);
    window.addEventListener(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, handleTranscriptToolToggle);

    void load();
    void loadDefaultWorkspace();
  });
  onCleanup(() => {
    isUnmounting = true;
    const activeTid = currentStreamThreadId();
    if (!activeTid || !persistedStreams.has(activeTid)) {
      controller()?.abort();
    }
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_EXTERNAL_ABORT, handleExternalAbort);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunningExternal);
    window.removeEventListener(CUSTOM_DOM_EVENTS.SESSION_STARTED, handleSessionStartedOrUpdated);
    window.removeEventListener(CUSTOM_DOM_EVENTS.SESSION_UPDATED, handleSessionStartedOrUpdated);
    window.removeEventListener(CUSTOM_DOM_EVENTS.SESSION_STOPPED, handleSessionStopped);
    window.removeEventListener(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, handleTranscriptToolToggle);
  });

  const handleCompactConversation = async () => {
    const threadId = currentThreadId();
    if (!threadId) {
      showToast("No active conversation to compact.", "warning");
      return;
    }
    if (compacting()) {
      return;
    }
    if (streaming() || conversationBusy()) {
      showToast("Cannot compact while agent is running.", "warning");
      return;
    }
    if (hasPendingInterrupt()) {
      showToast("Cannot compact while conversation is awaiting your input.", "warning");
      return;
    }
    const currentItems = items();
    if (currentItems.length < 3) {
      showToast("Conversation has too few messages to compact.", "warning");
      return;
    }

    setCompacting(true);
    scroll.setAutoScroll(true);
    scroll.scrollToBottom(true);
    try {
      const payload = await postJson<ThreadMessagesPayload & {
        compacted_count: number;
        kept_count: number;
        summary: string;
        current_context_tokens?: number;
        retained_tokens?: number;
      }>(`/dashboard-api/chat-history/${encodeURIComponent(threadId)}/compact`, {});

      if (currentThreadId() !== threadId) {
        return;
      }
      if (payload && Array.isArray(payload.messages)) {
        if (payload.thread_id && payload.thread_id !== threadId) {
          showToast("Failed to compact conversation: invalid server response.", "error");
          return;
        }
        setActiveCheckpointId(payload.active_checkpoint_id || "");
        const newTranscriptItems = toThreadTranscript(payload.messages, threadId).map((item) => ({
          ...item,
          id: allocItemId(),
        }));
        setItems(newTranscriptItems, threadId);
        const nextTokens = payload.current_context_tokens ?? payload.retained_tokens;
        if (typeof nextTokens === "number") {
          updateContextTokens(nextTokens);
        }
        void refreshThreadUsage(threadId);
        showToast(
          `Conversation compacted! Summarized ${payload.compacted_count} earlier message(s).`,
          "success",
        );
      } else {
        showToast("Failed to compact conversation: invalid server response.", "error");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to compact conversation.";
      showToast(message, "error");
    } finally {
      setCompacting(false);
    }
  };

  let touchStartX = 0;
  let touchStartY = 0;

  const handleShellClick = (event: MouseEvent) => {
    // Backdrop click to dismiss drawer is only for mobile viewport (<= 980px),
    // never close on desktop or while/immediately after dragging resize handle.
    if (
      isMobileViewport()
      && explorer.open()
      && !explorer.resizing()
      && !explorer.justResized()
      && event.target === chatShellRef
    ) {
      explorer.toggle();
    }
  };

  const handleTouchStart = (event: TouchEvent) => {
    if (!isMobileViewport() || !explorer.open() || explorer.resizing() || explorer.justResized()) return;
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
  };

  const handleTouchEnd = (event: TouchEvent) => {
    if (!isMobileViewport() || !explorer.open() || explorer.resizing() || explorer.justResized()) return;
    const touchEndX = event.changedTouches[0].clientX;
    const touchEndY = event.changedTouches[0].clientY;
    const deltaX = touchEndX - touchStartX;
    const deltaY = touchEndY - touchStartY;

    // Close when swiping right (finger moves from left to right on panel)
    if (deltaX > 80 && Math.abs(deltaY) < 45) {
      explorer.toggle();
    }
  };

  return (
    <>
    <AppShell
      title={currentThreadId() ? "Thread Chat" : "Agent Chat"}
      noHeader={true}
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div
            ref={chatShellRef}
            class={`chat-shell chat-shell-resizable ${explorer.open() ? "workspace-open" : "workspace-closed"} ${explorer.resizing() ? "workspace-resizing" : ""}`}
            style={`--workspace-explorer-width: ${explorer.width()}px;`}
            onClick={handleShellClick}
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
          >
            <section class="panel chat-panel">
              <div class="chat-header">
                <div class="chat-header-left">
                  <Show when={isDrawerActive()}>
                    <button
                      class="chat-header-menu-toggle"
                      type="button"
                      onClick={() => {
                        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.OPEN_MOBILE_NAV));
                      }}
                      aria-label="Open navigation"
                    >
                      <Menu size={16} />
                    </button>
                  </Show>
                  <div class="chat-header-title-wrap">
                    <span class="chat-header-title" title={fullThreadTitle()}>
                      {displayThreadTitle()}
                    </span>
                    <Show when={isBackgroundThread() || activeSession()}>
                      <ChatThreadBadges
                        isBackgroundThread={isBackgroundThread()}
                        backgroundTask={backgroundTask()}
                        backgroundLive={backgroundLive()}
                        backgroundSession={backgroundSession()}
                        activeSession={activeSession()}
                      />
                    </Show>
                  </div>
                </div>
                <div class="chat-header-right">
                  <Show when={!explorer.open()}>
                    <button
                      class="chat-header-workspace-btn"
                      type="button"
                      onClick={explorer.toggle}
                      title="Open workspace"
                      aria-label="Open workspace"
                    >
                      <PanelRightOpen size={19} />
                    </button>
                  </Show>
                </div>
              </div>
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
              <ChatTranscript
                setTranscriptRef={(el) => (transcriptRef = el)}
                onScroll={handleTranscriptScroll}
                onUserScroll={scroll.pauseAutoScroll}
                items={items()}
                filteredItems={filteredItems()}
                threadLoading={threadLoading()}
                currentThreadId={currentThreadId()}
                streaming={streaming()}
                backgroundLive={backgroundLive()}
                compacting={compacting()}
                autoScroll={autoScroll()}
                turnAnchorSpacerHeight={turnAnchorSpacerHeight()}
                onScrollToBottomClick={handleScrollToBottomClick}
                workingDir={workingDir()}
                defaultWorkingDir={defaultWorkingDir()}
                workspace={workspaceRef()}
                workspaceSelection={workspaceSelection()}
                conversationBusy={conversationBusy()}
                agents={validCards()}
                activeAgentName={agentName()}
                onWorkspaceSelectionChange={setWorkspaceDraft}
                onSelectPrompt={handleSelectPrompt}
                onEditMessage={(payload) => void handleEditMessage(payload)}
                onRegenerateMessage={handleRegenerateMessage}
                onBranchSelect={handleBranchSelect}
                onApprovePlanReview={handleApprovePlanReview}
                onRevisePlanReview={handleRevisePlanReview}
                onMessageClick={setViewingMessage}
              />
              <ChatComposer
                setChatPromptRef={(el) => (chatPromptRef = el)}
                chatPromptRef={(el) => (chatPromptRef = el)}
                prompt={prompt()}
                onPromptChange={setPrompt}
                onSend={() => void sendMessage()}
                onStop={stopChat}
                onResume={handleResume}
                onAddFiles={addFiles}
                onPasteAsAttachment={handlePasteAsAttachment}
                onRemoveAttachment={removeAttachment}
                stopActive={stopActive()}
                composerDisabled={composerDisabled()}
                inputDisabled={inputDisabled()}
                backgroundTaskActive={backgroundTaskActive()}
                currentThreadId={currentThreadId()}
                attachments={attachments()}
                attachmentAccept={attachmentAccept()}
                agentName={agentName()}
                agents={validCards()}
                onAgentChange={handleAgentChange}
                provider={provider()}
                model={model()}
                onProviderModelChange={(nextProvider, nextModel) => {
                  setProvider(nextProvider);
                  setModel(nextModel);
                }}
                payload={payload}
                recursionLimitReached={recursionLimitReached()}
                currentTodos={currentTodos()}
                todoProgress={todoProgress()}
                todosExpanded={todosExpanded()}
                onTodosToggle={() => setTodosExpanded(!todosExpanded())}
                contextWindowData={contextWindowData()}
                compacting={compacting()}
                onCompactClick={handleCompactConversation}
                userInputRequest={pendingUserInputRequest()}
                userInputRequestDisabled={userInputRequestDisabled()}
                onSubmitUserInputRequest={handleSubmitUserInputRequest}
              />
            </section>
            <div class="workspace-drawer" aria-hidden={!explorer.open()}>
              <button
                class="workspace-resize-handle"
                type="button"
                onPointerDown={explorer.startResize}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
                title="Resize workspace explorer"
                aria-label="Resize workspace explorer"
              />
              <WorkspaceExplorer
                threadId={currentThreadId()}
                workingDir={workingDir()}
                workspace={workspaceRef()}
                disabled={
                  conversationBusy()
                  || !workspaceRef()
                  || !(workspaceRef()?.locator || workingDir()).trim()
                  || !currentThreadId()
                  || (workspaceLocked() && workspaceRef()?.backend !== "modal")
                }
                onWorkingDirChange={setWorkspace}
                onCollapse={explorer.toggle}
              />
            </div>
          </div>
        )}
      </DataGate>
    </AppShell>
    <Dialog
      open={viewingMessage() !== null}
      title={`${viewingMessage()?.role || "Message"} Message`}
      subtitle={`Raw inspect view for ${viewingMessage()?.role || "message"}`}
      size="xl"
      onClose={() => setViewingMessage(null)}
    >
      <Show
        when={viewingMessage()?.role === "assistant"}
        fallback={
          <div style={{ "max-height": "70vh", "overflow-y": "auto" }}>
            <pre style={{
              "white-space": "pre-wrap",
              "word-break": "break-all",
              "font-size": "13px",
              "line-height": "1.6",
              "margin": 0,
            }}>
              {viewingMessage()?.text}
            </pre>
          </div>
        }
      >
        <div style={{ "max-height": "70vh", "overflow-y": "auto" }}>
          <Markdown
            text={viewingMessage()?.text || ""}
            class="message-markdown"
            threadId={currentThreadId()}
          />
        </div>
      </Show>
      <Show when={viewingMessage()?.attachments?.length}>
        <div style={{ "margin-top": "12px", "border-top": "1px solid var(--border)", "padding-top": "12px" }}>
          <For each={viewingMessage()?.attachments || []}>
            {(attachment) => (
              <div style={{ "margin-bottom": "8px" }}>
                <div style={{
                  "font-size": "12px",
                  "font-weight": 600,
                  "color": "var(--muted)",
                  "margin-bottom": "4px",
                }}>
                  {attachment.name}
                </div>
                <Show
                  when={attachment.kind === "image" && attachment.preview_url}
                  fallback={
                    <Show when={attachment.content}>
                      <pre style={{
                        "white-space": "pre-wrap",
                        "word-break": "break-all",
                        "font-size": "12px",
                        "line-height": "1.5",
                        "margin": 0,
                        "background": "var(--surface-2)",
                        "border": "1px solid var(--border)",
                        "border-radius": "6px",
                        "padding": "8px",
                        "max-height": "300px",
                        "overflow-y": "auto",
                      }}>
                        {attachment.content}
                      </pre>
                    </Show>
                  }
                >
                  <img
                    src={attachment.preview_url}
                    alt={attachment.name}
                    style={{ "max-width": "100%", "border-radius": "6px" }}
                  />
                </Show>
              </div>
            )}
          </For>
        </div>
      </Show>
    </Dialog>
    </>
  );
}
