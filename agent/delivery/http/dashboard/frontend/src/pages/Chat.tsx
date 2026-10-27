import { useLocation, useNavigate, useParams, useSearchParams } from "@solidjs/router";
import {
  Menu,
  PanelRightClose,
  PanelRightOpen,
  X,
} from "lucide-solid";
import { batch, createEffect, createMemo, createSignal, For, on, onCleanup, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { ChatComposer } from "@/components/ChatComposer";
import { ComposerWorkspacePicker } from "@/components/ComposerWorkspacePicker";
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
import { ProjectLocalSync } from "@/components/ProjectLocalSync";
import type { TodoProgress } from "@/components/ChatTodos";
import { apiFetch, fetchWithCsrf, postJson, readError } from "@/lib/api";
import { fetchAgentChatOptions } from "@/lib/agents";
import { readChatModelPreference, writeChatModelPreference, type ChatModelPreference } from "@/lib/chatModelPreference";
import { resolveModelAndProvider } from "@/lib/modelSelection";
import {
  chatThreadHref,
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import type { NewChatNavigationState, ThreadMessagesPayload } from "@/lib/chatThreads";
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
  WorkspaceBinding,
  WorkspaceBackendKey,
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
import { useWorkspaceRuntime } from "@/lib/useWorkspaceRuntime";
import { fetchCatalog, getBackendDisplayName, isBackendEnabled } from "@/lib/catalogStore";
import { useChatAttachments } from "@/lib/useChatAttachments";
import { useContextWindow } from "@/lib/useContextWindow";
import { useBackgroundStream } from "@/lib/useBackgroundStream";
import { useReasoningEffort } from "@/lib/useReasoningEffort";
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
  const location = useLocation<NewChatNavigationState>();
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
  const [customModel, setCustomModel] = createSignal(false);
  const [reasoningScope, setReasoningScope] = createSignal(0);
  const [workingDir, setWorkingDir] = createSignal("");
  const [workspaceRef, setWorkspaceRef] = createSignal<WorkspaceRef | null>(null);
  const [workspaceBinding, setWorkspaceBinding] = createSignal<WorkspaceBinding | null>(null);
  const [draftBackend, setDraftBackend] = createSignal<WorkspaceBackendKey>("local");
  const [workspaceSelection, setWorkspaceSelection] = createSignal<WorkspaceSelectionDraft | null>(null);
  const [defaultWorkingDir, setDefaultWorkingDir] = createSignal("");
  const [prompt, setPrompt] = createSignal("");
  const [todosExpanded, setTodosExpanded] = createSignal(false);
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
  let loadedNewChatId = "";
  let isUnmounting = false;
  let threadLoadRequestId = 0;
  let threadLoadController: AbortController | undefined;
  let compactRequestId = 0;
  let latestLocalStreamFinish: { threadId: string; finishedAt: number } | null = null;
  const selectionDrafts = new Map<string, ChatModelPreference & { agentName: string }>();
  const promptDrafts = new Map<string, string>();

  const scroll = useChatScroll(() => transcriptRef);
  const {
    showScrollToBottom,
    handleTranscriptScroll,
    handleScrollToBottomClick,
    turnAnchorSpacerHeight,
  } = scroll;

  const explorer = useWorkspaceExplorer(() => chatShellRef);

  createEffect(() => {
    if (isDrawerActive() && explorer.fullscreen()) {
      explorer.setFullscreen(false);
    }
  });

  const validCards = createMemo(() => (data()?.cards || []).filter((card) => card.valid && !card.internal));
  const agentOptions = createMemo(() =>
    validCards().map((card) => ({
      value: card.name,
      label: card.display_name || card.name,
    })),
  );
  const selectedCard = createMemo<AgentCard | undefined>(() =>
    validCards().find((card) => card.name === agentName()),
  );

  const modelSelectionAvailable = (nextProvider: string, nextModel: string, customModel = false): boolean => {
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
    // Only explicitly remembered custom models may bypass catalog membership.
    if (customModel && resolvedProvider !== "@pool") {
      return true;
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

  const fallbackModelSelection = (card: AgentCard): ChatModelPreference => ({
    provider: card.provider || "default",
    model: card.model || "",
  });

  const restoredModelSelection = (
    thread: ThreadMessagesPayload | undefined,
    card: AgentCard,
  ): ChatModelPreference => {
    const threadProvider = String(thread?.provider || "").trim();
    const threadModel = String(thread?.model || "").trim();
    if (threadProvider && modelSelectionAvailable(threadProvider, threadModel)) {
      return { provider: threadProvider, model: threadModel };
    }
    const cardSelection = fallbackModelSelection(card);
    if (modelSelectionAvailable(cardSelection.provider, cardSelection.model)) {
      return cardSelection;
    }
    const payload = data();
    return resolveModelAndProvider(
      "default", "", payload?.default_provider || "", payload?.default_model || "", payload?.model_catalogs || [],
    );
  };

  const rememberSelection = () => {
    const selection = {
      agentName: agentName(), provider: provider(), model: model(),
      ...(customModel() ? { customModel: true as const } : {}),
    };
    const threadId = currentThreadId();
    if (threadId) selectionDrafts.set(threadId, selection);
    const stream = persistedStreams.get(currentStreamThreadId() || "");
    if (stream) stream.selection = selection;
    return selection;
  };

  const restoreSelection = (thread?: ThreadMessagesPayload) => batch(() => {
    const cards = validCards();
    if (!cards.length) return;
    const threadId = thread?.thread_id || currentThreadId();
    const draft = selectionDrafts.get(threadId) || persistedStreams.get(threadId)?.selection;
    if (draft && threadId) selectionDrafts.set(threadId, draft);
    const remembered = !threadId ? readChatModelPreference() : null;
    const requested = thread?.agent_name || (!threadId ? String(searchParams.agent || "") : "");
    const card = cards.find((item) => item.name === (draft?.agentName || requested))
      || cards.find((item) => item.name === remembered?.agentName)
      || cards.find((item) => item.name === "default") || cards[0];
    const selection = draft || (
      remembered && modelSelectionAvailable(remembered.provider, remembered.model, remembered.customModel)
        ? remembered
        : restoredModelSelection(thread, card)
    );
    setAgentName(card.name);
    setProvider(selection.provider);
    setModel(selection.model);
    setCustomModel(selection.customModel === true);
  });

  const handleProviderModelChange = (nextProvider: string, nextModel: string, isCustomModel = false) => batch(() => {
    setProvider(nextProvider);
    setModel(nextModel);
    setCustomModel(isCustomModel);
    rememberSelection();
  });

  const selectedModelSelection = createMemo(() => {
    const payload = data();
    if (!payload) return undefined;
    const card = selectedCard();
    const activeProvider = provider() || card?.provider || "default";
    const activeModel = model() || card?.model || "";
    return resolveModelAndProvider(
      activeProvider, activeModel, payload.default_provider, payload.default_model, payload.model_catalogs || [],
    );
  });

  const selectedModelIsCustom = () => {
    const selection = selectedModelSelection();
    return Boolean(selection && selection.provider !== "@pool"
      && !modelSelectionAvailable(selection.provider, selection.model));
  };

  const selectedModelOption = createMemo<ModelOption | undefined>(() => {
    const selected = selectedModelSelection();
    return data()?.model_catalogs?.find((c) => c.provider === selected?.provider)?.models
      .find((m) => m.id === selected?.model);
  });
  const { levels: effortLevels, effort: reasoningEffort, setEffort: setReasoningEffort, requestEffort } =
    useReasoningEffort(
      () => JSON.stringify([reasoningScope(), agentName(), selectedModelSelection()]),
      selectedModelOption,
      () => {
        const card = selectedCard();
        const selected = selectedModelSelection();
        const payload = data();
        if (!card || !selected || !payload) return undefined;
        const cardSelection = resolveModelAndProvider(
          card.provider, card.model, payload.default_provider, payload.default_model, payload.model_catalogs || [],
        );
        return cardSelection.provider === selected.provider && cardSelection.model === selected.model
          ? card.reasoning_effort : undefined;
      },
    );

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

  const { contextWindowData, refreshThreadUsage, updateContextUsage, updateCompactedContextUsage } = useContextWindow({
    getCurrentThreadId: currentThreadId,
    getStreaming: streaming,
    getSelectedCard: selectedCard,
    getData: data,
    getProvider: provider,
    getModel: model,
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
            || item.name === "plan_mode_respond"
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

  const setWorkspace = (value: WorkspaceRef | WorkspaceBinding | string | null) => {
    setWorkspaceSelection(null);
    setWorkspaceBinding(value && typeof value !== "string" && "execution" in value ? value : null);
    if (value && typeof value !== "string" && "execution" in value) value = value.execution;
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
    setWorkspaceBinding(null);
    setWorkspaceSelection(selection);
    setWorkspaceRef(null);
    setWorkingDir(workspaceSelectionLocator(selection));
  };

  const draftProjectId = () => !currentThreadId() ? location.state?.projectId : undefined;
  const workspaceRuntime = useWorkspaceRuntime(currentThreadId, setWorkspace, draftProjectId);

  const runtimeDecision = () => workspaceRuntime.payload().runtime?.decision || null;
  const decisionBackendLabel = () => getBackendDisplayName(runtimeDecision()?.backend || "local");
  const decisionBackendEnabled = () => Boolean(runtimeDecision() && isBackendEnabled(runtimeDecision()!.backend));
  // The recovery banner imports backend metadata; make sure the catalog exists
  // even when no workspace picker popover has been opened yet.
  onMount(() => { void fetchCatalog().catch(() => undefined); });
  const runtimeRecoverMessage = () => runtimeDecision()
    ? `Project files are saved. The ${decisionBackendLabel()} environment will be restored automatically when a tool needs to use this workspace.`
    : "";

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

  const resolveWorkspaceForSend = async (isCurrent: () => boolean): Promise<WorkspaceRef | null> => {
    if (currentThreadId()) {
      const latest = await workspaceRuntime.refresh();
      if (!isCurrent()) return null;
      if (workspaceRuntime.busy()) throw new Error("Wait for the workspace backend switch to finish.");
      if (latest?.workspace) return latest.workspace.execution;
    }
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
    if (isCurrent()) {
      if (selection) setDraftBackend(selection.backend);
      setWorkspace(response.workspace);
    }
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

  const applyThreadPayload = (payload: ThreadMessagesPayload, preserveStreamError = false) => batch(() => {
    flushPendingMessageChunks();
    const lastItem = preserveStreamError ? items().at(-1) : undefined;
    const streamErrors = lastItem?.type === "message" && lastItem.role === "error"
      ? [lastItem]
      : [];
    scroll.clearTurnAnchor();
    setThreadData(payload);
    restoreSelection(payload);
    setThreadTitleOverride(payload.title || "");
    setCurrentThreadId(payload.thread_id);
    setActiveCheckpointId(payload.active_checkpoint_id || "");
    setWorkspace(payload.workspace || null);
    if (!persistedStreams.has(payload.thread_id)) {
      const historyItems = [
        ...toThreadTranscript(payload.messages, payload.thread_id),
        ...(payload.pending_requests || []).map((value) => {
          const request = normalizeUserInputRequest(value);
          return createTranscriptUserInputRequest({ toolCallId: request.tool_call_id, interruptId: request.interrupt_id,
            title: request.title, questions: request.questions, submitLabel: request.submit_label });
        }),
      ].map((item) => ({ ...item, id: allocItemId() }));
      // Restore continuation from the saved graph state on every thread load.
      // Keep a local transport error only when there is no checkpoint error.
      const savedErrors = payload.resume_error ? [{
        id: allocItemId(),
        type: "message" as const,
        role: "error" as const,
        text: payload.resume_error.message || payload.resume_error.content,
        resumable: true,
      }] : streamErrors;
      setItems([...historyItems, ...savedErrors]);
    }
    scroll.setAutoScroll(true);
    scroll.scrollToBottom(true);
  });

  const onThreadCreated = (threadId: string, streamThreadIdRef: StreamThreadIdRef) => batch(() => {
    // Apply buffered deltas before the persisted stream entry is re-keyed below.
    flushPendingMessageChunks();
    const oldStreamTid = streamThreadIdRef.id;
    const isViewingPending = !isUnmounting && currentStreamThreadId() === oldStreamTid;

    if (isViewingPending) {
      loadedThreadId = threadId;
      loadedNewChatId = "";
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
            workspace: streamThreadIdRef.workspace,
            agent_name: persistedStreams.get(threadId)?.selection?.agentName,
          },
        }),
      );
    }

    if (isViewingPending) {
      navigate(`/c/${encodeURIComponent(threadId)}`, { replace: true });
    }
  });

  const streamCallbacks: StreamCallbacks = {
    appendItem,
    updateMessage,
    replaceMessage,
    removeItem,
    updateToolResult,
    updatePlanReviewResult,
    updateUserInputRequestResult,
    onError: (message) => showToast(message, "error"),
    setRecursionLimitReached: (v, threadId) => {
      if (!isUnmounting && threadId === currentThreadId()) setRecursionLimitReached(v);
    },
    onThreadCreated,
    onContextUsage: updateContextUsage,
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
  const workspaceLocked = createMemo(() => Boolean((currentThreadId() || draftProjectId()) && workingDir().trim()));
  const composerDisabled = createMemo(() => (
    conversationBusy() || hasPendingInterrupt() || compacting() || workspaceRuntime.busy()
  ));
  const inputDisabled = createMemo(() => (
    threadLoading() || hasPendingInterrupt() || compacting() || workspaceRuntime.busy()
  ));
  const userInputRequestDisabled = createMemo(() => conversationBusy() || compacting());

  const loadThread = async (threadId: string, checkpointId = "") => {
    const requestId = threadLoadRequestId + 1;
    threadLoadRequestId = requestId;
    threadLoadController?.abort();
    const requestController = new AbortController();
    threadLoadController = requestController;
    setCurrentThreadId(threadId);
    if (threadData()?.thread_id !== threadId) setThreadData(undefined);
    setThreadError("");
    setRecursionLimitReached(window.localStorage.getItem(recursionLimitStorageKey(threadId)) === "true");
    if (!persistedStreams.has(threadId) && (checkpointId || threadData()?.thread_id !== threadId)) {
      setThreadLoading(true);
      setItems([]);
    }
    closeBackgroundStream();
    setBackgroundTask(null);
    scroll.setAutoScroll(true);

    try {
      const payload = await apiFetch<ThreadMessagesPayload>(
        threadApiPath(threadId, checkpointId),
        { signal: requestController.signal },
      );
      if (isUnmounting || requestId !== threadLoadRequestId) {
        return;
      }
      applyThreadPayload(payload);
      if (payload.thread_id !== threadId && routeThreadId() === threadId) {
        loadedThreadId = payload.thread_id;
        const checkpointQuery = checkpointId
          ? `?checkpoint_id=${encodeURIComponent(checkpointId)}`
          : "";
        navigate(`${chatThreadHref(payload.thread_id)}${checkpointQuery}`, { replace: true });
      }
      if (payload.kind === "background") {
        openBackgroundStream(payload.thread_id);
      } else {
        closeBackgroundStream();
        setBackgroundTask(null);
      }
    } catch (err) {
      if (isUnmounting || requestId !== threadLoadRequestId) {
        return;
      }
      setThreadError(err instanceof Error ? err.message : "Failed to load thread");
    } finally {
      if (!isUnmounting && requestId === threadLoadRequestId) {
        setThreadLoading(false);
      }
    }
  };

  const refreshThread = async (threadId: string, checkpointId = "") => {
    const requestId = ++threadLoadRequestId;
    threadLoadController?.abort();
    const requestController = new AbortController();
    threadLoadController = requestController;
    try {
      const payload = await apiFetch<ThreadMessagesPayload>(
        threadApiPath(threadId, checkpointId),
        { signal: requestController.signal },
      );
      if (!isUnmounting && requestId === threadLoadRequestId
        && payload.thread_id === currentThreadId() && !streaming()) {
        applyThreadPayload(payload, true);
      }
    } catch (err) {
      if (!isUnmounting && requestId === threadLoadRequestId && !requestController.signal.aborted) {
        showToast(err instanceof Error ? err.message : "Failed to refresh thread", "error");
      }
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
      new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_AGENT_UPDATED, {
        detail: {
          threadId,
          agent_name: nextAgentName,
        },
      }),
    );
  };

  createEffect(on(data, () => restoreSelection(threadData())));

  const handleAgentChange = (nextAgentName: string) => batch(() => {
    setAgentName(nextAgentName);
    const card = validCards().find((item) => item.name === nextAgentName);
    if (card) {
      const selection = fallbackModelSelection(card);
      setProvider(selection.provider);
      setModel(selection.model);
      setCustomModel(selectedModelIsCustom());
    }
    rememberSelection();
    updateCurrentThreadAgent(nextAgentName);
  });

  createEffect(on(
    [routeThreadId, () => !routeThreadId() ? location.state?.newChatId || "" : ""],
    ([threadId, newChatId]) => {
      if (threadId === loadedThreadId && newChatId === loadedNewChatId) return;
      if (loadedThreadId !== null) {
        promptDrafts.set(loadedThreadId || loadedNewChatId || "__new__", prompt());
      }
      flushPendingMessageChunks();
      loadedThreadId = threadId;
      loadedNewChatId = newChatId;
      // Reset effort on conversation switches, while thread creation keeps the same scope.
      setReasoningScope((scope) => scope + 1);
      threadLoadRequestId += 1;
      compactRequestId += 1;
      threadLoadController?.abort();
      clearAllAttachments();
      closeBackgroundStream();
      setBackgroundTask(null);
      setRecursionLimitReached(false);
      setActiveSession(null);
      setThreadTitleOverride("");
      setActiveCheckpointId("");
      setThreadData(undefined);
      setThreadError("");
      setViewingMessage(null);
      setTodosExpanded(false);
      setCompacting(false);
      setPrompt(promptDrafts.get(threadId || newChatId || "__new__") || "");
      setWorkspace(!threadId && newChatId ? location.state?.workspaceBinding || location.state?.workspace || null : null);
      setDraftBackend("local");
      setCurrentThreadId(threadId);
      setCurrentStreamThreadId(persistedStreams.has(threadId) ? threadId : null);
      restoreSelection();
      scroll.clearTurnAnchor();
      setThreadLoading(false);
      if (threadId) void loadThread(threadId);
      else setItems([]);
    },
  ));

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
      reasoning_effort: requestEffort(),
    };
    if (provider()) {
      payload.provider = provider();
    }
    if (model()) {
      payload.model = model();
    }
    const workspace = resolvedWorkspace || workspaceRef() || localWorkspaceRef(workingDir());
    if (workspace) {
      payload.workspace = workspaceBinding() || workspace;
    }
    if (currentThreadId()) {
      payload.thread_id = currentThreadId();
    } else {
      payload.new_thread = true;
      if (draftProjectId()) payload.execution_backend = draftBackend();
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
  ) => {
    const callbacks: StreamCallbacks = {
      ...streamCallbacks,
      onError: (message) => {
        if (!isUnmounting && currentStreamThreadId() === streamThreadIdRef.id) showToast(message, "error");
      },
    };
    return (event: Record<string, unknown>) =>
      handleStreamEvent(event, assistantIdRef, streamedRef, streamThreadIdRef, callbacks);
  };

  const markLocalStreamFinished = (threadId: string) => {
    latestLocalStreamFinish = { threadId, finishedAt: Date.now() };
  };

  const finishLocalStream = (threadId: string) => batch(() => {
    flushPendingMessageChunks();
    markLocalStreamFinished(threadId);
    setStreaming(false, threadId);
    setController(null, threadId);
    const isViewing = currentStreamThreadId() === threadId;
    const entry = persistedStreams.get(threadId);
    if (entry) {
      if (isViewing && !isUnmounting) setLocalItems(entry.items[0]());
      cleanupStreamSignals(threadId);
    }
    if (isViewing) setCurrentStreamThreadId(null);
  });

  const shouldSkipStopReload = (threadId: string) => {
    if (streaming()) {
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

    // Capture the request before workspace resolution yields to navigation.
    const payload = buildPayload(message, resume ? [] : attachedFiles, undefined, effectiveAgentName);
    if (!resume) {
      const selection = selectedModelSelection();
      if (selection) writeChatModelPreference({
        ...selection,
        agentName: effectiveAgentName,
        ...(customModel() && selection.provider !== "@pool"
          ? { customModel: true as const } : {}),
      });
    }
    if (resume) {
      payload.resume = true;
      payload.message = "";
      if (resumePayload) payload.resume_payload = resumePayload;
    } else if (payload.thread_id && activeCheckpointId()) {
      payload.checkpoint_id = activeCheckpointId();
    }
    threadLoadRequestId += 1;
    compactRequestId += 1;
    threadLoadController?.abort();

    setRecursionLimitReached(false);
    const activeTid = currentThreadId();
    if (activeTid) {
      window.localStorage.removeItem(recursionLimitStorageKey(activeTid));
    }

    const streamThreadIdRef: StreamThreadIdRef = {
      id: currentThreadId() || `__pending__${allocItemId()}`,
      message,
      workspace: workspaceExecution(payload.workspace),
    };

    getOrCreateStreamSignals(streamThreadIdRef.id, items()).selection = rememberSelection();
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
    if (startTid && !startTid.startsWith("__pending__")) {
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
      needsWorkspaceResolution || (!currentThreadId() && draftBackend() !== "local" && Boolean(workspaceBinding()))
      || workspaceNeedsEnvironmentInitialization(currentWorkspace, initializedWorkspaces())
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
        ? streamThreadIdRef.workspace
        : await resolveWorkspaceForSend(() => !isUnmounting && currentStreamThreadId() === streamThreadIdRef.id);
      abortController.signal.throwIfAborted();
      streamThreadIdRef.workspace = resolvedWorkspace;
      if (resolvedWorkspace) payload.workspace = workspaceBinding() || resolvedWorkspace;
      if (!resume && !currentThreadId() && workspaceBinding()) payload.execution_backend = draftBackend();
      if (statusMessageId !== null && !needsEnvironmentInitialization) {
        replaceMessage(statusMessageId, THINKING_TEXT, streamThreadIdRef.id);
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
          resumable: resume,
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
      const finishedTid = streamThreadIdRef.id;
      finishLocalStream(finishedTid);
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
    if (isUnmounting || threadId !== currentThreadId() || threadLoading()) return;
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
    getOrCreateStreamSignals(streamThreadIdRef.id, items()).selection = rememberSelection();
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
      const finishedTid = streamThreadIdRef.id;
      finishLocalStream(finishedTid);
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
    if (!currentThreadId() || conversationBusy() || compacting()) {
      return;
    }
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
        setCustomModel(selectedModelIsCustom());
      }
    }
    rememberSelection();
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
    threadLoadRequestId += 1;
    threadLoadController?.abort();
    getOrCreateStreamSignals(streamThreadIdRef.id, nextItems).selection = rememberSelection();
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
          reasoning_effort: requestEffort(),
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
      const finishedTid = streamThreadIdRef.id;
      finishLocalStream(finishedTid);
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

  let sessionEventRevision = 0;
  const handleSessionStartedOrUpdated = (event: Event) => {
    const customEvent = event as CustomEvent<ActiveSession>;
    const session = customEvent.detail;
    if (session && session.thread_id === currentThreadId()) {
      sessionEventRevision += 1;
      setActiveSession(session);
      if (!streaming() && !threadLoading() && threadData() && !isBackgroundThread()) {
        void reconnectStream(session.thread_id);
      }
    }
  };

  const handleSessionStopped = (event: Event) => {
    const customEvent = event as CustomEvent<{ session_id: string; thread_id: string }>;
    const { thread_id } = customEvent.detail;
    if (thread_id === currentThreadId()) {
      sessionEventRevision += 1;
      setActiveSession(null);
      if (shouldSkipStopReload(thread_id)) {
        return;
      }
      void loadThread(thread_id);
    }
  };

  createEffect(on(
    [currentThreadId, () => threadData()?.thread_id || "", isBackgroundThread],
    ([threadId, readyThreadId, backgroundThread]) => {
      let cancelled = false;
      const revision = sessionEventRevision;
      const requestController = new AbortController();
      onCleanup(() => {
        cancelled = true;
        requestController.abort();
      });
      if (threadId && readyThreadId === threadId) {
        const fetchInitialSession = async () => {
          try {
            const payload = await apiFetch<{ sessions: ActiveSession[] }>("/dashboard-api/sessions", {
              signal: requestController.signal,
            });
            const found = payload.sessions.find((s) => s.thread_id === threadId);
            if (!cancelled && !isUnmounting && revision === sessionEventRevision && threadId === currentThreadId()) {
              setActiveSession(found || null);
              if (found && !streaming() && threadData() && !backgroundThread) {
                void reconnectStream(threadId);
              }
            }
          } catch (err) {
            if (!requestController.signal.aborted) console.error("Failed to fetch initial session details", err);
          }
        };
        void fetchInitialSession();
      } else {
        setActiveSession(null);
      }
    },
  ));

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
    threadLoadRequestId += 1;
    threadLoadController?.abort();
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

    const requestId = ++compactRequestId;
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
        context_breakdown?: unknown;
        context_updated_at?: string;
        context_window?: number;
        reserved_tokens?: number;
      }>(`/dashboard-api/chat-history/${encodeURIComponent(threadId)}/compact`, {});

      if (isUnmounting || requestId !== compactRequestId || currentThreadId() !== threadId) {
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
        updateCompactedContextUsage(
          payload.current_context_tokens ?? payload.retained_tokens, threadId, payload.context_breakdown,
          payload.context_window ? { context_window: payload.context_window,
            context_updated_at: payload.context_updated_at, reserved_tokens: payload.reserved_tokens } : undefined,
        );
        void refreshThreadUsage(threadId);
        showToast(
          `Conversation compacted! Summarized ${payload.compacted_count} earlier message(s).`,
          "success",
        );
      } else {
        showToast("Failed to compact conversation: invalid server response.", "error");
      }
    } catch (err) {
      if (isUnmounting || requestId !== compactRequestId) return;
      const message = err instanceof Error ? err.message : "Failed to compact conversation.";
      showToast(message, "error");
    } finally {
      if (!isUnmounting && requestId === compactRequestId) setCompacting(false);
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
            class={`chat-shell chat-shell-resizable ${explorer.open() ? "workspace-open" : "workspace-closed"} ${explorer.fullscreen() ? "workspace-fullscreen" : ""} ${explorer.resizing() ? "workspace-resizing" : ""}`}
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
                  <ComposerWorkspacePicker
                    threadId={currentThreadId()}
                    workingDir={workingDir()}
                    defaultWorkingDir={defaultWorkingDir()}
                    workspace={workspaceRef()}
                    selection={workspaceSelection()}
                    locked={workspaceLocked()}
                    disabled={conversationBusy()}
                    runtimePayload={workspaceRuntime.payload()}
                    runtimeBusy={workspaceRuntime.busy()}
                    runtimeError={workspaceRuntime.error()}
                    draft={!currentThreadId() && Boolean(draftProjectId())}
                    draftBackend={draftBackend()}
                    onSwitchBackend={async (backend, token) => {
                      if (!currentThreadId()) setDraftBackend(backend);
                      else await workspaceRuntime.switchBackend(backend, token);
                    }}
                    onAbandonBackend={workspaceRuntime.abandon}
                    onSelectionChange={setWorkspaceDraft}
                  />
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
              <Show when={runtimeDecision()}>
                <div class="thread-banner" role="alert">
                  <span>{runtimeRecoverMessage()}</span>
                  <div class="thread-banner-actions">
                    <Show when={decisionBackendEnabled()}>
                      <button class="btn btn-primary" type="button" disabled={workspaceRuntime.busy()}
                        onClick={() => void workspaceRuntime.confirmRecovery("restore")}>
                        {`Restore on ${decisionBackendLabel()}`}
                      </button>
                    </Show>
                    <Show when={workspaceRuntime.payload().sync_policy !== "manual_apply"}><button class="btn" type="button" disabled={workspaceRuntime.busy()}
                      onClick={() => void workspaceRuntime.confirmRecovery("local")}>
                      Move to local workspace
                    </button></Show>
                  </div>
                  <Show when={workspaceRuntime.payload().sync_policy === "manual_apply"}>
                    <ProjectLocalSync projectId={workspaceRuntime.payload().workspace?.project_id || undefined}
                      workspaceId={workspaceRuntime.payload().workspace?.project_workspace_id}
                      generation={workspaceRuntime.payload().workspace?.runtime_generation || 1}
                      localLink={workspaceRuntime.payload().local_link} disabled={workspaceRuntime.busy()}
                      onApplied={() => void workspaceRuntime.refresh()}
                      onContinue={(token) => workspaceRuntime.confirmRecovery("local", token)} />
                  </Show>
                </div>
              </Show>
              <ChatTranscript
                setTranscriptRef={(el) => {
                  transcriptRef = el;
                  scroll.observeTranscript(el);
                }}
                onScroll={handleTranscriptScroll}
                onUserScroll={scroll.handleUserScroll}
                items={items()}
                filteredItems={filteredItems()}
                threadLoading={threadLoading()}
                currentThreadId={currentThreadId()}
                streaming={streaming()}
                backgroundLive={backgroundLive()}
                compacting={compacting()}
                showScrollToBottom={showScrollToBottom()}
                turnAnchorSpacerHeight={turnAnchorSpacerHeight()}
                onScrollToBottomClick={handleScrollToBottomClick}
                conversationBusy={conversationBusy()}
                agents={validCards()}
                activeAgentName={agentName()}
                onSelectPrompt={handleSelectPrompt}
                onResume={handleResume}
                onEditMessage={(payload) => void handleEditMessage(payload)}
                onRegenerateMessage={handleRegenerateMessage}
                onBranchSelect={handleBranchSelect}
                onApprovePlanReview={handleApprovePlanReview}
                onRevisePlanReview={handleRevisePlanReview}
                onMessageClick={setViewingMessage}
              />
              <ChatComposer
                workspace={workspaceRef() || localWorkspaceRef(workingDir())}
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
                reasoningEffort={reasoningEffort()}
                reasoningEffortLevels={effortLevels()}
                reasoningEffortDefault={selectedModelOption()?.reasoning_effort_default}
                onReasoningEffortChange={setReasoningEffort}
                onProviderModelChange={handleProviderModelChange}
                payload={payload()}
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
            <div class={`workspace-drawer ${explorer.fullscreen() ? "workspace-drawer-fullscreen" : ""}`} aria-hidden={!explorer.open()}>
              <Show when={!explorer.fullscreen()}>
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
              </Show>
              <WorkspaceExplorer
                binding={workspaceBinding()}
                visible={explorer.open()}
                fullscreen={explorer.fullscreen()}
                splitView={true}
                onToggleFullscreen={isDrawerActive() ? undefined : explorer.toggleFullscreen}
                running={conversationBusy()}
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
              "font-size": "var(--font-size-chat)",
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
                  "font-size": "var(--font-size-chat)",
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
                        "font-size": "var(--font-size-chat)",
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
