import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import {
  GripVertical,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-solid";
import { createEffect, createMemo, createSignal, onCleanup, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { ChatComposer } from "@/components/ChatComposer";
import { ChatThreadBadges } from "@/components/ChatThreadBadges";
import { ChatTranscript } from "@/components/ChatTranscript";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import type { TodoProgress } from "@/components/ChatTodos";
import { apiFetch, postJson, readError } from "@/lib/api";
import {
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import { localWorkspaceRef } from "@/lib/workspace";
import type {
  ActiveSession,
  AgentCard,
  AgentsPayload,
  ModelOption,
  WorkspaceRef,
} from "@/types";
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
import {
  type ChatAttachmentPayload,
  type ChatPayload,
  type DefaultWorkspacePayload,
  type WorkspaceResolvePayload,
  ACTIVE_TASK_STATUSES,
  ATTACHMENT_ACCEPT,
  DEFAULT_ATTACHMENT_MESSAGE,
} from "@/lib/chatTypes";
import { useChatScroll } from "@/lib/useChatScroll";
import { useChatStreams } from "@/lib/useChatStreams";
import { useWorkspaceExplorer } from "@/lib/useWorkspaceExplorer";
import { useChatAttachments } from "@/lib/useChatAttachments";
import { useContextWindow } from "@/lib/useContextWindow";
import { useBackgroundStream } from "@/lib/useBackgroundStream";

export function ChatPage() {
  const navigate = useNavigate();
  const params = useParams<{ threadId?: string }>();
  const [searchParams] = useSearchParams();
  const routeThreadId = () => (params.threadId ? decodeURIComponent(params.threadId) : "");

  const { showToast } = useToast();

  const [data, setData] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [threadData, setThreadData] = createSignal<ThreadMessagesPayload>();
  const [threadError, setThreadError] = createSignal("");
  const [threadLoading, setThreadLoading] = createSignal(false);
  const [currentThreadId, setCurrentThreadId] = createSignal("");
  const [activeCheckpointId, setActiveCheckpointId] = createSignal("");

  const [agentName, setAgentName] = createSignal("");
  const [provider, setProvider] = createSignal("default");
  const [model, setModel] = createSignal("");
  const [workingDir, setWorkingDir] = createSignal("");
  const [workspaceRef, setWorkspaceRef] = createSignal<WorkspaceRef | null>(null);
  const [defaultWorkingDir, setDefaultWorkingDir] = createSignal("");
  const [defaultWorkspace, setDefaultWorkspace] = createSignal<WorkspaceRef | null>(null);
  const [prompt, setPrompt] = createSignal("");
  const [todosExpanded, setTodosExpanded] = createSignal(true);
  const [recursionLimitReached, setRecursionLimitReached] = createSignal(false);
  const [activeSession, setActiveSession] = createSignal<ActiveSession | null>(null);

  let transcriptRef: HTMLDivElement | undefined;
  let chatShellRef: HTMLDivElement | undefined;
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
    updateToolResult,
  } = streams;

  const attach = useChatAttachments({
    getModelSupportsImage: modelSupportsImage,
    showToast,
  });
  const { attachments, addFiles, removeAttachment, clearAttachments, clearAllAttachments } = attach;

  const { contextWindowData } = useContextWindow({
    getCurrentThreadId: currentThreadId,
    getStreaming: streaming,
    getSelectedCard: selectedCard,
    getData: data,
    getProvider: provider,
    getModel: model,
    getAttachments: attachments,
    getItems: items,
  });

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

  const applyThreadPayload = (payload: ThreadMessagesPayload) => {
    scroll.clearTurnAnchor();
    setThreadData(payload);
    setCurrentThreadId(payload.thread_id);
    setActiveCheckpointId(payload.active_checkpoint_id || "");
    setWorkspace(payload.workspace || null);
    if (!persistedStreams.has(payload.thread_id)) {
      setItems(
        toThreadTranscript(payload.messages).map((item) => ({
          ...item,
          id: allocItemId(),
        })),
      );
    }
    scroll.setAutoScroll(true);
    scroll.scrollToBottom(true);
  };

  const onThreadCreated = (threadId: string, streamThreadIdRef: StreamThreadIdRef) => {
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
  };

  const streamCallbacks: StreamCallbacks = {
    appendItem,
    updateMessage,
    updateToolResult,
    onError: (message) => showToast(message, "error"),
    setRecursionLimitReached: (v) => setRecursionLimitReached(v),
    onThreadCreated,
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

  const pageSubtitle = createMemo(() => (
    currentThreadId()
      ? "Continue this thread."
      : "Choose a workspace, then stream an agent response with visible tool calls."
  ));
  const isBackgroundThread = createMemo(() => threadData()?.kind === "background");
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

  const loadThread = async (threadId: string, checkpointId = "") => {
    const requestId = threadLoadRequestId + 1;
    threadLoadRequestId = requestId;
    setCurrentThreadId(threadId);
    setThreadData(undefined);
    setThreadError("");
    setRecursionLimitReached(window.localStorage.getItem(`kaka:recursion-limit-reached:${threadId}`) === "true");
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
      setActiveCheckpointId("");
      setThreadData(undefined);
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

    const assistantIdRef: AssistantIdRef = { id: null };
    const streamedRef: StreamedRef = { received: false };

    try {
      const payload = buildPayload(message, resume ? [] : attachedFiles);
      if (resume) {
        (payload as any).resume = true;
        payload.message = "";
      } else if (currentThreadId() && activeCheckpointId()) {
        payload.checkpoint_id = activeCheckpointId();
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
          text: err instanceof Error ? err.message : "Chat failed",
        }, "bottom", streamThreadIdRef.id);
      }
    } finally {
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
          new CustomEvent("kaka:thread-stop-running", {
            detail: { threadId: finishedTid },
          }),
        );
      }
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
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

  const handleBranchSelect = (checkpointId: string) => {
    const threadId = currentThreadId();
    if (!threadId || !checkpointId || checkpointId === activeCheckpointId() || conversationBusy()) {
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
    if (conversationBusy()) {
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
    window.localStorage.removeItem(`kaka:recursion-limit-reached:${threadId}`);

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
      new CustomEvent("kaka:thread-start-running", {
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
      const response = await fetch("/api/chat/events/edit", {
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
        new CustomEvent("kaka:thread-stop-running", {
          detail: { threadId: finishedTid },
        }),
      );
      window.dispatchEvent(new CustomEvent("kaka:threads-changed"));
      if (finishedTid && !isUnmounting && currentThreadId() === finishedTid) {
        void refreshThread(finishedTid);
      }
    }
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
    if (stoppedId === currentThreadId() && !shouldSkipStopReload(stoppedId)) {
      void loadThread(stoppedId);
    }
  };

  onMount(() => {
    isUnmounting = false;
    cleanupStaleStreams();

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
    const activeTid = currentStreamThreadId();
    if (!activeTid || !persistedStreams.has(activeTid)) {
      controller()?.abort();
    }
    window.removeEventListener("kaka:thread-external-abort", handleExternalAbort);
    window.removeEventListener("kaka:thread-stop-running", handleThreadStopRunningExternal);
    window.removeEventListener("kaka:session-started", handleSessionStartedOrUpdated);
    window.removeEventListener("kaka:session-updated", handleSessionStartedOrUpdated);
    window.removeEventListener("kaka:session-stopped", handleSessionStopped);
  });

  return (
    <AppShell
      title={currentThreadId() ? "Thread Chat" : "Agent Chat"}
      subtitle={
        <span class="row-wrap" style="gap: 8px; align-items: center; flex-wrap: wrap; display: inline-flex;">
          <span>{pageSubtitle()}</span>
          <ChatThreadBadges
            isBackgroundThread={isBackgroundThread()}
            backgroundTask={backgroundTask()}
            backgroundLive={backgroundLive()}
            backgroundSession={backgroundSession()}
            activeSession={activeSession()}
          />
        </span>
      }
      actions={
        <>
          <button
            class={`btn btn-icon ${explorer.open() ? "active" : ""}`}
            type="button"
            onClick={explorer.toggle}
            title={explorer.open() ? "Hide workspace explorer" : "Show workspace explorer"}
            aria-label={
              explorer.open() ? "Hide workspace explorer" : "Show workspace explorer"
            }
            aria-pressed={explorer.open()}
          >
            <Show when={explorer.open()} fallback={<PanelRightOpen size={15} />}>
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
            class={`chat-shell chat-shell-resizable ${explorer.open() ? "workspace-open" : "workspace-closed"} ${explorer.resizing() ? "workspace-resizing" : ""}`}
            style={`--workspace-explorer-width: ${explorer.width()}px;`}
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
              <ChatTranscript
                setTranscriptRef={(el) => (transcriptRef = el)}
                onScroll={handleTranscriptScroll}
                items={items()}
                filteredItems={filteredItems()}
                threadLoading={threadLoading()}
                currentThreadId={currentThreadId()}
                streaming={streaming()}
                backgroundLive={backgroundLive()}
                autoScroll={autoScroll()}
                turnAnchorSpacerHeight={turnAnchorSpacerHeight()}
                onScrollToBottomClick={handleScrollToBottomClick}
                workingDir={workingDir()}
                defaultWorkingDir={defaultWorkingDir()}
                workspace={workspaceRef()}
                conversationBusy={conversationBusy()}
                onWorkspaceResolved={setWorkspace}
                onEditMessage={(payload) => void handleEditMessage(payload)}
                onBranchSelect={handleBranchSelect}
              />
              <ChatComposer
                prompt={prompt()}
                onPromptChange={setPrompt}
                onSend={() => void sendMessage()}
                onStop={stopChat}
                onResume={handleResume}
                onAddFiles={addFiles}
                onRemoveAttachment={removeAttachment}
                streaming={streaming()}
                composerDisabled={composerDisabled()}
                inputDisabled={inputDisabled()}
                workspaceMissing={workspaceMissing()}
                backgroundTaskActive={backgroundTaskActive()}
                currentThreadId={currentThreadId()}
                attachments={attachments()}
                attachmentAccept={attachmentAccept()}
                agentName={agentName()}
                agents={validCards()}
                onAgentChange={setAgentName}
                selectedCard={selectedCard()}
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
              />
            </section>
            <div class="workspace-drawer" aria-hidden={!explorer.open()}>
              <button
                class="workspace-resize-handle"
                type="button"
                onPointerDown={explorer.startResize}
                title="Resize workspace explorer"
                aria-label="Resize workspace explorer"
              >
                <GripVertical size={15} />
              </button>
              <WorkspaceExplorer
                threadId={currentThreadId()}
                workingDir={workingDir()}
                workspace={workspaceRef()}
                disabled={
                  conversationBusy()
                  || !(workspaceRef()?.locator || workingDir()).trim()
                  || !currentThreadId()
                  || (workspaceLocked() && workspaceRef()?.backend !== "modal")
                }
                onWorkingDirChange={setWorkspace}
              />
            </div>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
