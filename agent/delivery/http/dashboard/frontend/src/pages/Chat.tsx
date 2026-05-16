import { useSearchParams } from "@solidjs/router";
import { Bot, RefreshCw, Send, Square, Trash2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import {
  createTranscriptTool,
  findTranscriptToolTarget,
  TranscriptItemView,
} from "@/components/Transcript";
import { useToast } from "@/components/Toast";
import { apiFetch, readError } from "@/lib/api";
import { uniqueSorted } from "@/lib/utils";
import type { TranscriptItem } from "@/components/Transcript";
import type { AgentCard, AgentsPayload } from "@/types";

type ChatTranscriptItem = TranscriptItem & { id: number };

let nextItemId = 1;

export function ChatPage() {
  const [searchParams] = useSearchParams();
  const [data, setData] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [agentName, setAgentName] = createSignal("");
  const [workflow, setWorkflow] = createSignal("");
  const [provider, setProvider] = createSignal("default");
  const [model, setModel] = createSignal("");
  const [prompt, setPrompt] = createSignal("");
  const [items, setItems] = createSignal<ChatTranscriptItem[]>([]);
  const [streaming, setStreaming] = createSignal(false);
  const [controller, setController] = createSignal<AbortController | null>(null);
  const { showToast } = useToast();
  let transcriptRef: HTMLDivElement | undefined;

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
  const resolvedProvider = createMemo(() =>
    provider() === "default" ? data()?.default_provider || "default" : provider(),
  );
  const modelOptions = createMemo(() => {
    const catalog = data()?.model_catalogs.find((item) => item.provider === resolvedProvider());
    return uniqueSorted([...(catalog?.models.map((entry) => entry.id) || []), catalog?.default_model, selectedCard()?.model, model()]);
  });

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
    setWorkflow("");
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
    const payload: Record<string, string> = {
      message,
      user_id: "dashboard",
      agent_name: agentName(),
    };
    if (workflow()) {
      payload.workflow = workflow();
    }
    if (provider()) {
      payload.provider = provider();
    }
    if (model()) {
      payload.model = model();
    }
    return payload;
  };

  const handleEvent = (
    event: Record<string, unknown>,
    assistantIdRef: { id: number | null },
    streamedRef: { received: boolean },
  ) => {
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
    }
  };

  const stopChat = () => controller()?.abort();
  const resetChat = () => setItems([]);

  onMount(load);

  return (
    <AppShell
      title="Agent Chat"
      subtitle="Stream an agent response with visible tool calls."
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
                  <label>Workflow</label>
                  <select
                    class="select"
                    value={workflow()}
                    disabled={streaming()}
                    onChange={(event) => setWorkflow(event.currentTarget.value)}
                  >
                    <option value="">Use agent default</option>
                    <For each={uniqueSorted([...(payload.workflows || []), selectedCard()?.graph_type])}>
                      {(item) => <option value={item}>{item}</option>}
                    </For>
                  </select>
                </div>
                <div class="field">
                  <label>Provider</label>
                  <select
                    class="select"
                    value={provider()}
                    disabled={streaming()}
                    onChange={(event) => setProvider(event.currentTarget.value)}
                  >
                    <For each={uniqueSorted(["default", ...payload.provider_names, provider()])}>
                      {(item) => <option value={item}>{item}</option>}
                    </For>
                  </select>
                </div>
                <div class="field">
                  <label>Model</label>
                  <input
                    class="input"
                    list="chat-model-options"
                    value={model()}
                    disabled={streaming()}
                    placeholder="Provider default"
                    onInput={(event) => setModel(event.currentTarget.value)}
                  />
                  <datalist id="chat-model-options">
                    <For each={modelOptions()}>
                      {(item) => <option value={item} />}
                    </For>
                  </datalist>
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
              <div class="transcript" ref={transcriptRef}>
                <Show
                  when={items().length > 0}
                  fallback={<div class="empty">Send a message to start a conversation.</div>}
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
                  disabled={streaming()}
                  placeholder="Ask the selected agent something..."
                  onInput={(event) => setPrompt(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.ctrlKey && event.key === "Enter") {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                />
                <div class="split">
                  <span class="hint">Press Ctrl + Enter to send.</span>
                  <div class="row-wrap">
                    <button class="btn" type="button" onClick={resetChat} disabled={streaming()}>
                      <Trash2 size={14} />
                      Reset
                    </button>
                    <button class="btn btn-warning" type="button" onClick={stopChat} disabled={!streaming()}>
                      <Square size={14} />
                      Stop
                    </button>
                    <button class="btn btn-primary" type="button" onClick={sendMessage} disabled={streaming()}>
                      <Send size={14} />
                      Send
                    </button>
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
