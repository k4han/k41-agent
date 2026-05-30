import {
  Bot,
  FileText,
  Image as ImageIcon,
  MoreHorizontal,
  Plus,
  Send,
  Square,
  X,
} from "lucide-solid";
import { createEffect, createSignal, For, Show, type JSX } from "solid-js";

import { ChatTodos, type TodoProgress } from "@/components/ChatTodos";
import { ContextWindowIndicator, type ContextWindowData } from "@/components/ContextWindowIndicator";
import { ModelPicker } from "@/components/ModelPicker";
import { SelectControl } from "@/components/SelectControl";
import { formatBytes } from "@/lib/chatAttachments";
import type { PendingAttachment } from "@/lib/chatTypes";
import type { AgentCard, AgentsPayload, ModelCatalog } from "@/types";

export interface ChatComposerProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onResume: () => void;
  onAddFiles: (files: FileList | null) => Promise<void>;
  onRemoveAttachment: (id: number) => void;
  streaming: boolean;
  composerDisabled: boolean;
  inputDisabled: boolean;
  workspaceMissing: boolean;
  backgroundTaskActive: boolean;
  currentThreadId: string;
  attachments: PendingAttachment[];
  attachmentAccept: string;
  agentName: string;
  agentOptions: { value: string; label: string }[];
  onAgentChange: (name: string) => void;
  selectedCard: AgentCard | undefined;
  provider: string;
  model: string;
  onProviderModelChange: (provider: string, model: string) => void;
  payload: AgentsPayload;
  recursionLimitReached: boolean;
  currentTodos: Array<{ content: string; status: "pending" | "in_progress" | "completed" }> | null;
  todoProgress: TodoProgress;
  todosExpanded: boolean;
  onTodosToggle: () => void;
  contextWindowData: ContextWindowData;
}

export function ChatComposer(props: ChatComposerProps) {
  let chatPromptRef: HTMLTextAreaElement | undefined;
  let fileInputRef: HTMLInputElement | undefined;
  const [composerOptionsOpen, setComposerOptionsOpen] = createSignal(false);

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

  createEffect(() => {
    props.prompt;
    resizeChatPromptInput();
  });

  return (
    <div class="composer chat-composer">
      <input
        ref={fileInputRef}
        class="is-hidden"
        type="file"
        multiple
        accept={props.attachmentAccept}
        onChange={async (event) => {
          await props.onAddFiles(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
      />
      <ChatTodos
        todos={props.currentTodos}
        progress={props.todoProgress}
        expanded={props.todosExpanded}
        onToggle={props.onTodosToggle}
      />
      <Show when={props.recursionLimitReached}>
        <div class="chat-recursion-warning">
          <div class="chat-recursion-warning-left">
            <span style="font-size: 16px;">⚠️</span>
            <span>Agent đã đạt giới hạn bước xử lý mà chưa hoàn thành nhiệm vụ. Bạn có muốn tiếp tục chạy không?</span>
          </div>
          <button
            class="chat-recursion-warning-btn"
            type="button"
            onClick={props.onResume}
          >
            Tiếp tục chạy
          </button>
        </div>
      </Show>
      <Show when={props.attachments.length > 0}>
        <div class="chat-attachment-list">
          <For each={props.attachments}>
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
                  onClick={() => props.onRemoveAttachment(attachment.id)}
                  disabled={props.composerDisabled}
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
      <textarea
        ref={chatPromptRef}
        class="chat-prompt-input"
        rows={1}
        value={props.prompt}
        disabled={props.inputDisabled}
        placeholder={
          props.backgroundTaskActive
            ? "Background task is running..."
            : props.workspaceMissing
              ? "Select a workspace before sending..."
              : props.currentThreadId
                ? "Continue this thread..."
                : "Ask Kaka to build features, fix bugs, or work on your code"
        }
        onInput={(event) => {
          props.onPromptChange(event.currentTarget.value);
          resizeChatPromptInput();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.ctrlKey && !event.shiftKey) {
            event.preventDefault();
            if (!props.composerDisabled) {
              props.onSend();
            }
          }
        }}
      />
      <div class="chat-composer-toolbar">
        <div class="chat-composer-actions">
          <SelectControl
            class="chat-agent-picker"
            value={props.agentName}
            options={props.agentOptions}
            disabled={props.composerDisabled}
            onChange={props.onAgentChange}
            ariaLabel="Agent"
            title={props.selectedCard?.description || "Select agent"}
            icon={<Bot size={14} />}
          />
          <button
            class="chat-composer-icon"
            type="button"
            onClick={() => fileInputRef?.click()}
            disabled={props.composerDisabled}
            title="Attach files"
            aria-label="Attach files"
          >
            <Plus size={18} />
          </button>
          <button
            class={`chat-composer-icon ${composerOptionsOpen() ? "active" : ""}`}
            type="button"
            onClick={() => setComposerOptionsOpen((current) => !current)}
            disabled={props.workspaceMissing}
            title="Run settings"
            aria-label="Run settings"
            aria-expanded={composerOptionsOpen()}
          >
            <MoreHorizontal size={18} />
          </button>
          <Show when={props.currentThreadId}>
            <ContextWindowIndicator data={props.contextWindowData} />
          </Show>
        </div>
        <Show
          when={props.streaming}
          fallback={
            <button
              class="chat-composer-icon"
              type="button"
              onClick={() => props.onSend()}
              disabled={props.composerDisabled || (!props.prompt.trim() && !props.attachments.length)}
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
            onClick={props.onStop}
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
              catalogs={props.payload.model_catalogs}
              providerNames={props.payload.provider_names}
              defaultProvider={props.payload.default_provider}
              defaultModel={props.payload.default_model}
              provider={props.provider}
              model={props.model}
              disabled={props.composerDisabled}
              dropdownPlacement="top"
              resolveDefault={true}
              onChange={(nextProvider, nextModel) => {
                props.onProviderModelChange(nextProvider, nextModel);
              }}
            />
          </div>
          <div class="chat-agent-summary">
            <div class="row">
              <Bot size={14} />
              <strong>{props.selectedCard?.display_name || props.selectedCard?.name || "No agent"}</strong>
            </div>
            <p class="hint">{props.selectedCard?.description || "No description."}</p>
            <div class="chips">
              <span class="chip">{props.selectedCard?.graph_type || "default"}</span>
              <span class="chip">
                {(() => {
                  const activeProvider = props.provider || props.selectedCard?.provider || "default";
                  const activeModel = props.model || props.selectedCard?.model || "";
                  const resolvedProv = activeProvider === "default" ? props.payload.default_provider : activeProvider;
                  const catalog = props.payload.model_catalogs.find((c) => c.provider === resolvedProv);
                  const resolvedMod = (activeModel === "" || activeModel === "provider default")
                    ? (activeProvider === "default" ? props.payload.default_model : (catalog?.default_model || "default"))
                    : activeModel;
                  return `${resolvedProv}/${resolvedMod}`;
                })()}
              </span>
            </div>
          </div>
        </div>
      </Show>
    </div>
  );
}
