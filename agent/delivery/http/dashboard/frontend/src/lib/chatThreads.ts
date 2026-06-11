import {
  PLAN_MODE_TOOL_NAME,
  createTranscriptPlanReview,
  createTranscriptUserInputRequest,
  createTranscriptTool,
  findTranscriptPlanReviewTarget,
  findTranscriptToolTarget,
  findTranscriptUserInputRequestTarget,
  parsePlanReviewToolResult,
  parseUserInputRequestToolResult,
} from "@/components/Transcript";
import type { TranscriptAttachment, TranscriptItem } from "@/components/Transcript";
import {
  ASK_USER_TOOL_NAME,
  normalizeUserQuestion,
} from "@/lib/userInputRequest";
import {
  GENERATE_IMAGE_TOOL_NAME,
  generatedImageAttachmentFromToolResult,
} from "@/lib/generatedImages";
import { workspaceDisplayLabelFromValues } from "@/lib/workspace";
import { NO_WORKSPACE_KEY, NO_WORKSPACE_LABEL } from "@/lib/workspaceConstants";
import type { WorkspaceRef } from "@/types";

export type ThreadSummary = {
  thread_id: string;
  latest_checkpoint_id: string;
  checkpoint_count: number;
  platform: string;
  user_id: string;
  channel_id: string;
  agent_name?: string;
  provider?: string;
  model?: string;
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
  message_index?: number;
  source_checkpoint_id?: string;
  parent_checkpoint_id?: string;
  branch?: ThreadMessageBranch;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{ id: string; name: string; args: unknown }>;
  attachments?: TranscriptAttachment[];
};

export type ThreadMessageBranchOption = {
  checkpoint_id: string;
  message: string;
};

export type ThreadMessageBranch = {
  current: number;
  total: number;
  options: ThreadMessageBranchOption[];
};

export type ThreadMessagesPayload = {
  thread_id: string;
  active_checkpoint_id?: string;
  messages: ThreadMessage[];
  platform: string;
  user_id: string;
  channel_id: string;
  agent_name?: string;
  provider?: string;
  model?: string;
  title?: string;
  kind?: string;
  workspace?: WorkspaceRef | null;
};

export type ThreadTranscriptItem = TranscriptItem & { key: string };

export function threadApiPath(threadId: string, checkpointId?: string): string {
  const path = `/dashboard-api/chat-history/${encodeURIComponent(threadId)}`;
  if (!checkpointId) {
    return path;
  }
  return `${path}?checkpoint_id=${encodeURIComponent(checkpointId)}`;
}

export function chatThreadHref(threadId: string): string {
  return `/c/${encodeURIComponent(threadId)}`;
}

export function threadWorkspaceKey(thread: ThreadSummary): string {
  if (thread.workspace_key) {
    return thread.workspace_key;
  }
  const workspace = thread.workspace;
  if (!workspace || !workspace.backend) {
    // Backend may return ``workspace: {}`` for legacy threads; treat that the
    // same as a missing workspace so the key stays a stable, comparable
    // sentinel rather than ``"undefined:undefined"``.
    return NO_WORKSPACE_KEY;
  }
  return `${workspace.backend}:${workspace.locator ?? ""}`;
}

export function threadWorkspaceLabel(thread: ThreadSummary): string {
  if (!thread.workspace) {
    return thread.workspace_label || NO_WORKSPACE_LABEL;
  }
  return workspaceDisplayLabelFromValues(
    thread.workspace_label || thread.workspace.label,
    thread.workspace.locator,
    thread.workspace.metadata,
  ) || NO_WORKSPACE_LABEL;
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
      if (msg.name === GENERATE_IMAGE_TOOL_NAME) {
        const attachment = generatedImageAttachmentFromToolResult(msg.content);
        if (attachment) {
          items.push({
            key: `generated-image-${messageIndex}-${msg.tool_call_id || "unknown"}`,
            type: "message",
            role: "assistant",
            text: "",
            attachments: [attachment],
          });
        }
        return;
      }

      if (msg.name === ASK_USER_TOOL_NAME) {
        const parsed = parseUserInputRequestToolResult(msg.content);
        if (!parsed.valid) {
          return;
        }
        const target = findTranscriptUserInputRequestTarget(items, msg.tool_call_id);
        if (target) {
          Object.assign(target, parsed);
        } else {
          items.push({
            key: `user-input-result-${messageIndex}-${msg.tool_call_id || "unknown"}`,
            ...createTranscriptUserInputRequest({
              toolCallId: msg.tool_call_id,
              status: "answered",
              answers: parsed.answers,
              summary: parsed.summary,
              result: msg.content,
            }),
          });
        }
        if (parsed.summary) {
          items.push({
            key: `user-input-summary-${messageIndex}-${msg.tool_call_id || "unknown"}`,
            type: "message",
            role: "user",
            text: parsed.summary,
          });
        }
        return;
      }

      if (msg.name === PLAN_MODE_TOOL_NAME) {
        const planTarget = findTranscriptPlanReviewTarget(items, msg.tool_call_id);
        if (planTarget) {
          Object.assign(planTarget, parsePlanReviewToolResult(msg.content));
          return;
        }
        items.push({
          key: `plan-review-result-${messageIndex}-${msg.tool_call_id || "unknown"}`,
          ...createTranscriptPlanReview({
            toolCallId: msg.tool_call_id,
            ...parsePlanReviewToolResult(msg.content),
          }),
        });
        return;
      }

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
      const resolvedAttachments = msg.attachments?.map((att) => {
        if (att.kind === "image" && att.base64 && !att.preview_url) {
          return { ...att, preview_url: `data:${att.mime_type};base64,${att.base64}` };
        }
        return att;
      });
      items.push({
        key: `message-${messageIndex}-${msg.id || "unknown"}`,
        type: "message",
        role: msg.role,
        text: msg.content,
        messageIndex: msg.message_index,
        sourceCheckpointId: msg.source_checkpoint_id,
        parentCheckpointId: msg.parent_checkpoint_id,
        branch: msg.branch,
        attachments: resolvedAttachments,
      });
    }

    if (msg.tool_calls?.length) {
      msg.tool_calls.forEach((toolCall, toolCallIndex) => {
        if (toolCall.name === GENERATE_IMAGE_TOOL_NAME) {
          return;
        }
        if (toolCall.name === ASK_USER_TOOL_NAME) {
          const args = toolCall.args as { title?: unknown; questions?: unknown; submit_label?: unknown } | null;
          const questions = Array.isArray(args?.questions)
            ? args.questions
                .map(normalizeUserQuestion)
                .filter((question): question is NonNullable<ReturnType<typeof normalizeUserQuestion>> => Boolean(question))
            : [];
          const existingRequest = findTranscriptUserInputRequestTarget(items, toolCall.id);
          if (existingRequest) {
            if (!existingRequest.questions.length) {
              existingRequest.questions = questions;
            }
            if (!existingRequest.title && typeof args?.title === "string") {
              existingRequest.title = args.title;
            }
            return;
          }
          items.push({
            key: `user-input-request-${messageIndex}-${toolCallIndex}-${toolCall.id || "unknown"}`,
            ...createTranscriptUserInputRequest({
              toolCallId: toolCall.id,
              title: typeof args?.title === "string" ? args.title : "",
              questions,
              submitLabel: typeof args?.submit_label === "string" ? args.submit_label : "",
            }),
          });
          return;
        }
        if (toolCall.name === PLAN_MODE_TOOL_NAME) {
          const args = toolCall.args as { plan?: unknown } | null;
          const existingPlanReview = findTranscriptPlanReviewTarget(items, toolCall.id);
          if (existingPlanReview) {
            if (!existingPlanReview.plan && typeof args?.plan === "string") {
              existingPlanReview.plan = args.plan;
            }
            return;
          }
          items.push({
            key: `plan-review-${messageIndex}-${toolCallIndex}-${toolCall.id || "unknown"}`,
            ...createTranscriptPlanReview({
              toolCallId: toolCall.id,
              plan: typeof args?.plan === "string" ? args.plan : "",
            }),
          });
          return;
        }
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
