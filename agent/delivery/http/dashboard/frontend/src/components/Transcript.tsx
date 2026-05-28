import { FileText, Image as ImageIcon } from "lucide-solid";
import { For, Show } from "solid-js";

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

export type TranscriptMessage = {
  type: "message";
  role: TranscriptRole;
  text: string;
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
  deferMermaid?: boolean;
  itemId?: number;
}) {
  return (
    <div class={`message ${props.role}`} data-transcript-item-id={props.itemId}>
      <div class="message-bubble">
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
}) {
  return props.item.type === "message" ? (
    <TranscriptMessageView
      role={props.item.role}
      text={props.item.text}
      attachments={props.item.attachments}
      deferMermaid={props.deferMermaid}
      itemId={props.itemId}
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
