import {
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  Image as ImageIcon,
  Pencil,
  X,
} from "lucide-solid";
import { createEffect, createSignal, For, Show } from "solid-js";

import { AgentPicker } from "@/components/AgentPicker";
import { CopyButton } from "@/components/CopyButton";
import { Markdown } from "@/components/Markdown";
import { StatusIndicator } from "@/components/StatusIndicator";
import { isChatStatusText } from "@/lib/chatStatus";
import { formatValue } from "@/lib/utils";
import type { AgentCard } from "@/types";

export const PLAN_MODE_TOOL_NAME = "plan_mode_respond";
export const PLAN_REVIEW_APPROVED_PREFIX = "PLAN_REVIEW_APPROVED";
export const PLAN_REVIEW_REVISION_PREFIX = "PLAN_REVIEW_REVISION_REQUESTED";

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

export type TranscriptPlanReviewStatus = "pending" | "approved" | "revision_requested";

export type TranscriptPlanReview = {
  type: "plan_review";
  tool_call_id?: string | null;
  interrupt_id?: string | null;
  plan: string;
  status: TranscriptPlanReviewStatus;
  targetAgent?: string;
  feedback?: string;
  result?: unknown;
};

export type TranscriptItem = TranscriptMessage | TranscriptTool | TranscriptPlanReview;

type TranscriptToolTarget<T extends TranscriptItem> = Extract<T, { type: "tool" }>;
type TranscriptPlanReviewTarget<T extends TranscriptItem> = Extract<T, { type: "plan_review" }>;

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

export function createTranscriptPlanReview(options: {
  toolCallId?: string | null;
  interruptId?: string | null;
  plan?: string;
  status?: TranscriptPlanReviewStatus;
  targetAgent?: string;
  feedback?: string;
  result?: unknown;
}): TranscriptPlanReview {
  return {
    type: "plan_review",
    tool_call_id: options.toolCallId || null,
    interrupt_id: options.interruptId || null,
    plan: options.plan || "",
    status: options.status || "pending",
    targetAgent: options.targetAgent,
    feedback: options.feedback,
    result: options.result,
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

export function findTranscriptPlanReviewTarget<T extends TranscriptItem>(
  items: T[],
  toolCallId?: string | null,
): TranscriptPlanReviewTarget<T> | undefined {
  if (!toolCallId) {
    return undefined;
  }
  return items.find(
    (item): item is TranscriptPlanReviewTarget<T> =>
      item.type === "plan_review" && item.tool_call_id === toolCallId,
  );
}

export function parsePlanReviewToolResult(
  result: unknown,
): Partial<TranscriptPlanReview> {
  const text = typeof result === "string" ? result : "";
  if (text.startsWith(PLAN_REVIEW_APPROVED_PREFIX)) {
    const targetMatch = text.match(/^Target agent:\s*(.+)$/m);
    return {
      status: "approved",
      targetAgent: targetMatch?.[1]?.trim() || undefined,
      result,
    };
  }
  if (text.startsWith(PLAN_REVIEW_REVISION_PREFIX)) {
    const feedbackMatch = text.match(/User feedback:\n([\s\S]*?)(?:\n\n|$)/);
    return {
      status: "revision_requested",
      feedback: feedbackMatch?.[1]?.trim() || undefined,
      result,
    };
  }
  return { result };
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
              when={isChatStatusText(props.text)}
              fallback={
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
              }
            >
              <StatusIndicator text={props.text} />
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
            <CopyButton
              value={props.text}
              class="message-action-btn"
              title="Copy message"
              ariaLabel="Copy message"
              copiedTitle="Copied"
              successMessage="Message copied."
              failureMessage="Copy failed"
              iconSize={15}
            />
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

export function PlanReviewView(props: {
  plan: string;
  status: TranscriptPlanReviewStatus;
  toolCallId?: string | null;
  interruptId?: string | null;
  targetAgent?: string;
  feedback?: string;
  agents?: AgentCard[];
  activeAgentName?: string;
  itemId?: number;
  actionsDisabled?: boolean;
  onApprove?: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    targetAgent: string;
  }) => void;
  onRevise?: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    feedback: string;
  }) => void;
}) {
  const agents = () => props.agents || [];
  const activeAgent = () => agents().find((agent) => agent.name === props.activeAgentName);
  const approvalTargetAgents = () => {
    const sourceAgent = activeAgent();
    const sourceAgentName = sourceAgent?.name || props.activeAgentName || "";
    const allowedNames = new Set(sourceAgent?.plan_approval_targets || []);
    const candidates = agents().filter((agent) => (
      agent.name !== sourceAgentName && agent.valid && !agent.hidden
    ));
    if (allowedNames.size === 0) {
      return candidates;
    }
    return candidates.filter((agent) => allowedNames.has(agent.name));
  };
  const defaultTargetAgent = () =>
    (props.targetAgent &&
    approvalTargetAgents().some((agent) => agent.name === props.targetAgent)
      ? props.targetAgent
      : approvalTargetAgents()[0]?.name || "");
  const [targetAgent, setTargetAgent] = createSignal(defaultTargetAgent());
  const [feedback, setFeedback] = createSignal("");

  createEffect(() => {
    const options = approvalTargetAgents();
    if (!targetAgent() || !options.some((agent) => agent.name === targetAgent())) {
      setTargetAgent(defaultTargetAgent());
    }
  });

  const pending = () => props.status === "pending";
  const canAct = () => pending() && !props.actionsDisabled;
  const submitFeedback = () => {
    const nextFeedback = feedback().trim();
    if (!nextFeedback || !canAct()) {
      return;
    }
    props.onRevise?.({
      toolCallId: props.toolCallId,
      interruptId: props.interruptId,
      plan: props.plan,
      feedback: nextFeedback,
    });
  };
  const approve = () => {
    const nextAgent = targetAgent().trim();
    if (!nextAgent || !canAct()) {
      return;
    }
    props.onApprove?.({
      toolCallId: props.toolCallId,
      interruptId: props.interruptId,
      plan: props.plan,
      targetAgent: nextAgent,
    });
  };

  return (
    <section class="plan-review" data-transcript-item-id={props.itemId}>
      <div class="plan-review-header">
        <div>
          <div class="plan-review-title">Plan Review</div>
          <Show when={props.status !== "pending"}>
            <div class="plan-review-state">
              <Show
                when={props.status === "approved"}
                fallback={`Revision requested${props.feedback ? `: ${props.feedback}` : ""}`}
              >
                {`Approved${props.targetAgent ? ` for ${props.targetAgent}` : ""}`}
              </Show>
            </div>
          </Show>
        </div>
      </div>
      <Markdown text={props.plan} class="message-markdown plan-review-markdown" />
      <Show when={pending()}>
        <div class="plan-review-controls">
          <div class="plan-review-approve-row">
            <AgentPicker
              class="plan-review-agent-picker"
              value={targetAgent()}
              agents={approvalTargetAgents()}
              disabled={!canAct() || approvalTargetAgents().length === 0}
              onChange={setTargetAgent}
              ariaLabel="Target agent"
            />
            <button
              class="btn primary plan-review-approve-btn"
              type="button"
              onClick={approve}
              disabled={!canAct() || !targetAgent().trim()}
              title="Approve"
              aria-label="Approve plan"
            >
              <Check size={15} />
              <span>Approve</span>
            </button>
          </div>
          <div class="plan-review-feedback-row">
            <textarea
              class="plan-review-feedback-input"
              rows={2}
              value={feedback()}
              disabled={!canAct()}
              placeholder="Add feedback"
              onInput={(event) => setFeedback(event.currentTarget.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  submitFeedback();
                }
              }}
            />
            <button
              class="btn plan-review-feedback-btn"
              type="button"
              onClick={submitFeedback}
              disabled={!canAct() || !feedback().trim()}
              title="Send feedback"
              aria-label="Send plan feedback"
            >
              <Pencil size={15} />
              <span>Send input</span>
            </button>
          </div>
        </div>
      </Show>
    </section>
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
  agents?: AgentCard[];
  activeAgentName?: string;
  actionsDisabled?: boolean;
  onEditMessage?: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
  onBranchSelect?: (checkpointId: string) => void;
  onApprovePlanReview?: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    targetAgent: string;
  }) => void;
  onRevisePlanReview?: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    feedback: string;
  }) => void;
}) {
  if (props.item.type === "message") {
    return (
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
      onEdit={props.onEditMessage}
      onBranchSelect={props.onBranchSelect}
    />
    );
  }
  if (props.item.type === "plan_review") {
    return (
      <PlanReviewView
        plan={props.item.plan}
        status={props.item.status}
        toolCallId={props.item.tool_call_id}
        interruptId={props.item.interrupt_id}
        targetAgent={props.item.targetAgent}
        feedback={props.item.feedback}
        agents={props.agents}
        activeAgentName={props.activeAgentName}
        itemId={props.itemId}
        actionsDisabled={props.actionsDisabled}
        onApprove={props.onApprovePlanReview}
        onRevise={props.onRevisePlanReview}
      />
    );
  }
  return (
    <ToolCallDetail
      name={props.item.name}
      args={props.item.args}
      result={props.item.result}
      itemId={props.itemId}
    />
  );
}
