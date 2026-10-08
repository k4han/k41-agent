import {
  FileText,
  Image as ImageIcon,
  Plus,
  Paperclip,
  Send,
  Square,
  Trash2,
  Upload,
  X,
} from "lucide-solid";
import { createEffect, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { AgentModelPicker } from "@/components/AgentModelPicker";
import { ChatTodos, type TodoProgress } from "@/components/ChatTodos";
import { ContextWindowIndicator, type ContextWindowData } from "@/components/ContextWindowIndicator";
import { Dialog } from "@/components/Dialog";
import {
  UserInputRequestCard,
  type UserInputRequestSubmitPayload,
} from "@/components/UserInputRequestCard";
import { formatBytes } from "@/lib/chatAttachments";
import { useSkillSuggestions } from "@/lib/useSkillSuggestions";
import { PASTE_AS_ATTACHMENT_THRESHOLD, type PendingAttachment, type ReasoningEffort } from "@/lib/chatTypes";
import type { TranscriptUserInputRequest } from "@/components/Transcript";
import type { AgentCard, AgentChatPayload, WorkspaceRef } from "@/types";

export interface ChatComposerProps {
  workspace?: WorkspaceRef | null;
  prompt: string;
  onPromptChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onResume: () => void;
  onAddFiles: (files: FileList | File[] | null) => Promise<void>;
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
  reasoningEffort: ReasoningEffort;
  reasoningEffortLevels: ReasoningEffort[];
  reasoningEffortDefault?: string | null;
  onReasoningEffortChange: (effort: ReasoningEffort) => void;
  payload: AgentChatPayload;
  recursionLimitReached: boolean;
  currentTodos: Array<{ content: string; status: "pending" | "in_progress" | "completed" }> | null;
  todoProgress: TodoProgress;
  todosExpanded: boolean;
  onTodosToggle: () => void;
  contextWindowData: ContextWindowData;
  onCompactClick?: () => void;
  compacting?: boolean;
  userInputRequest: TranscriptUserInputRequest | null;
  userInputRequestDisabled: boolean;
  onSubmitUserInputRequest: (payload: UserInputRequestSubmitPayload) => void;
  setChatPromptRef?: (el: HTMLTextAreaElement) => void;
  chatPromptRef?: (el: HTMLTextAreaElement) => void;
}

export function ChatComposer(props: ChatComposerProps) {
  const effortLabel = (effort: string) => effort.charAt(0).toUpperCase() + effort.slice(1);

  let chatPromptRef: HTMLTextAreaElement | undefined;
  let fileInputRef: HTMLInputElement | undefined;
  const skillSuggestions = useSkillSuggestions(props, () => chatPromptRef);

  const [previewAttachment, setPreviewAttachment] = createSignal<PendingAttachment | null>(null);
  const [isDragging, setIsDragging] = createSignal(false);
  const [showMoreMenu, setShowMoreMenu] = createSignal(false);
  const [showPasteDialog, setShowPasteDialog] = createSignal(false);
  const [customPasteText, setCustomPasteText] = createSignal("");
  const [moreMenuPos, setMoreMenuPos] = createSignal({ top: 0, left: 0 });
  let moreTriggerRef: HTMLButtonElement | undefined;
  let moreMenuRef: HTMLDivElement | undefined;

  const updateMoreMenuPosition = () => {
    if (!moreTriggerRef || !showMoreMenu()) return;
    const rect = moreTriggerRef.getBoundingClientRect();
    const gap = 8;
    const width = 230;
    let left = rect.left;
    if (left + width > window.innerWidth - 8) {
      left = Math.max(8, rect.right - width);
    }
    const estimatedHeight = 260;
    let top = rect.top - estimatedHeight - gap;
    if (top < 8) {
      top = rect.bottom + gap;
    }
    setMoreMenuPos({ top, left });
  };

  createEffect(() => {
    if (!showMoreMenu()) return;
    updateMoreMenuPosition();
    const handler = () => updateMoreMenuPosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    onCleanup(() => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    });
  });

  let dragCounter = 0;

  const resizeChatPromptInput = () => {
    if (!chatPromptRef) {
      return;
    }
    chatPromptRef.style.height = "auto";
    const computed = window.getComputedStyle(chatPromptRef);
    const maxHeight = Number.parseFloat(computed.maxHeight) || 220;
    const nextHeight = Math.min(chatPromptRef.scrollHeight, maxHeight);
    const minHeight = Number.parseFloat(computed.minHeight) || 48;
    chatPromptRef.style.height = `${Math.max(nextHeight, minHeight)}px`;
    chatPromptRef.style.overflowY = chatPromptRef.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  createEffect(() => {
    props.prompt;
    resizeChatPromptInput();
  });

  const handleGlobalClick = (e: MouseEvent) => {
    if (!showMoreMenu()) return;
    const target = e.target as Node | null;
    if (target && moreMenuRef?.contains(target)) return;
    if (target && moreTriggerRef?.contains(target)) return;
    const wrapper = moreTriggerRef?.closest(".chat-composer-more-wrapper");
    if (target && wrapper?.contains(target)) return;
    setShowMoreMenu(false);
  };

  const handleGlobalKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape" && showMoreMenu()) {
      setShowMoreMenu(false);
    }
  };

  onMount(() => {
    document.addEventListener("click", handleGlobalClick);
    document.addEventListener("keydown", handleGlobalKeyDown);
  });

  onCleanup(() => {
    document.removeEventListener("click", handleGlobalClick);
    document.removeEventListener("keydown", handleGlobalKeyDown);
  });

  const handleDragEnter = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (props.composerDisabled) return;
    dragCounter += 1;
    if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) {
      setIsDragging(true);
    }
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!props.composerDisabled && e.dataTransfer) {
      e.dataTransfer.dropEffect = "copy";
    }
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter -= 1;
    if (dragCounter <= 0) {
      dragCounter = 0;
      setIsDragging(false);
    }
  };

  const handleDrop = async (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter = 0;
    setIsDragging(false);
    if (props.composerDisabled) return;
    if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
      await props.onAddFiles(e.dataTransfer.files);
    }
  };

  return (
    <div
      class={`composer chat-composer ${isDragging() ? "is-drag-over" : ""}`}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
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
      <Show when={isDragging()}>
        <div class="chat-composer-dropzone" aria-hidden="true">
          <div class="chat-composer-dropzone-badge">
            <Upload size={18} class="chat-composer-dropzone-icon" />
            <span>Drop images or files here to attach</span>
          </div>
        </div>
      </Show>
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
                title={`Click to preview ${attachment.name}`}
              >
                <Show
                  when={attachment.kind === "image" && attachment.preview_url}
                  fallback={
                    <span class="chat-attachment-icon">
                      <FileText size={15} />
                    </span>
                  }
                >
                  <img
                    class="chat-attachment-thumb"
                    src={attachment.preview_url}
                    alt=""
                  />
                </Show>
                <div class="chat-attachment-info">
                  <span class="chat-attachment-name">{attachment.name}</span>
                  <span class="chat-attachment-size">{formatBytes(attachment.size)}</span>
                </div>
                <button
                  class="chat-attachment-remove"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    props.onRemoveAttachment(attachment.id);
                  }}
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
      <div class="chat-prompt-wrapper">
        <Show when={skillSuggestions.open()}>
          <div class="chat-skill-suggestions">
            <div class="chat-skill-suggestions-header">
              <span>Skills</span>
              <span>↑ ↓ to navigate · Enter or Tab to select · Esc to dismiss</span>
            </div>
            <Show when={skillSuggestions.loading()}>
              <div class="chat-skill-suggestions-status" role="status">Loading available skills...</div>
            </Show>
            <Show when={skillSuggestions.error()}>
              <div class="chat-skill-suggestions-status" role="alert">{skillSuggestions.error()}</div>
            </Show>
            <Show when={!skillSuggestions.loading() && !skillSuggestions.error() && !skillSuggestions.matches().length}>
              <div class="chat-skill-suggestions-status" role="status">No matching skills available for this workspace.</div>
            </Show>
            <div id="chat-skill-suggestions" class="chat-skill-suggestions-list" role="listbox" aria-label="Skill suggestions" aria-busy={skillSuggestions.loading()}>
              <For each={skillSuggestions.matches()}>
                {(item, index) => (
                  <button
                    id={`chat-skill-option-${index()}`}
                    class="chat-skill-suggestion"
                    classList={{ active: skillSuggestions.activeIndex() === index() }}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={skillSuggestions.activeIndex() === index()}
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => skillSuggestions.setActiveIndex(index())}
                    onClick={() => skillSuggestions.select(item)}
                  >
                    <strong>{item.name}</strong>
                    <span>{item.description}</span>
                  </button>
                )}
              </For>
            </div>
          </div>
        </Show>
        <textarea
          ref={(el) => {
            chatPromptRef = el;
            props.setChatPromptRef?.(el);
            props.chatPromptRef?.(el);
          }}
          class="chat-prompt-input"
          rows={1}
          value={props.prompt}
          disabled={props.inputDisabled}
          placeholder={
            props.backgroundTaskActive
              ? "Background task is running..."
              : props.currentThreadId
                ? "Reply or attach files..."
                : "What would you like to work on?"
          }
          inputMode="text"
          enterkeyhint="send"
          aria-label="Chat message"
          aria-autocomplete="list"
          aria-controls={skillSuggestions.open() ? "chat-skill-suggestions" : undefined}
          aria-activedescendant={skillSuggestions.open() && skillSuggestions.matches().length ? `chat-skill-option-${skillSuggestions.activeIndex()}` : undefined}
          onFocus={() => {
            skillSuggestions.setFocused(true);
            skillSuggestions.syncSelection(true);
          }}
          onBlur={() => skillSuggestions.setFocused(false)}
          onClick={() => skillSuggestions.syncSelection(true)}
          onSelect={() => skillSuggestions.syncSelection()}
          onKeyUp={() => skillSuggestions.syncSelection()}
          onInput={(event) => {
            props.onPromptChange(event.currentTarget.value);
            skillSuggestions.syncSelection(true);
            resizeChatPromptInput();
          }}
          onPaste={(event) => {
            const clipboardFiles = event.clipboardData?.files;
            if (clipboardFiles && clipboardFiles.length > 0) {
              event.preventDefault();
              void props.onAddFiles(clipboardFiles);
              return;
            }

            const pastedText = event.clipboardData?.getData("text/plain") || "";
            if (pastedText.length > PASTE_AS_ATTACHMENT_THRESHOLD) {
              event.preventDefault();
              props.onPasteAsAttachment(pastedText);
            }
          }}
          onKeyDown={(event) => {
            if (event.isComposing || event.keyCode === 229) {
              return;
            }
            if (skillSuggestions.handleKeyDown(event)) return;
            if ((event.key === "Enter" && !event.shiftKey) || (event.key === "Enter" && (event.ctrlKey || event.metaKey))) {
              event.preventDefault();
              if (!props.composerDisabled) {
                props.onSend();
              }
            }
          }}
        />
      </div>
      <div class="chat-composer-toolbar">
        <div class="chat-composer-tier chat-composer-tier-selectors">
          <div class="chat-composer-more-wrapper">
            <button
              ref={moreTriggerRef}
              class={`chat-composer-icon ${showMoreMenu() ? "active" : ""}`}
              type="button"
              onClick={() => setShowMoreMenu(!showMoreMenu())}
              title="Add files and more"
              aria-label="Add files and more"
              aria-haspopup="menu"
              aria-expanded={showMoreMenu()}
            >
              <Plus size={18} />
            </button>
            <Show when={showMoreMenu()}>
              <Portal>
                <div
                  ref={moreMenuRef}
                  class="chat-composer-more-menu chat-composer-more-menu--portal"
                  role="menu"
                  style={{
                    top: `${moreMenuPos().top}px`,
                    left: `${moreMenuPos().left}px`,
                  }}
                >
                  <button
                    class="chat-composer-menu-item"
                    type="button"
                    onClick={() => {
                      fileInputRef?.click();
                      setShowMoreMenu(false);
                    }}
                    disabled={props.composerDisabled}
                  >
                    <Paperclip size={15} />
                    <span>Attach files</span>
                  </button>
                  <button
                    class="chat-composer-menu-item"
                    type="button"
                    onClick={() => {
                      setShowPasteDialog(true);
                      setShowMoreMenu(false);
                    }}
                    disabled={props.composerDisabled}
                  >
                    <FileText size={15} />
                    <span>Paste as attachment</span>
                  </button>
                  <button
                    class="chat-composer-menu-item"
                    type="button"
                    onClick={() => {
                      props.onPromptChange("");
                      setShowMoreMenu(false);
                    }}
                    disabled={props.composerDisabled || !props.prompt.trim()}
                  >
                    <Trash2 size={15} />
                    <span>Clear prompt</span>
                  </button>
                  <div class="chat-composer-menu-divider" />
                  <div class="chat-composer-menu-shortcuts">
                    <div class="chat-composer-shortcut-row">
                      <span>Send message</span>
                      <span class="chat-composer-kbd"><kbd>Enter</kbd></span>
                    </div>
                    <div class="chat-composer-shortcut-row">
                      <span>New line</span>
                      <span class="chat-composer-kbd"><kbd>Shift</kbd> + <kbd>Enter</kbd></span>
                    </div>
                    <div class="chat-composer-shortcut-row">
                      <span>Attach files</span>
                      <span class="chat-composer-kbd">Drag / Paste</span>
                    </div>
                  </div>
                </div>
              </Portal>
            </Show>
          </div>
          <AgentModelPicker
            agentName={props.agentName}
            agents={props.agents}
            onAgentChange={props.onAgentChange}
            provider={props.provider}
            model={props.model}
            onProviderModelChange={props.onProviderModelChange}
            catalogs={props.payload.model_catalogs}
            providerNames={props.payload.provider_names}
            defaultProvider={props.payload.default_provider}
            defaultModel={props.payload.default_model}
            disabled={props.composerDisabled}
          />
          <select
            class="chat-effort-button"
            value={props.reasoningEffort}
            onChange={(event) => props.onReasoningEffortChange(event.currentTarget.value)}
            disabled={props.composerDisabled || props.reasoningEffortLevels.length === 0}
            title={props.reasoningEffortLevels.length
              ? "Reasoning effort for the selected model"
              : "No configurable effort levels available. Missing metadata can be supplied in provider settings."}
            aria-label="Reasoning effort"
          >
            <option value="" selected={props.reasoningEffort === ""}>Auto{props.reasoningEffortDefault ? ` (${effortLabel(props.reasoningEffortDefault)})` : ""}</option>
            <For each={props.reasoningEffortLevels}>
              {(level) => <option value={level} selected={props.reasoningEffort === level}>{effortLabel(level)}</option>}
            </For>
          </select>
        </div>
        <div class="chat-composer-tier chat-composer-tier-actions">
          <div class="chat-composer-actions">
            <Show when={props.currentThreadId}>
              <ContextWindowIndicator
                data={props.contextWindowData}
                onCompactClick={props.onCompactClick}
                compacting={props.compacting}
              />
            </Show>
          </div>
          <div class="chat-composer-right-group">
            <div class="chat-composer-send-wrapper">
              <Show
                when={props.stopActive}
                fallback={
                  <button
                    class="chat-composer-icon chat-composer-send"
                    type="button"
                    onClick={() => props.onSend()}
                    disabled={props.composerDisabled || (!props.prompt.trim() && !props.attachments.length)}
                    title="Send message (Enter)"
                    aria-label="Send message"
                  >
                    <Send size={15} />
                  </button>
                }
              >
                <button
                  class="chat-composer-icon chat-composer-stop"
                  type="button"
                  onClick={props.onStop}
                  title="Stop generating"
                  aria-label="Stop generating"
                >
                  <Square size={14} />
                </button>
              </Show>
            </div>
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
      <Dialog
        open={showPasteDialog()}
        title="Paste Text Attachment"
        subtitle="Paste code, logs, or large text to attach as a file"
        size="lg"
        onClose={() => {
          setShowPasteDialog(false);
          setCustomPasteText("");
        }}
      >
        <div class="chat-paste-dialog-content">
          <textarea
            class="chat-paste-dialog-textarea"
            rows={10}
            placeholder="Paste your code, log output, or text content here..."
            value={customPasteText()}
            onInput={(e) => setCustomPasteText(e.currentTarget.value)}
          />
          <div class="chat-paste-dialog-actions">
            <button
              class="btn btn-secondary"
              type="button"
              onClick={() => {
                setShowPasteDialog(false);
                setCustomPasteText("");
              }}
            >
              Cancel
            </button>
            <button
              class="btn btn-primary"
              type="button"
              disabled={!customPasteText().trim()}
              onClick={() => {
                props.onPasteAsAttachment(customPasteText());
                setShowPasteDialog(false);
                setCustomPasteText("");
              }}
            >
              Add as Attachment
            </button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
