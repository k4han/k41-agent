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

export function TranscriptMessageView(props: { role: TranscriptRole; text: string }) {
  return (
    <div class={`message ${props.role}`}>
      <div class="message-bubble">
        <div class="hint">{props.role}</div>
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
        <span class="hint">Tool call</span>{" "}
        <span class="mono">{props.name || "unknown"}</span>
      </summary>
      <pre>{formatValue(props.args)}</pre>
      <pre>{props.result === null ? "Waiting for tool result..." : formatValue(props.result)}</pre>
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
