import {
  FileText,
  Image as ImageIcon,
  MoreHorizontal,
  Plus,
  Send,
  Square,
  X,
} from "lucide-solid";
import { createEffect, createSignal, For, Show, type JSX } from "solid-js";

import { AgentPicker } from "@/components/AgentPicker";
import { ChatTodos, type TodoProgress } from "@/components/ChatTodos";
import { ContextWindowIndicator, type ContextWindowData } from "@/components/ContextWindowIndicator";
import { Dialog } from "@/components/Dialog";
import { ModelPicker } from "@/components/ModelPicker";
import {
  UserInputRequestCard,
  type UserInputRequestSubmitPayload,
} from "@/components/UserInputRequestCard";
import { formatBytes } from "@/lib/chatAttachments";
import { PASTE_AS_ATTACHMENT_THRESHOLD, type PendingAttachment } from "@/lib/chatTypes";
import type { TranscriptUserInputRequest } from "@/components/Transcript";
import type { AgentCard, AgentChatPayload, ModelCatalog } from "@/types";

export interface ChatComposerProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onResume: () => void;
  onAddFiles: (files: FileList | null) => Promise<void>;
  onPasteAsAttachment: (text: string) => void;
  onRemoveAttachment: (id: number) => void;
  stopActive: boolean;
  composerDisabled: boolean;
  inputDisabled: boolean;
  backgroundTaskActive: boolean;
  currentThreadId: string;
  attachments: PendingAttachment[];
  attachmentAccept: string;
  agentName: string;
  agents: AgentCard[];
  onAgentChange: (name: string) => void;
  provider: string;
  model: string;
  onProviderModelChange: (provider: string, model: string) => void;
  payload: AgentChatPayload;
  recursionLimitReached: boolean;
  currentTodos: Array<{ content: string; status: "pending" | "in_progress" | "completed" }> | null;
  todoProgress: TodoProgress;
  todosExpanded: boolean;
  onTodosToggle: () => void;
  contextWindowData: ContextWindowData;
  userInputRequest: TranscriptUserInputRequest | null;
  userInputRequestDisabled: boolean;
  onSubmitUserInputRequest: (payload: UserInputRequestSubmitPayload) => void;
}

export function ChatComposer(props: ChatComposerProps) {
  let chatPromptRef: HTMLTextAreaElement | undefined;
  let fileInputRef: HTMLInputElement | undefined;
  const [previewAttachment, setPreviewAttachment] = createSignal<PendingAttachment | null>(null);

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
      <Show when={props.userInputRequest}>
        {(request) => (
          <UserInputRequestCard
            request={request()}
            disabled={props.userInputRequestDisabled}
            onSubmit={props.onSubmitUserInputRequest}
          />
        )}
      </Show>
      <Show when={props.recursionLimitReached}>
        <div class="chat-recursion-warning">
          <div class="chat-recursion-warning-left">
            <span style="font-size: 16px;">⚠️</span>
            <span>Agent reached the step limit without completing the task. Do you want to continue running?</span>
          </div>
          <button
            class="chat-recursion-warning-btn"
            type="button"
            onClick={props.onResume}
          >
            Continue running
          </button>
        </div>
      </Show>
      <Show when={props.attachments.length > 0}>
        <div class="chat-attachment-list">
          <For each={props.attachments}>
            {(attachment) => (
              <div
                class="chat-attachment-chip"
                role="button"
                tabIndex={0}
                onClick={() => setPreviewAttachment(attachment)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setPreviewAttachment(attachment);
                  }
                }}
              >
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
            : props.currentThreadId
              ? "Continue this thread..."
              : "Ask Kai to build features, fix bugs, or work on your code"
        }
        inputMode="text"
        enterkeyhint="send"
        onInput={(event) => {
          props.onPromptChange(event.currentTarget.value);
          resizeChatPromptInput();
        }}
        onPaste={(event) => {
          const pastedText = event.clipboardData?.getData("text/plain") || "";
          if (pastedText.length > PASTE_AS_ATTACHMENT_THRESHOLD) {
            event.preventDefault();
            props.onPasteAsAttachment(pastedText);
          }
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
        <div class="chat-composer-tier chat-composer-tier-selectors">
          <AgentPicker
            class="chat-agent-picker"
            value={props.agentName}
            agents={props.agents}
            disabled={props.composerDisabled}
            onChange={props.onAgentChange}
          />
          <ModelPicker
            class="chat-model-picker"
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
        <div class="chat-composer-tier chat-composer-tier-actions">
          <div class="chat-composer-actions">
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
              class="chat-composer-icon"
              type="button"
              title="More options"
              aria-label="More options"
            >
              <MoreHorizontal size={18} />
            </button>
            <Show when={props.currentThreadId}>
              <ContextWindowIndicator data={props.contextWindowData} />
            </Show>
          </div>
          <div class="chat-composer-send-wrapper">
            <Show
              when={props.stopActive}
              fallback={
                <button
                  class="chat-composer-icon chat-composer-send"
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
        </div>
      </div>
      <Dialog
        open={previewAttachment() !== null}
        title={previewAttachment()?.name || "Preview"}
        subtitle={previewAttachment()?.kind === "image" ? "Image attachment preview" : "Text file preview"}
        size="lg"
        onClose={() => setPreviewAttachment(null)}
      >
        <Show
          when={previewAttachment()?.kind === "image"}
          fallback={
            <pre style={{
              "white-space": "pre-wrap",
              "word-break": "break-all",
              "font-size": "12px",
              "line-height": "1.5",
              "margin": 0,
              "max-height": "60vh",
              "overflow-y": "auto",
            }}>
              {previewAttachment()?.content || "(empty)"}
            </pre>
          }
        >
          <img
            src={previewAttachment()?.preview_url}
            alt={previewAttachment()?.name}
            style={{ "max-width": "100%", "max-height": "60vh", "border-radius": "6px", display: "block", margin: "0 auto" }}
          />
        </Show>
      </Dialog>
    </div>
  );
}
