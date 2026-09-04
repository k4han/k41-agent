import {
  Check,
  ChevronLeft,
  ChevronRight,
  Code,
  Download,
  Expand,
  ExternalLink,
  Eye,
  FileText,
  GripHorizontal,
  Image as ImageIcon,
  Maximize2,
  Monitor,
  Pencil,
  RotateCcw,
  Shrink,
  Smartphone,
  Tablet,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-solid";
import {
  createEffect,
  createMemo,
  createSignal,
  For,
  onCleanup,
  onMount,
  Show,
  type Accessor,
} from "solid-js";
import { Portal } from "solid-js/web";

import { AgentPicker } from "@/components/AgentPicker";
import { CopyButton } from "@/components/CopyButton";
import { Markdown } from "@/components/Markdown";
import { StatusIndicator } from "@/components/StatusIndicator";
import { useToast } from "@/components/Toast";
import { isChatStatusText } from "@/lib/chatStatus";
import { getSharedDarkMode } from "@/lib/theme";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import {
  HTML_PREVIEW_TOOL_NAME,
  buildPreviewChromeStyle,
  htmlPreviewFromArgs,
  injectPreviewChrome,
  isFullPageHtml,
  PREVIEW_VIEWPORT_WIDTHS,
  type HtmlPreviewContent,
  type PreviewViewport,
} from "@/lib/htmlPreview";
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
  kind: "text" | "image" | "file";
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
  deferHighlight?: boolean;
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
  threadId?: string | null;
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
                    deferHighlight={props.deferHighlight}
                    threadId={props.threadId}
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
  threadId?: string | null;
}) {
  const generatedImage = () =>
    props.name === GENERATE_IMAGE_TOOL_NAME
      ? generatedImageFromToolResult(props.result, props.threadId)
      : null;

  // HTML previews replace the raw args/result view entirely: the agent
  // already sent the document, so showing it rendered is more useful than
  // JSON noise. The fallback keeps normal tool call rendering when the args
  // do not contain a usable HTML payload.
  const htmlPreview = () =>
    props.name === HTML_PREVIEW_TOOL_NAME ? htmlPreviewFromArgs(props.args) : null;

  // When the user expands a tool call to inspect its details, notify the
  // transcript scroll controller so it stops following the stream. Without
  // this, the next streamed chunk would scroll back to the turn anchor and
  // yank the expanded tool out of view.
  const handleToggle = (event: Event) => {
    const details = event.currentTarget as HTMLDetailsElement;
    // Only react when expanding; collapsing must not interrupt following.
    if (!details.open) {
      return;
    }
    details.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, { bubbles: true }),
    );
  };

  return (
    <Show
      when={htmlPreview()}
      fallback={
        <details
          class="tool-call"
          open={props.defaultOpen ?? false}
          data-transcript-item-id={props.itemId}
          onToggle={handleToggle}
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
      }
    >
      {(preview) => <HtmlPreviewBlock preview={preview()} itemId={props.itemId} result={props.result} />}
    </Show>
  );
}

function HtmlPreviewBlock(props: {
  preview: HtmlPreviewContent;
  itemId?: number;
  result?: unknown;
}) {
  const [mode, setMode] = createSignal<"preview" | "source">("preview");
  const [expanded, setExpanded] = createSignal(false);
  const [isFullWidth, setIsFullWidth] = createSignal(false);
  const title = () => props.preview.title || "HTML Preview";

  // Use shared dark mode signal to avoid creating a MutationObserver per preview.
  const darkMode = getSharedDarkMode();
  const previewSrc = createMemo(() => {
    darkMode();
    return injectPreviewChrome(props.preview.html, buildPreviewChromeStyle());
  });

  const isPage = createMemo(() => isFullPageHtml(props.preview.html, props.preview.mode));

  const defaultViewportFor = (page: boolean): PreviewViewport =>
    page ? "desktop" : "responsive";
  const defaultHeightFor = (page: boolean): number => (page ? 520 : 360);

  const [viewport, setViewport] = createSignal<PreviewViewport>(
    defaultViewportFor(isPage()),
  );
  const [scaleToFit, setScaleToFit] = createSignal(true);
  const [zoom, setZoom] = createSignal(1.0);

  const [height, setHeight] = createSignal(defaultHeightFor(isPage()));
  const [autoHeight, setAutoHeight] = createSignal(!isPage());
  const [contentHeight, setContentHeight] = createSignal<number | null>(null);
  const { showToast } = useToast();

  // Reset per-preview state when Solid reuses this component instance for a
  // different tool call (new html/mode). Without this a card rendered after
  // a full page would keep the desktop viewport and tall frame.
  const [previewKey, setPreviewKey] = createSignal(
    `${props.preview.html.length}:${props.preview.mode ?? "auto"}`,
  );
  createEffect(() => {
    const key = `${props.preview.html.length}:${props.preview.mode ?? "auto"}:${props.preview.html.slice(0, 128)}`;
    if (key !== previewKey()) {
      setPreviewKey(key);
      const page = isFullPageHtml(props.preview.html, props.preview.mode);
      setViewport(defaultViewportFor(page));
      setHeight(defaultHeightFor(page));
      setAutoHeight(!page);
      setContentHeight(null);
      setZoom(1.0);
      setScaleToFit(true);
    }
  });

  let inlineStageRef: HTMLDivElement | undefined;
  let inlineFrameRef: HTMLIFrameElement | undefined;
  let dialogStageRef: HTMLDivElement | undefined;
  let dialogFrameRef: HTMLIFrameElement | undefined;

  const [inlineStageWidth, setInlineStageWidth] = createSignal(960);
  const [dialogStageWidth, setDialogStageWidth] = createSignal(1200);
  const [dialogStageHeight, setDialogStageHeight] = createSignal(750);

  let inlineStageObserver: ResizeObserver | undefined;
  let dialogStageObserver: ResizeObserver | undefined;

  const attachInlineStage = (el: HTMLDivElement | undefined) => {
    inlineStageRef = el;
    inlineStageObserver?.disconnect();
    inlineStageObserver = undefined;
    if (el) {
      inlineStageObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          if (entry.contentRect.width > 0) {
            setInlineStageWidth(entry.contentRect.width);
          }
        }
      });
      inlineStageObserver.observe(el);
    }
  };

  const attachDialogStage = (el: HTMLDivElement | undefined) => {
    dialogStageRef = el;
    dialogStageObserver?.disconnect();
    dialogStageObserver = undefined;
    if (el) {
      const update = (rect: DOMRectReadOnly) => {
        if (rect.width > 0) {
          setDialogStageWidth(rect.width);
        }
        if (rect.height > 0) {
          setDialogStageHeight(rect.height);
        }
      };
      dialogStageObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          update(entry.contentRect);
        }
      });
      dialogStageObserver.observe(el);
      update(el.getBoundingClientRect());
    }
  };

  onCleanup(() => {
    inlineStageObserver?.disconnect();
    dialogStageObserver?.disconnect();
  });

  const clampFrameHeight = (value: number): number =>
    Math.max(180, Math.min(850, Math.ceil(value) + 16));

  const handleWindowMessage = (event: MessageEvent) => {
    if (
      event.data &&
      typeof event.data === "object" &&
      event.data.type === "k41-preview-height" &&
      Number.isFinite(event.data.height)
    ) {
      const measured = event.data.height as number;
      if (measured <= 0 || measured > 10000) {
        return;
      }
      const isFromInline = inlineFrameRef && event.source === inlineFrameRef.contentWindow;
      const isFromDialog = dialogFrameRef && event.source === dialogFrameRef.contentWindow;
      if (isFromInline || isFromDialog) {
        const prev = contentHeight();
        if (prev !== null && Math.abs(measured - prev) <= 1) {
          return;
        }
        setContentHeight(measured);
        if (autoHeight() && isFromInline) {
          const clamped = clampFrameHeight(measured);
          if (Math.abs(clamped - height()) > 1) {
            setHeight(clamped);
          }
        }
      }
    }
  };

  onMount(() => {
    window.addEventListener("message", handleWindowMessage);
    onCleanup(() => window.removeEventListener("message", handleWindowMessage));
  });

  const isSuccessResult = () =>
    typeof props.result === "string" && props.result.startsWith("HTML preview ready");
  const hasErrorResult = () =>
    props.result !== null && props.result !== undefined && !isSuccessResult();

  const handleEscKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      setExpanded(false);
    }
  };
  createEffect(() => {
    if (expanded()) {
      window.addEventListener("keydown", handleEscKeyDown);
    } else {
      window.removeEventListener("keydown", handleEscKeyDown);
    }
  });
  onCleanup(() => window.removeEventListener("keydown", handleEscKeyDown));

  // NOTE: the inline iframe is sandboxed (no allow-same-origin), but the
  // new-tab blob URL runs with full blob-origin privileges (scripts, fetch,
  // storage). This is intentional for faithful full-page viewing, but the
  // HTML may come from untrusted agent output, so treat the new tab as
  // untrusted web content.
  const handleOpenNewTab = () => {
    try {
      const blob = new Blob([previewSrc()], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const opened = window.open(url, "_blank", "noopener,noreferrer");
      if (!opened) {
        URL.revokeObjectURL(url);
        showToast("Popup blocked. Allow popups to open the preview.", "error");
        return;
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      showToast("Could not open preview in new tab", "error");
    }
  };

  const handleResizeStart = (event: PointerEvent) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = height();
    const cleanup = () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };
    const onPointerMove = (e: PointerEvent) => {
      const deltaY = e.clientY - startY;
      const nextHeight = Math.max(180, Math.min(1600, startHeight + deltaY));
      setHeight(nextHeight);
      setAutoHeight(false);
    };
    const onPointerUp = () => {
      cleanup();
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };

  const handleResetHeight = () => {
    setAutoHeight(true);
    const ch = contentHeight();
    if (Number.isFinite(ch) && (ch as number) > 0) {
      setHeight(clampFrameHeight(ch as number));
    } else {
      setHeight(defaultHeightFor(isPage()));
    }
  };

  const handleZoomIn = () => {
    setZoom((z) => +(Math.min(2.5, z + 0.15)).toFixed(2));
  };

  const handleZoomOut = () => {
    setZoom((z) => +(Math.max(0.3, z - 0.15)).toFixed(2));
  };

  const handleZoomReset = () => {
    setZoom(1.0);
    setScaleToFit(true);
  };

  const targetWidth = createMemo(() => PREVIEW_VIEWPORT_WIDTHS[viewport()]);

  const effectiveScale = (availableWidth: number) => {
    const tw = targetWidth();
    const z = zoom();
    if (tw === null) {
      return z;
    }
    const cw = availableWidth > 0 ? availableWidth : 960;
    if (scaleToFit()) {
      const fit = cw < tw ? cw / tw : 1;
      return +(fit * z).toFixed(3);
    }
    return z;
  };

  const renderStage = (isDialog: boolean) => {
    const availableWidth = isDialog ? dialogStageWidth() : inlineStageWidth();
    const tw = targetWidth();
    const s = effectiveScale(availableWidth);
    const frameHeight = isDialog ? dialogStageHeight() : height();
    const safeScale = Number.isFinite(s) && s > 0 ? s : 1;

    return (
      <div
        ref={(el) => {
          if (isDialog) {
            attachDialogStage(el);
          } else {
            attachInlineStage(el);
          }
        }}
        class="tool-html-preview-stage"
        style={{
          height: isDialog ? "100%" : `${height()}px`,
        }}
      >
        <Show
          when={tw !== null}
          fallback={
            <iframe
              ref={(el) => (isDialog ? (dialogFrameRef = el) : (inlineFrameRef = el))}
              class="tool-html-preview-frame"
              style={{
                width: "100%",
                height: "100%",
                border: "0",
                zoom: `${zoom()}`,
              }}
              sandbox="allow-scripts allow-forms"
              srcdoc={previewSrc()}
              title={title()}
            />
          }
        >
          <div
            class="tool-html-preview-viewport-wrapper"
            style={{
              width: scaleToFit()
                ? `${Math.round(tw! * safeScale)}px`
                : `${tw!}px`,
              height: isDialog ? "100%" : `${height()}px`,
              position: "relative",
              overflow: scaleToFit() ? "hidden" : "auto",
              "flex-shrink": "0",
              margin: "0 auto",
              "max-width": "100%",
            }}
          >
            <iframe
              ref={(el) => (isDialog ? (dialogFrameRef = el) : (inlineFrameRef = el))}
              class="tool-html-preview-frame"
              style={{
                position: scaleToFit() ? "absolute" : "static",
                top: "0",
                left: "0",
                width: `${tw!}px`,
                height: scaleToFit()
                  ? `${Math.round(frameHeight / safeScale)}px`
                  : `${Math.round(frameHeight)}px`,
                transform: scaleToFit() ? `scale(${safeScale})` : undefined,
                "transform-origin": "top left",
                border: "0",
              }}
              sandbox="allow-scripts allow-forms"
              srcdoc={previewSrc()}
              title={title()}
            />
          </div>
        </Show>
      </div>
    );
  };

  return (
    <>
      <section
        class="tool-html-preview"
        classList={{
          "is-page": isPage(),
          "is-fullwidth": isFullWidth(),
        }}
        data-transcript-item-id={props.itemId}
      >
        <div class="tool-html-preview-header">
          <div class="tool-html-preview-title-wrap">
            <span class="mono tool-html-preview-title">{title()}</span>
            <Show when={isPage()}>
              <span class="tool-html-preview-tag" title="Full web page layout detected">
                Full Page
              </span>
            </Show>
          </div>
          <div class="tool-html-preview-actions">
            <HtmlPreviewToolbar
              mode={mode}
              onToggleMode={() => setMode(mode() === "preview" ? "source" : "preview")}
              html={props.preview.html}
              viewport={viewport}
              onSelectViewport={setViewport}
              zoom={zoom}
              onZoomIn={handleZoomIn}
              onZoomOut={handleZoomOut}
              onZoomReset={handleZoomReset}
              scaleToFit={scaleToFit}
              onToggleScaleToFit={() => setScaleToFit(!scaleToFit())}
              onOpenNewTab={handleOpenNewTab}
              isFullWidth={isFullWidth}
              onToggleFullWidth={() => setIsFullWidth(!isFullWidth())}
              onExpandDialog={() => setExpanded(true)}
              autoHeight={autoHeight}
              onResetHeight={handleResetHeight}
            />
          </div>
        </div>
        <Show when={isPage() && mode() === "preview"}>
          <div class="tool-html-preview-hint">
            <span>Full-page content scaled for desktop preview. Switch device, zoom, or open in new tab.</span>
          </div>
        </Show>
        <Show
          when={mode() === "preview"}
          fallback={<pre class="tool-html-preview-source">{props.preview.html}</pre>}
        >
          {renderStage(false)}
          <div
            class="tool-html-preview-resizer"
            onPointerDown={handleResizeStart}
            onDblClick={handleResetHeight}
            title="Drag to resize height (Double-click to auto-fit)"
            aria-label="Resize preview height"
          >
            <GripHorizontal size={14} />
          </div>
        </Show>
        <Show when={hasErrorResult()}>
          <pre class="tool-html-preview-error">{formatValue(props.result)}</pre>
        </Show>
      </section>
      <Show when={expanded()}>
        <Portal>
          <div
            class="dialog-backdrop"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                setExpanded(false);
              }
            }}
          >
            <section
              class="dialog tool-html-preview-dialog"
              role="dialog"
              aria-modal="true"
              aria-label={title()}
            >
              <header class="dialog-header tool-html-preview-dialog-header">
                <div class="tool-html-preview-title-wrap">
                  <span class="mono tool-html-preview-title">{title()}</span>
                  <Show when={isPage()}>
                    <span class="tool-html-preview-tag">Full Page</span>
                  </Show>
                </div>
                <div class="tool-html-preview-actions">
                  <HtmlPreviewToolbar
                    mode={mode}
                    onToggleMode={() => setMode(mode() === "preview" ? "source" : "preview")}
                    html={props.preview.html}
                    viewport={viewport}
                    onSelectViewport={setViewport}
                    zoom={zoom}
                    onZoomIn={handleZoomIn}
                    onZoomOut={handleZoomOut}
                    onZoomReset={handleZoomReset}
                    scaleToFit={scaleToFit}
                    onToggleScaleToFit={() => setScaleToFit(!scaleToFit())}
                    onOpenNewTab={handleOpenNewTab}
                    isDialog={true}
                  />
                  <button
                    class="btn btn-icon btn-sm"
                    type="button"
                    onClick={() => setExpanded(false)}
                    title="Close"
                    aria-label="Close preview popup"
                  >
                    <X size={15} />
                  </button>
                </div>
              </header>
              <div class="tool-html-preview-dialog-body">
                <Show
                  when={mode() === "preview"}
                  fallback={<pre class="tool-html-preview-source">{props.preview.html}</pre>}
                >
                  {renderStage(true)}
                </Show>
                <Show when={hasErrorResult()}>
                  <pre class="tool-html-preview-error">{formatValue(props.result)}</pre>
                </Show>
              </div>
            </section>
          </div>
        </Portal>
      </Show>
    </>
  );
}

function HtmlPreviewToolbar(props: {
  mode: Accessor<"preview" | "source">;
  onToggleMode: () => void;
  html: string;
  viewport: Accessor<PreviewViewport>;
  onSelectViewport: (vp: PreviewViewport) => void;
  zoom: Accessor<number>;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onZoomReset: () => void;
  scaleToFit?: Accessor<boolean>;
  onToggleScaleToFit?: () => void;
  onOpenNewTab: () => void;
  isDialog?: boolean;
  isFullWidth?: Accessor<boolean>;
  onToggleFullWidth?: () => void;
  onExpandDialog?: () => void;
  autoHeight?: Accessor<boolean>;
  onResetHeight?: () => void;
}) {
  const isPreview = () => props.mode() === "preview";

  return (
    <>
      <Show when={isPreview()}>
        <div class="tool-html-preview-btn-group" title="Preview Viewport Mode">
          <button
            class="message-action-btn"
            classList={{ "is-active": props.viewport() === "responsive" }}
            type="button"
            onClick={() => props.onSelectViewport("responsive")}
            title="Responsive (Fluid 100%)"
            aria-label="Responsive viewport"
          >
            Auto
          </button>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.viewport() === "desktop" }}
            type="button"
            onClick={() => props.onSelectViewport("desktop")}
            title="Desktop (1280px, scale-to-fit)"
            aria-label="Desktop viewport"
          >
            <Monitor size={13} />
          </button>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.viewport() === "tablet" }}
            type="button"
            onClick={() => props.onSelectViewport("tablet")}
            title="Tablet (768px)"
            aria-label="Tablet viewport"
          >
            <Tablet size={13} />
          </button>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.viewport() === "mobile" }}
            type="button"
            onClick={() => props.onSelectViewport("mobile")}
            title="Mobile (375px)"
            aria-label="Mobile viewport"
          >
            <Smartphone size={13} />
          </button>
        </div>

        <div class="tool-html-preview-btn-group" title="Zoom Controls">
          <button
            class="message-action-btn"
            type="button"
            onClick={props.onZoomOut}
            title="Zoom out (-15%)"
            aria-label="Zoom out"
          >
            <ZoomOut size={13} />
          </button>
          <button
            class="tool-html-preview-zoom-btn"
            type="button"
            onClick={props.onZoomReset}
            title="Click to reset zoom (100%)"
            aria-label="Reset zoom"
          >
            {Math.round(props.zoom() * 100)}%
          </button>
          <button
            class="message-action-btn"
            type="button"
            onClick={props.onZoomIn}
            title="Zoom in (+15%)"
            aria-label="Zoom in"
          >
            <ZoomIn size={13} />
          </button>
        </div>

        <Show when={props.onToggleScaleToFit && props.viewport() !== "responsive"}>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.scaleToFit?.() }}
            type="button"
            onClick={props.onToggleScaleToFit}
            title={
              props.scaleToFit?.()
                ? "Scale to fit enabled (click for 1:1 with scroll)"
                : "Scale to fit disabled (click to fit width)"
            }
            aria-label="Toggle scale to fit"
            aria-pressed={props.scaleToFit?.()}
          >
            <Shrink size={13} />
          </button>
        </Show>

        <Show when={!props.isDialog && props.onResetHeight}>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.autoHeight?.() }}
            type="button"
            onClick={props.onResetHeight}
            title={
              props.autoHeight?.()
                ? "Auto-height enabled (click to re-fit)"
                : "Fit content height"
            }
            aria-label="Fit content height"
          >
            <RotateCcw size={13} />
          </button>
        </Show>

        <Show when={!props.isDialog && props.onToggleFullWidth}>
          <button
            class="message-action-btn"
            classList={{ "is-active": props.isFullWidth?.() }}
            type="button"
            onClick={props.onToggleFullWidth}
            title={props.isFullWidth?.() ? "Reset chat width" : "Expand to full width"}
            aria-label="Toggle full width preview"
          >
            <Show when={props.isFullWidth?.()} fallback={<Expand size={13} />}>
              <Shrink size={13} />
            </Show>
          </button>
        </Show>
      </Show>

      <button
        class="message-action-btn"
        type="button"
        onClick={props.onOpenNewTab}
        title="Open in new browser tab"
        aria-label="Open in new browser tab"
      >
        <ExternalLink size={13} />
      </button>

      <Show when={!props.isDialog && props.onExpandDialog}>
        <button
          class="message-action-btn"
          type="button"
          onClick={props.onExpandDialog}
          title="Open in popup dialog"
          aria-label="Open preview in large popup"
        >
          <Maximize2 size={13} />
        </button>
      </Show>

      <button
        class="message-action-btn"
        type="button"
        onClick={props.onToggleMode}
        title={isPreview() ? "Show HTML source" : "Show rendered preview"}
        aria-label={isPreview() ? "Show HTML source" : "Show rendered preview"}
      >
        <Show when={isPreview()} fallback={<Eye size={13} />}>
          <Code size={13} />
        </Show>
      </button>

      <CopyButton
        value={props.html}
        class="message-action-btn"
        title="Copy HTML"
        ariaLabel="Copy HTML"
        copiedTitle="Copied"
        successMessage="HTML copied."
        failureMessage="Copy failed"
        iconSize={13}
      />
    </>
  );
}

export function TranscriptItemView(props: {
  item: TranscriptItem;
  deferMermaid?: boolean;
  deferHighlight?: boolean;
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
  threadId?: string | null;
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
      deferHighlight={props.deferHighlight}
      itemId={props.itemId}
      actionsDisabled={props.actionsDisabled}
      onEdit={props.onEditMessage}
      onBranchSelect={props.onBranchSelect}
      onMessageClick={props.onMessageClick}
      threadId={props.threadId}
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
      threadId={props.threadId}
    />
  );
}
