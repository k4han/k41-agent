import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileText,
  Image as ImageIcon,
  Pencil,
  X,
} from "lucide-solid";
import { createEffect, createSignal, For, Show } from "solid-js";

import { Markdown } from "@/components/Markdown";
import { formatValue } from "@/lib/utils";

export type TranscriptRole = "user" | "assistant" | "error" | "system";

export type TranscriptAttachment = {
  name: string;
  mime_type: string;
  size: number;
  kind: "text" | "image";
  preview_url?: string;
};

export type TranscriptBranchOption = {
  checkpoint_id: string;
  message: string;
};

export type TranscriptBranch = {
  current: number;
  total: number;
  options: TranscriptBranchOption[];
};

export type TranscriptMessage = {
  type: "message";
  role: TranscriptRole;
  text: string;
  messageIndex?: number;
  sourceCheckpointId?: string;
  parentCheckpointId?: string;
  branch?: TranscriptBranch;
  attachments?: TranscriptAttachment[];
};

export type TranscriptTool = {
  type: "tool";
  tool_call_id?: string | null;
  name?: string | null;
  args: unknown;
  result: unknown;
};

export type TranscriptItem = TranscriptMessage | TranscriptTool;

type TranscriptToolTarget<T extends TranscriptItem> = Extract<T, { type: "tool" }>;

export function createTranscriptTool(options: {
  toolCallId?: string | null;
  name?: string | null;
  args?: unknown;
  result?: unknown;
}): TranscriptTool {
  return {
    type: "tool",
    tool_call_id: options.toolCallId || null,
    name: options.name || "unknown",
    args: options.args ?? null,
    result: options.result ?? null,
  };
}

export function findTranscriptToolTarget<T extends TranscriptItem>(
  items: T[],
  toolCallId?: string | null,
  name?: string | null,
): TranscriptToolTarget<T> | undefined {
  const targetById = toolCallId
    ? items.find(
        (item): item is TranscriptToolTarget<T> =>
          item.type === "tool" && item.tool_call_id === toolCallId,
      )
    : undefined;
  if (targetById) {
    return targetById;
  }
  if (!name) {
    return undefined;
  }

  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type === "tool" && item.name === name && item.result === null) {
      return item as TranscriptToolTarget<T>;
    }
  }
  return undefined;
}

function formatAttachmentSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) {
    return "0 B";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function TranscriptMessageView(props: {
  role: TranscriptRole;
  text: string;
  attachments?: TranscriptAttachment[];
  messageIndex?: number;
  sourceCheckpointId?: string;
  parentCheckpointId?: string;
  branch?: TranscriptBranch;
  deferMermaid?: boolean;
  itemId?: number;
  actionsDisabled?: boolean;
  onCopy?: (text: string) => void;
  onEdit?: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
  onBranchSelect?: (checkpointId: string) => void;
}) {
  const [editing, setEditing] = createSignal(false);
  const [draft, setDraft] = createSignal(props.text);

  createEffect(() => {
    if (!editing()) {
      setDraft(props.text);
    }
  });

  const canEdit = () =>
    props.role === "user" &&
    props.messageIndex !== undefined &&
    !!props.sourceCheckpointId &&
    !props.actionsDisabled;
  const branch = () => props.branch;
  const branchCurrentIndex = () => Math.max(0, (branch()?.current || 1) - 1);
  const branchOptions = () => branch()?.options || [];
  const canShowBranchSwitcher = () =>
    props.role === "user" && !!branch() && branchOptions().length > 1;
  const selectBranch = (delta: number) => {
    if (props.actionsDisabled) {
      return;
    }
    const nextIndex = branchCurrentIndex() + delta;
    const option = branchOptions()[nextIndex];
    if (!option) {
      return;
    }
    props.onBranchSelect?.(option.checkpoint_id);
  };
  const submitEdit = () => {
    const text = draft().trim();
    if (!text || !canEdit()) {
      return;
    }
    props.onEdit?.({
      itemId: props.itemId,
      messageIndex: props.messageIndex!,
      sourceCheckpointId: props.sourceCheckpointId!,
      text,
    });
    setEditing(false);
  };

  return (
    <div
      class={`message ${props.role}`}
      data-transcript-item-id={props.itemId}
      role={props.role === "error" ? "alert" : undefined}
    >
      <div class="message-bubble">
        <Show
          when={!editing()}
          fallback={
            <div class="message-edit">
              <textarea
                class="message-edit-input"
                value={draft()}
                rows={Math.min(8, Math.max(2, draft().split("\n").length))}
                onInput={(event) => setDraft(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                    event.preventDefault();
                    submitEdit();
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setEditing(false);
                    setDraft(props.text);
                  }
                }}
              />
              <div class="message-edit-actions">
                <button
                  class="message-action-btn"
                  type="button"
                  onClick={submitEdit}
                  disabled={!draft().trim()}
                  title="Save edit"
                  aria-label="Save edit"
                >
                  <Check size={15} />
                </button>
                <button
                  class="message-action-btn"
                  type="button"
                  onClick={() => {
                    setEditing(false);
                    setDraft(props.text);
                  }}
                  title="Cancel edit"
                  aria-label="Cancel edit"
                >
                  <X size={15} />
                </button>
              </div>
            </div>
          }
        >
          <Show when={props.text}>
            <Show
              when={props.role === "assistant"}
              fallback={<div class="message-text">{props.text}</div>}
            >
              <Markdown
                text={props.text}
                class="message-markdown"
                deferMermaid={props.deferMermaid}
              />
            </Show>
          </Show>
        </Show>
        <Show when={props.attachments?.length}>
          <div class="message-attachments">
            <For each={props.attachments || []}>
              {(attachment) => (
                <div class="message-attachment">
                  <Show
                    when={attachment.kind === "image" && attachment.preview_url}
                    fallback={
                      <span class="message-attachment-icon">
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
                      class="message-attachment-thumb"
                      src={attachment.preview_url}
                      alt=""
                    />
                  </Show>
                  <span class="message-attachment-name">{attachment.name}</span>
                  <span class="message-attachment-meta">
                    {formatAttachmentSize(attachment.size)}
                  </span>
                </div>
              )}
            </For>
          </div>
        </Show>
        <Show when={props.role === "user"}>
          <div class="message-actions" aria-label="Message actions">
            <button
              class="message-action-btn"
              type="button"
              onClick={() => props.onCopy?.(props.text)}
              title="Copy message"
              aria-label="Copy message"
            >
              <Copy size={15} />
            </button>
            <button
              class="message-action-btn"
              type="button"
              onClick={() => {
                setDraft(props.text);
                setEditing(true);
              }}
              disabled={!canEdit()}
              title="Edit and regenerate"
              aria-label="Edit and regenerate"
            >
              <Pencil size={15} />
            </button>
            <Show when={canShowBranchSwitcher()}>
              <div class="message-branch-switcher" aria-label="Message branches">
                <button
                  class="message-action-btn"
                  type="button"
                  onClick={() => selectBranch(-1)}
                  disabled={props.actionsDisabled || branchCurrentIndex() <= 0}
                  title="Previous branch"
                  aria-label="Previous branch"
                >
                  <ChevronLeft size={15} />
                </button>
                <span class="message-branch-count">
                  {branch()?.current || 1}/{branch()?.total || 1}
                </span>
                <button
                  class="message-action-btn"
                  type="button"
                  onClick={() => selectBranch(1)}
                  disabled={
                    props.actionsDisabled ||
                    branchCurrentIndex() >= branchOptions().length - 1
                  }
                  title="Next branch"
                  aria-label="Next branch"
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            </Show>
          </div>
        </Show>
      </div>
    </div>
  );
}

export function ToolCallDetail(props: {
  name?: string | null;
  args: unknown;
  result: unknown;
  defaultOpen?: boolean;
  itemId?: number;
}) {
  return (
    <details
      class="tool-call"
      open={props.defaultOpen ?? false}
      data-transcript-item-id={props.itemId}
    >
      <summary>
        <span class="mono">{props.name || "unknown"}</span>
      </summary>
      <div class="tool-call-body">
        <pre>{formatValue(props.args)}</pre>
        <pre>{props.result === null ? "Waiting for tool result..." : formatValue(props.result)}</pre>
      </div>
    </details>
  );
}

export function TranscriptItemView(props: {
  item: TranscriptItem;
  deferMermaid?: boolean;
  itemId?: number;
  actionsDisabled?: boolean;
  onCopyMessage?: (text: string) => void;
  onEditMessage?: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
  onBranchSelect?: (checkpointId: string) => void;
}) {
  return props.item.type === "message" ? (
    <TranscriptMessageView
      role={props.item.role}
      text={props.item.text}
      attachments={props.item.attachments}
      messageIndex={props.item.messageIndex}
      sourceCheckpointId={props.item.sourceCheckpointId}
      parentCheckpointId={props.item.parentCheckpointId}
      branch={props.item.branch}
      deferMermaid={props.deferMermaid}
      itemId={props.itemId}
      actionsDisabled={props.actionsDisabled}
      onCopy={props.onCopyMessage}
      onEdit={props.onEditMessage}
      onBranchSelect={props.onBranchSelect}
    />
  ) : (
    <ToolCallDetail
      name={props.item.name}
      args={props.item.args}
      result={props.item.result}
      itemId={props.itemId}
    />
  );
}
