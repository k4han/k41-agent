import { useNavigate, useSearchParams } from "@solidjs/router";
import { Bot, RefreshCw, Send, Square, Trash2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { ModelPicker } from "@/components/ModelPicker";
import { DataGate } from "@/components/State";
import {
  createTranscriptTool,
  findTranscriptToolTarget,
  TranscriptItemView,
} from "@/components/Transcript";
import { useToast } from "@/components/Toast";
import { apiFetch, readError } from "@/lib/api";
import {
  threadApiPath,
  toThreadTranscript,
} from "@/lib/chatThreads";
import { truncateText } from "@/lib/utils";
import type { TranscriptItem } from "@/components/Transcript";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import type { AgentCard, AgentsPayload } from "@/types";

type ChatTranscriptItem = TranscriptItem & { id: number; key?: string };

let nextItemId = 1;

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
  const [prompt, setPrompt] = createSignal("");
  const [items, setItems] = createSignal<ChatTranscriptItem[]>([]);
  const [streaming, setStreaming] = createSignal(false);
  const [controller, setController] = createSignal<AbortController | null>(null);
  const { showToast } = useToast();
  let transcriptRef: HTMLDivElement | undefined;
  let loadedThreadId: string | null = null;
  let threadLoadRequestId = 0;

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<AgentsPayload>("/dashboard-api/agents"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chat options");
    }
  };

  const validCards = createMemo(() => (data()?.cards || []).filter((card) => card.valid));
  const selectedCard = createMemo<AgentCard | undefined>(() =>
    validCards().find((card) => card.name === agentName()),
  );
  const pageSubtitle = createMemo(() => (
    currentThreadId()
      ? `Continue thread ${truncateText(currentThreadId(), 88)}`
      : "Stream an agent response with visible tool calls."
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

  const loadThread = async (threadId: string) => {
    const requestId = threadLoadRequestId + 1;
    threadLoadRequestId = requestId;
    setCurrentThreadId(threadId);
    setThreadData(undefined);
    setThreadError("");
    setThreadLoading(true);
    setItems([]);

    try {
      const payload = await apiFetch<ThreadMessagesPayload>(threadApiPath(threadId));
      if (requestId !== threadLoadRequestId) {
        return;
      }
      setThreadData(payload);
      setCurrentThreadId(payload.thread_id);
      setItems(
        toThreadTranscript(payload.messages).map((item) => ({
          ...item,
          id: nextItemId++,
        })),
      );
      scrollToBottom();
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

    if (!threadId) {
      setCurrentThreadId("");
      setThreadData(undefined);
      setThreadError("");
      setThreadLoading(false);
      setItems([]);
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

  const buildPayload = (message: string) => {
    const payload: Record<string, string | boolean> = {
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
    if (currentThreadId()) {
      payload.thread_id = currentThreadId();
    } else {
      payload.new_thread = true;
    }
    return payload;
  };

  const handleEvent = (
    event: Record<string, unknown>,
    assistantIdRef: { id: number | null },
    streamedRef: { received: boolean },
  ) => {
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
  };

  const sendMessage = async () => {
    const message = prompt().trim();
    if (!message) {
      showToast("Enter a message.", "warning");
      return;
    }
    if (threadLoading()) {
      showToast("Wait for the thread to load.", "warning");
      return;
    }
    if (!agentName()) {
      showToast("No valid agent is available.", "error");
      return;
    }

    appendItem({ type: "message", role: "user", text: message });
    setPrompt("");
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
        body: JSON.stringify(buildPayload(message)),
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
  const resetChat = () => {
    setItems([]);
    if (currentThreadId()) {
      navigate("/chat");
    }
  };

  onMount(load);

  return (
    <AppShell
      title={currentThreadId() ? "Thread Chat" : "Agent Chat"}
      subtitle={pageSubtitle()}
      actions={
        <button class="btn" type="button" onClick={load}>
          <RefreshCw size={14} />
          Refresh Options
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="chat-shell">
            <aside class="panel">
              <div class="panel-header">
                <div class="panel-title">Run Settings</div>
              </div>
              <div class="panel-body stack">
                <div class="field">
                  <label>Agent</label>
                  <select
                    class="select"
                    value={agentName()}
                    disabled={streaming()}
                    onChange={(event) => setAgentName(event.currentTarget.value)}
                  >
                    <For each={validCards()}>
                      {(card) => <option value={card.name}>{card.display_name || card.name}</option>}
                    </For>
                  </select>
                </div>
                <div class="field">
                  <label>Provider / Model</label>
                  <ModelPicker
                    catalogs={payload.model_catalogs}
                    providerNames={payload.provider_names}
                    defaultProvider={payload.default_provider}
                    provider={provider()}
                    model={model()}
                    disabled={streaming()}
                    onChange={(nextProvider, nextModel) => {
                      setProvider(nextProvider);
                      setModel(nextModel);
                    }}
                  />
                </div>
                <div class="panel">
                  <div class="panel-body">
                    <div class="row">
                      <Bot size={14} />
                      <strong>{selectedCard()?.display_name || selectedCard()?.name || "No agent"}</strong>
                    </div>
                    <p class="hint">{selectedCard()?.description || "No description."}</p>
                    <div class="chips">
                      <span class="chip">{selectedCard()?.graph_type || "default"}</span>
                      <span class="chip">{selectedCard()?.provider || "default"}</span>
                    </div>
                  </div>
                </div>
              </div>
            </aside>

            <section class="panel chat-panel">
              <Show when={currentThreadId() || threadError()}>
                <div class={`thread-banner ${threadError() ? "thread-banner-error" : ""}`}>
                  <div class="row-wrap">
                    <span class="badge">{threadData()?.platform || "thread"}</span>
                    <span class="mono">{truncateText(currentThreadId(), 84)}</span>
                  </div>
                  <Show when={threadError()}>
                    <span>{threadError()}</span>
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
              <div class="composer stack">
                <textarea
                  class="textarea"
                  rows={4}
                  value={prompt()}
                  disabled={streaming() || threadLoading()}
                  placeholder={currentThreadId() ? "Continue this thread..." : "Ask the selected agent something..."}
                  onInput={(event) => setPrompt(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.ctrlKey && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                />
                <div class="split">
                  <span class="hint">Press Enter to send, Ctrl + Enter for new line.</span>
                  <div class="row-wrap">
                    <button class="btn" type="button" onClick={resetChat} disabled={streaming()}>
                      <Trash2 size={14} />
                      {currentThreadId() ? "New Chat" : "Reset"}
                    </button>
                    <Show
                      when={streaming()}
                      fallback={
                        <button class="btn btn-primary" type="button" onClick={sendMessage} disabled={threadLoading()}>
                          <Send size={14} />
                          Send
                        </button>
                      }
                    >
                      <button class="btn btn-warning" type="button" onClick={stopChat}>
                        <Square size={14} />
                        Stop
                      </button>
                    </Show>
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
