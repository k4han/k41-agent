import { Check, ChevronRight, Loader2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, createUniqueId, For, onCleanup, Show } from "solid-js";

import { TranscriptItemView, type TranscriptItemViewProps } from "@/components/Transcript";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { toolCallSummary } from "@/lib/toolCallSummary";
import { hasToolCallResult } from "@/lib/toolCallStatus";

export interface TranscriptActivityGroupProps
  extends Omit<
    TranscriptItemViewProps,
    "item" | "itemId" | "deferMermaid" | "deferHighlight" | "showAssistantActions"
  > {
  /** Folded process rows, in transcript order. */
  rows: ChatTranscriptItem[];
  /** True while this group may receive more calls from the running turn. */
  live: boolean;
  isItemStreaming?: (id: number) => boolean;
}

/**
 * Consecutive tools expand downward below their own count and status header.
 * Surrounding messages remain independent and retain transcript order. A
 * manual toggle takes precedence over the automatic live/collapsed state.
 */
export function TranscriptActivityGroup(props: TranscriptActivityGroupProps) {
  // null means "follow the automatic state".
  const [userOpen, setUserOpen] = createSignal<boolean | null>(null);
  const open = () => userOpen() ?? props.live;
  const [visited, setVisited] = createSignal(open());
  const mounted = () => visited() || open();
  const bodyId = createUniqueId();
  let groupRef: HTMLDivElement | undefined;
  let bodyRef: HTMLDivElement | undefined;
  let contentRef: HTMLDivElement | undefined;
  let following = true;
  let previousScrollTop = 0;
  let touchY: number | undefined;
  let pendingScrollFrame: number | undefined;

  const pauseFollowing = () => {
    following = false;
    if (pendingScrollFrame !== undefined) {
      window.cancelAnimationFrame(pendingScrollFrame);
      pendingScrollFrame = undefined;
    }
  };
  const isAtBottom = () => !!bodyRef
    && bodyRef.scrollHeight - bodyRef.clientHeight - bodyRef.scrollTop <= 2;
  const scheduleFollow = () => {
    if (!following || !props.live || !open() || pendingScrollFrame !== undefined) return;
    pendingScrollFrame = window.requestAnimationFrame(() => {
      pendingScrollFrame = undefined;
      if (!following || !props.live || !open() || !bodyRef) return;
      bodyRef.scrollTop = bodyRef.scrollHeight;
      previousScrollTop = bodyRef.scrollTop;
    });
  };
  const handleBodyScroll = (event: Event) => {
    event.stopPropagation();
    if (!bodyRef || !open()) return;
    const nextScrollTop = bodyRef.scrollTop;
    // Content growth and our own scrolls must not be mistaken for scrolling up.
    if (nextScrollTop < previousScrollTop && !isAtBottom()) {
      pauseFollowing();
    } else if (nextScrollTop > previousScrollTop && isAtBottom()) {
      following = true;
    }
    previousScrollTop = nextScrollTop;
  };

  createEffect(() => {
    if (open()) setVisited(true);
    if (props.live && open()) scheduleFollow();
  });
  createEffect(() => {
    if (!mounted()) return;
    // Observe the content, since the viewport stops growing at its height cap.
    const observer = new ResizeObserver(scheduleFollow);
    if (contentRef) observer.observe(contentRef);
    onCleanup(() => {
      observer.disconnect();
      if (pendingScrollFrame !== undefined) window.cancelAnimationFrame(pendingScrollFrame);
    });
  });

  const toggle = () => {
    // Pause following before the layout changes, including manual collapses.
    groupRef?.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, { bubbles: true }),
    );
    setUserOpen(!open());
  };

  const toolCount = () =>
    props.rows.reduce((count, row) => (row.type === "tool" ? count + 1 : count), 0);
  const runningTool = createMemo(() => props.live
    ? props.rows.find((row) => row.type === "tool" && !hasToolCallResult(row))
    : undefined);
  const running = () => !!runningTool();
  const liveDetail = () => {
    const tool = runningTool();
    if (!tool || tool.type !== "tool") {
      return "";
    }
    return toolCallSummary(tool.name, tool.args);
  };
  const label = () => {
    const tools = toolCount();
    return `${tools} tool call${tools === 1 ? "" : "s"} · ${running() ? "Running" : "Finished"}`;
  };

  return (
    <div
      class="activity-group"
      classList={{ "is-live": props.live, "is-open": open() }}
      ref={groupRef}
    >
      <button
        type="button"
        class="activity-group-summary activity-group-toggle"
        aria-expanded={open()}
        aria-controls={bodyId}
        title={open() ? "Collapse tool calls" : "Expand tool calls"}
        onClick={toggle}
      >
        <ChevronRight size={16} class="activity-group-chevron" />
        <span class="activity-group-icon">
          <Show when={running()} fallback={<Check size={14} />}>
            <Loader2 size={14} class="spin-icon" />
          </Show>
        </span>
        <span class="activity-group-label">{label()}</span>
        <Show when={running() && liveDetail()}>
          {(detail) => <span class="activity-group-detail">{detail()}</span>}
        </Show>
      </button>
      <div
        id={bodyId}
        ref={bodyRef}
        class="activity-group-body"
        hidden={!open()}
        role="region"
        aria-label="Tool calls"
        tabIndex={0}
        onWheel={(event) => {
          event.stopPropagation();
          if (event.deltaY < 0) pauseFollowing();
          else if (event.deltaY > 0 && isAtBottom()) {
            following = true;
            scheduleFollow();
          }
        }}
        onTouchStart={(event) => { touchY = event.touches[0]?.clientY; }}
        onTouchMove={(event) => {
          event.stopPropagation();
          const nextY = event.touches[0]?.clientY;
          if (touchY !== undefined && nextY !== undefined && nextY > touchY) pauseFollowing();
          touchY = nextY;
        }}
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
            pauseFollowing();
          }
        }}
        onScroll={handleBodyScroll}
      >
        <Show when={mounted()}>
          <div class="activity-group-content" ref={contentRef}>
            <For each={props.rows}>
              {(row) => (
                <TranscriptItemView
                  item={row}
                  itemId={row.id}
                  deferMermaid={props.isItemStreaming?.(row.id)}
                  deferHighlight={props.isItemStreaming?.(row.id)}
                  agents={props.agents}
                  activeAgentName={props.activeAgentName}
                  actionsDisabled={props.actionsDisabled}
                  onEditMessage={props.onEditMessage}
                  onRegenerateMessage={props.onRegenerateMessage}
                  onBranchSelect={props.onBranchSelect}
                  onApprovePlanReview={props.onApprovePlanReview}
                  onRevisePlanReview={props.onRevisePlanReview}
                  onMessageClick={props.onMessageClick}
                  threadId={props.threadId}
                />
              )}
            </For>
          </div>
        </Show>
      </div>
    </div>
  );
}
