import { formatValue } from "@/lib/utils";

export type TranscriptRole = "user" | "assistant" | "error" | "system";

export type TranscriptMessage = {
  type: "message";
  role: TranscriptRole;
  text: string;
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

export function TranscriptMessageView(props: { role: TranscriptRole; text: string }) {
  return (
    <div class={`message ${props.role}`}>
      <div class="message-bubble">
        {props.text}
      </div>
    </div>
  );
}

export function ToolCallDetail(props: {
  name?: string | null;
  args: unknown;
  result: unknown;
  defaultOpen?: boolean;
}) {
  return (
    <details class="tool-call" open={props.defaultOpen ?? false}>
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

export function TranscriptItemView(props: { item: TranscriptItem }) {
  return props.item.type === "message" ? (
    <TranscriptMessageView role={props.item.role} text={props.item.text} />
  ) : (
    <ToolCallDetail
      name={props.item.name}
      args={props.item.args}
      result={props.item.result}
    />
  );
}
