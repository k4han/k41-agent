import type { ThreadMessage, ThreadMessagesPayload } from "@/lib/chatThreads";

export type ChatExportFormat = "markdown" | "text" | "json";

export type ChatExportTarget = {
  thread_id: string;
  title?: string;
  checkpoint_id?: string;
};

export type ChatExportFile = {
  filename: string;
  mimeType: string;
  content: string;
};

function inlineText(value: string): string {
  return value.replace(/[\r\n]+/g, " ");
}

function markdownLabel(value: string): string {
  return inlineText(value).replace(/[\\`*_{}\[\]()<>#+.!|~-]/g, "\\$&");
}

function codeBlock(value: string, language = ""): string {
  const longestRun = (value.match(/`+/g) || []).reduce((longest, run) => Math.max(longest, run.length), 0);
  const fence = "`".repeat(Math.max(3, longestRun + 1));
  return `${fence}${language}\n${value}\n${fence}`;
}

// Models often send null for every argument a tool call does not use; those say nothing.
function withoutNullArgs(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutNullArgs);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => item !== null && item !== undefined)
      .map(([key, item]) => [key, withoutNullArgs(item)]),
  );
}

function readableMessage(message: ThreadMessage, markdown: boolean, includeTools: boolean): string {
  if (message.role === "tool" && !includeTools) return "";
  const label = message.role.charAt(0).toUpperCase() + message.role.slice(1);
  const heading = `${label}${message.name ? `: ${inlineText(message.name)}` : ""}`;
  const parts = [markdown ? `## ${markdownLabel(heading)}` : heading];
  if (message.content) parts.push(message.content);

  for (const attachment of message.attachments || []) {
    const name = inlineText(attachment.name || "Unnamed attachment");
    const details = [attachment.mime_type, `${attachment.size} bytes`].filter(Boolean).join(", ");
    const description = `Attachment: ${name} (${details})`;
    parts.push(markdown ? `**${markdownLabel(description)}**` : description);
    if (attachment.content) {
      parts.push(markdown ? codeBlock(attachment.content) : attachment.content);
    }
  }

  if (includeTools) {
    for (const call of message.tool_calls || []) {
      const description = `Tool call: ${inlineText(call.name)}`;
      const args = JSON.stringify(withoutNullArgs(call.args ?? {}), null, 2);
      parts.push(markdown ? `### ${markdownLabel(description)}\n\n${codeBlock(args, "json")}` : `${description}\n${args}`);
    }
  }

  if (parts.length === 1) return "";
  return parts.join("\n\n");
}

export function createChatExport(
  thread: ThreadMessagesPayload,
  format: ChatExportFormat,
  includeTools = false,
  exportedAt = new Date(),
): ChatExportFile {
  if (!thread.messages.length) throw new Error("No saved messages are available to export.");
  const title = thread.title?.trim() || thread.thread_id;
  const stem = Array.from(title.normalize("NFC").replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, "-")).slice(0, 100).join("")
    .trim().replace(/[. ]+$/g, "") || "chat";
  const timestamp = exportedAt.toISOString();
  const filename = `conversation-${stem}-${timestamp.replace(/[:.]/g, "-")}`;

  if (format === "json") {
    const payload = {
      format_version: 1,
      exported_at: timestamp,
      thread_id: thread.thread_id,
      title,
      active_checkpoint_id: thread.active_checkpoint_id,
      platform: thread.platform,
      user_id: thread.user_id,
      channel_id: thread.channel_id,
      agent_name: thread.agent_name,
      provider: thread.provider,
      model: thread.model,
      kind: thread.kind,
      messages: thread.messages.map(({ branch, ...message }) => message.tool_calls
        ? { ...message, tool_calls: message.tool_calls.map((call) => ({ ...call, args: withoutNullArgs(call.args) })) }
        : message),
    };
    return { filename: `${filename}.json`, mimeType: "application/json;charset=utf-8", content: `${JSON.stringify(payload, null, 2)}\n` };
  }

  const markdown = format === "markdown";
  const metadata = [
    ["Thread", thread.thread_id],
    ["Exported", timestamp],
    ["Checkpoint", thread.active_checkpoint_id],
    ["Agent", thread.agent_name],
    ["Provider", thread.provider],
    ["Model", thread.model],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  const messages = thread.messages.map((message) => readableMessage(message, markdown, includeTools)).filter(Boolean);
  if (!messages.length) throw new Error("No messages match the export options. Try including tool activity.");
  const parts = [
    markdown ? `# ${markdownLabel(title)}` : inlineText(title),
    metadata.map(([label, value]) => `${markdown ? "- " : ""}${label}: ${markdown ? markdownLabel(value) : inlineText(value)}`).join("\n"),
    ...messages,
  ];
  return {
    filename: `${filename}.${markdown ? "md" : "txt"}`,
    mimeType: `${markdown ? "text/markdown" : "text/plain"};charset=utf-8`,
    content: `${parts.join("\n\n---\n\n")}\n`,
  };
}

export function downloadChatExport(file: ChatExportFile): void {
  const url = URL.createObjectURL(new Blob([file.content], { type: file.mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.hidden = true;
  try {
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    // Keep the URL alive until the browser has started reading the download.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
