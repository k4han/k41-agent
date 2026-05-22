import {
  createTranscriptTool,
  findTranscriptToolTarget,
} from "@/components/Transcript";
import type { TranscriptAttachment, TranscriptItem } from "@/components/Transcript";
import type { WorkspaceRef } from "@/types";

export type ThreadSummary = {
  thread_id: string;
  latest_checkpoint_id: string;
  checkpoint_count: number;
  platform: string;
  user_id: string;
  channel_id: string;
  agent_name?: string;
  title?: string;
  kind?: string;
  created_at?: string | null;
  updated_at?: string | null;
  workspace?: WorkspaceRef | null;
  workspace_key?: string;
  workspace_label?: string;
};

export type ThreadListPayload = {
  threads: ThreadSummary[];
  has_more?: boolean;
  next_offset?: number;
};

export type ThreadWorkspaceGroup = {
  key: string;
  label: string;
  threads: ThreadSummary[];
};

export type ThreadMessage = {
  id: string | null;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{ id: string; name: string; args: unknown }>;
  attachments?: TranscriptAttachment[];
};

export type ThreadMessagesPayload = {
  thread_id: string;
  messages: ThreadMessage[];
  platform: string;
  user_id: string;
  channel_id: string;
  agent_name?: string;
  title?: string;
  kind?: string;
  workspace?: WorkspaceRef | null;
};

export type ThreadTranscriptItem = TranscriptItem & { key: string };

export function threadApiPath(threadId: string): string {
  return `/dashboard-api/chat-history/${encodeURIComponent(threadId)}`;
}

export function chatThreadHref(threadId: string): string {
  return `/c/${encodeURIComponent(threadId)}`;
}

export const NO_WORKSPACE_KEY = "no-workspace";
export const NO_WORKSPACE_LABEL = "No workspace";

export function threadWorkspaceKey(thread: ThreadSummary): string {
  if (thread.workspace_key) {
    return thread.workspace_key;
  }
  if (!thread.workspace) {
    return NO_WORKSPACE_KEY;
  }
  return `${thread.workspace.backend}:${thread.workspace.locator}`;
}

export function threadWorkspaceLabel(thread: ThreadSummary): string {
  return (
    thread.workspace_label
    || thread.workspace?.label
    || thread.workspace?.locator
    || NO_WORKSPACE_LABEL
  );
}

export function groupThreadsByWorkspace(threads: ThreadSummary[]): ThreadWorkspaceGroup[] {
  const groups: ThreadWorkspaceGroup[] = [];
  const groupIndexes = new Map<string, number>();

  threads.forEach((thread) => {
    const key = threadWorkspaceKey(thread);
    const existingIndex = groupIndexes.get(key);
    if (existingIndex !== undefined) {
      groups[existingIndex].threads.push(thread);
      return;
    }

    groupIndexes.set(key, groups.length);
    groups.push({
      key,
      label: threadWorkspaceLabel(thread),
      threads: [thread],
    });
  });

  return groups;
}

export function toThreadTranscript(messages: ThreadMessage[]): ThreadTranscriptItem[] {
  const items: ThreadTranscriptItem[] = [];

  messages.forEach((msg, messageIndex) => {
    if (msg.role === "tool") {
      const target = findTranscriptToolTarget(items, msg.tool_call_id, msg.name);

      if (target) {
        target.result = msg.content;
        target.tool_call_id = target.tool_call_id || msg.tool_call_id || null;
        target.name = target.name || msg.name || "unknown";
        return;
      }

      items.push({
        key: `tool-result-${messageIndex}-${msg.tool_call_id || msg.name || "unknown"}`,
        ...createTranscriptTool({
          toolCallId: msg.tool_call_id,
          name: msg.name,
          result: msg.content,
        }),
      });
      return;
    }

    if (msg.content || !msg.tool_calls?.length) {
      items.push({
        key: `message-${messageIndex}-${msg.id || "unknown"}`,
        type: "message",
        role: msg.role,
        text: msg.content,
        attachments: msg.attachments,
      });
    }

    if (msg.tool_calls?.length) {
      msg.tool_calls.forEach((toolCall, toolCallIndex) => {
        items.push({
          key: `tool-call-${messageIndex}-${toolCallIndex}-${toolCall.id || "unknown"}`,
          ...createTranscriptTool({
            toolCallId: toolCall.id,
            name: toolCall.name,
            args: toolCall.args,
          }),
        });
      });
    }
  });

  return items;
}
