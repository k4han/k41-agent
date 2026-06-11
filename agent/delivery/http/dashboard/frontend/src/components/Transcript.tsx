import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Image as ImageIcon,
  Pencil,
} from "lucide-solid";
import { createEffect, createSignal, For, Show } from "solid-js";

import { AgentPicker } from "@/components/AgentPicker";
import { CopyButton } from "@/components/CopyButton";
import { Markdown } from "@/components/Markdown";
import { StatusIndicator } from "@/components/StatusIndicator";
import { useToast } from "@/components/Toast";
import { isChatStatusText } from "@/lib/chatStatus";
import {
  GENERATE_IMAGE_TOOL_NAME,
  generatedImageFromToolResult,
} from "@/lib/generatedImages";
import { formatValue } from "@/lib/utils";
import {
  parseAskUserToolResult,
  type ParsedAskUserToolResult,
  type UserQuestion,
  type UserQuestionAnswer,
} from "@/lib/userInputRequest";
import type { AgentCard } from "@/types";

export const PLAN_MODE_TOOL_NAME = "plan_mode_respond";
export const PLAN_REVIEW_APPROVED_PREFIX = "PLAN_REVIEW_APPROVED";
export const PLAN_REVIEW_REVISION_PREFIX = "PLAN_REVIEW_REVISION_REQUESTED";
const PLAN_REVIEW_REVISION_INSTRUCTION =
  "\n\nRevise the plan according to the feedback and call plan_mode_respond again.";

export type TranscriptRole = "user" | "assistant" | "error" | "system";

export type TranscriptAttachment = {
  name: string;
  mime_type: string;
  size: number;
  kind: "text" | "image";
  content?: string;
  base64?: string;
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
  generatedImagePending?: boolean;
  generatedImageToolCallId?: string | null;
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

export type TranscriptUserInputRequestStatus = "pending" | "answered";

export type TranscriptUserInputRequest = {
  type: "user_input_request";
  tool_call_id?: string | null;
  interrupt_id?: string | null;
  title?: string;
  questions: UserQuestion[];
  submit_label?: string;
  status: TranscriptUserInputRequestStatus;
  answers?: UserQuestionAnswer[];
  summary?: string;
  result?: unknown;
};

export type TranscriptItem =
  | TranscriptMessage
  | TranscriptTool
  | TranscriptPlanReview
  | TranscriptUserInputRequest;

type TranscriptToolTarget<T extends TranscriptItem> = Extract<T, { type: "tool" }>;
type TranscriptPlanReviewTarget<T extends TranscriptItem> = Extract<T, { type: "plan_review" }>;
type TranscriptUserInputRequestTarget<T extends TranscriptItem> =
  Extract<T, { type: "user_input_request" }>;

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

export function createTranscriptUserInputRequest(options: {
  toolCallId?: string | null;
  interruptId?: string | null;
  title?: string;
  questions?: UserQuestion[];
  submitLabel?: string;
  status?: TranscriptUserInputRequestStatus;
  answers?: UserQuestionAnswer[];
  summary?: string;
  result?: unknown;
}): TranscriptUserInputRequest {
  return {
    type: "user_input_request",
    tool_call_id: options.toolCallId || null,
    interrupt_id: options.interruptId || null,
    title: options.title || "",
    questions: options.questions || [],
    submit_label: options.submitLabel || "",
    status: options.status || "pending",
    answers: options.answers,
    summary: options.summary,
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

export function findTranscriptUserInputRequestTarget<T extends TranscriptItem>(
  items: T[],
  toolCallId?: string | null,
): TranscriptUserInputRequestTarget<T> | undefined {
  if (!toolCallId) {
    return undefined;
  }
  return items.find(
    (item): item is TranscriptUserInputRequestTarget<T> =>
      item.type === "user_input_request" && item.tool_call_id === toolCallId,
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
    const feedbackPrefix = `${PLAN_REVIEW_REVISION_PREFIX}\nUser feedback:\n`;
    let feedback = "";
    if (text.startsWith(feedbackPrefix)) {
      feedback = text.slice(feedbackPrefix.length);
      if (feedback.endsWith(PLAN_REVIEW_REVISION_INSTRUCTION)) {
        feedback = feedback.slice(0, -PLAN_REVIEW_REVISION_INSTRUCTION.length);
      }
    }
    return {
      status: "revision_requested",
      feedback: feedback.trim() || undefined,
      result,
    };
  }
  return { result };
}

export function parseUserInputRequestToolResult(
  result: unknown,
): Partial<TranscriptUserInputRequest> & ParsedAskUserToolResult {
  const parsed = parseAskUserToolResult(result);
  return {
    valid: parsed.valid,
    status: "answered",
    answers: parsed.answers,
    summary: parsed.summary,
    result,
  };
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
  generatedImagePending?: boolean;
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
  onMessageClick?: (payload: { text: string; role: TranscriptRole; attachments?: TranscriptAttachment[] }) => void;
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
  const largeImageAttachments = () =>
    (props.attachments || []).filter(
      (attachment) =>
        props.role === "assistant" &&
        attachment.kind === "image" &&
        Boolean(attachment.preview_url),
    );
  const compactAttachments = () =>
    (props.attachments || []).filter(
      (attachment) =>
        !(
          props.role === "assistant" &&
          attachment.kind === "image" &&
          Boolean(attachment.preview_url)
        ),
    );
  const showGeneratedImagePlaceholder = () =>
    props.role === "assistant" && Boolean(props.generatedImagePending);
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
      <div class={`message-bubble${editing() ? " editing" : ""}`}>
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
                  class="message-edit-btn message-edit-btn--secondary"
                  type="button"
                  onClick={() => {
                    setEditing(false);
                    setDraft(props.text);
                  }}
                  title="Cancel edit"
                  aria-label="Cancel edit"
                >
                  Cancel
                </button>
                <button
                  class="message-edit-btn message-edit-btn--primary"
                  type="button"
                  onClick={submitEdit}
                  disabled={!draft().trim()}
                  title="Save edit"
                  aria-label="Save edit"
                >
                  Send
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
        <Show when={showGeneratedImagePlaceholder()}>
          <div
            class="message-generated-image-placeholder"
            role="status"
            aria-live="polite"
          >
            <div class="message-generated-image-placeholder-shimmer" />
            <div class="message-generated-image-placeholder-content">
              <ImageIcon size={22} />
              <span>Generating image...</span>
            </div>
          </div>
        </Show>
        <Show when={largeImageAttachments().length}>
          <div class="message-generated-images">
            <For each={largeImageAttachments()}>
              {(attachment) => (
                <a
                  class="message-generated-image"
                  href={attachment.preview_url}
                  target="_blank"
                  rel="noreferrer"
                  title={attachment.name}
                >
                  <img src={attachment.preview_url} alt={attachment.name} />
                </a>
              )}
            </For>
          </div>
        </Show>
        <Show when={compactAttachments().length}>
          <div
            class="message-attachments"
            onClick={() => {
              if (!editing()) {
                props.onMessageClick?.({ text: props.text, role: props.role, attachments: compactAttachments() });
              }
            }}
            style="cursor: pointer;"
          >
            <For each={compactAttachments()}>
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
          <div class="message-actions" aria-label="Message actions" onClick={(e) => e.stopPropagation()}>
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
  const { showToast } = useToast();
  const buildFileName = () => {
    const stamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .replace(/T/, "_")
      .replace(/Z$/, "");
    return `plan-${stamp}.md`;
  };
  const downloadPlan = () => {
    const text = (props.plan || "").trim();
    if (!text) {
      return;
    }
    try {
      const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = buildFileName();
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      showToast("Plan downloaded.");
    } catch (_error) {
      showToast("Download failed", "error");
    }
  };
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
        <div class="plan-review-actions" aria-label="Plan actions">
          <CopyButton
            value={() => props.plan}
            class="message-action-btn plan-review-action-btn"
            title="Copy plan"
            ariaLabel="Copy plan"
            copiedTitle="Copied"
            successMessage="Plan copied."
            failureMessage="Copy failed"
            iconSize={15}
            disabled={!props.plan.trim()}
          />
          <button
            class="message-action-btn plan-review-action-btn"
            type="button"
            onClick={downloadPlan}
            disabled={!props.plan.trim()}
            title="Download plan"
            aria-label="Download plan"
          >
            <Download size={15} />
          </button>
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
  const generatedImage = () =>
    props.name === GENERATE_IMAGE_TOOL_NAME
      ? generatedImageFromToolResult(props.result)
      : null;

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
        <Show when={generatedImage()}>
          {(image) => (
            <a
              class="tool-generated-image"
              href={image().url}
              target="_blank"
              rel="noreferrer"
            >
              <img src={image().url} alt={image().filename} />
            </a>
          )}
        </Show>
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
  onMessageClick?: (payload: { text: string; role: TranscriptRole; attachments?: TranscriptAttachment[] }) => void;
}) {
  if (props.item.type === "message") {
    return (
    <TranscriptMessageView
      role={props.item.role}
      text={props.item.text}
      generatedImagePending={props.item.generatedImagePending}
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
      onMessageClick={props.onMessageClick}
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
  if (props.item.type === "user_input_request") {
    return null;
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
