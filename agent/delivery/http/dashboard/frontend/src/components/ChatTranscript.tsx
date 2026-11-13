import { ArrowDown, Loader2, Sparkles } from "lucide-solid";
import { createVirtualizer, defaultRangeExtractor, type Range, type VirtualItem } from "@tanstack/solid-virtual";
import { createEffect, createMemo, For, onCleanup, onMount, Show } from "solid-js";

import { TranscriptActivityGroup } from "@/components/TranscriptActivityGroup";
import { StatusIndicator } from "@/components/StatusIndicator";
import { TranscriptItemView, type ForkMessagePayload, type TranscriptAttachment, type TranscriptRole } from "@/components/Transcript";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import { findTranscriptAnswerId, findTranscriptCompletedAnswerIds, findTranscriptResumeId, isTranscriptWaitingForModel, type TranscriptActivityEntry } from "@/lib/transcriptActivity";
import { THINKING_TEXT } from "@/lib/chatStatus";
import { useShowThinking } from "@/lib/useShowThinking";
import { useStreamingTranscriptItem } from "@/lib/useStreamingTranscriptItem";
import { useTranscriptActivityView } from "@/lib/useTranscriptActivityView";
import type { AgentCard } from "@/types";

export function ChatWelcomeHero() {
  return (
    <div class="chat-welcome-hero">
      <div class="chat-welcome-hero-header">
        <div class="chat-welcome-hero-badge">
          <Sparkles size={20} />
        </div>
        <h1 class="chat-welcome-hero-title">How can Kai help you today?</h1>
        <p class="chat-welcome-hero-desc">
          Write a message below. Use the project picker beside the + button to choose a project and backend, or start with No workspace.
        </p>
      </div>
    </div>
  );
}

export interface ChatTranscriptProps {
  setTranscriptRef: (el: HTMLDivElement) => void;
  onScroll: () => void;
  onUserScroll: (direction?: number) => void;
  items: ChatTranscriptItem[];
  filteredItems: ChatTranscriptItem[];
  threadLoading: boolean;
  currentThreadId: string;
  streaming: boolean;
  backgroundLive: boolean;
  compacting?: boolean;
  forkingItemId?: number | null;
  onForkMessage?: (payload: ForkMessagePayload) => void;
  showScrollToBottom: boolean;
  turnAnchorSpacerHeight: number;
  turnAnchorItemId?: number | null;
  onScrollToBottomClick: () => void;
  conversationBusy: boolean;
  agents: AgentCard[];
  activeAgentName: string;
  onSelectPrompt?: (promptText: string) => void;
  onResume?: () => void;
  onEditMessage: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
  hideRegenerateAction?: boolean;
  onRegenerateMessage?: (payload: {
    itemId?: number;
    messageIndex?: number;
    sourceCheckpointId?: string;
  }) => void;
  onBranchSelect: (checkpointId: string) => void;
  onApprovePlanReview: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    targetAgent: string;
  }) => void;
  onRevisePlanReview: (payload: {
    toolCallId?: string | null;
    interruptId?: string | null;
    plan: string;
    feedback: string;
  }) => void;
  onMessageClick?: (payload: { text: string; role: TranscriptRole; attachments?: TranscriptAttachment[] }) => void;
}

export function ChatTranscript(props: ChatTranscriptProps) {
  let previousTouchY: number | undefined;
  let transcriptElement: HTMLDivElement | undefined;
  const streamActive = () => props.streaming || props.backgroundLive;
  const isStreamingItem = useStreamingTranscriptItem(
    () => props.items,
    streamActive,
  );
  const activityView = useTranscriptActivityView(
    () => props.filteredItems,
    streamActive,
  );
  // Keep a newly submitted prompt mounted until the scroll controller anchors it.
  const anchorIndex = createMemo(() => props.turnAnchorItemId == null
    ? -1 : activityView().keys.indexOf(`item-${props.turnAnchorItemId}`));
  const itemKey = createMemo(() => {
    const keys = activityView().keys;
    const threadId = props.currentThreadId;
    return (index: number) => `${threadId}:${keys[index]}`;
  });
  const virtualizer = createVirtualizer<HTMLDivElement, HTMLDivElement>({
    get count() { return activityView().keys.length; },
    getScrollElement: () => transcriptElement ?? null,
    get getItemKey() { return itemKey(); },
    estimateSize: () => 160,
    overscan: 5,
    // The list starts after the transcript's vertical padding.
    scrollMargin: 18,
    get rangeExtractor() {
      const anchor = anchorIndex();
      return (range: Range) => {
        const indexes = defaultRangeExtractor(range);
        return anchor < 0 || indexes.includes(anchor)
          ? indexes : [...indexes, anchor].sort((a, b) => a - b);
      };
    },
  });
  const virtualRows = createMemo(() => new Map(
    virtualizer.getVirtualItems().map((row) => [String(row.key), row]),
  ));
  const virtualKeys = createMemo(() => Array.from(virtualRows().keys()));
  onMount(() => {
    const element = transcriptElement;
    if (!element) return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return;
      width = element.clientWidth;
      // Wrapped text and rich content need new measurements after a panel resize.
      virtualizer.measure();
    });
    observer.observe(element);
    onCleanup(() => observer.disconnect());
  });
  const answerItemId = createMemo(() => findTranscriptAnswerId(props.filteredItems));
  const completedAnswerIds = createMemo(() => streamActive() ? new Set<number>() : findTranscriptCompletedAnswerIds(props.items));
  const resumeItemId = createMemo(() => findTranscriptResumeId(props.items));
  const { showThinking } = useShowThinking();
  // Hidden reasoning still means the model is working on its reply.
  const thinkingHidden = createMemo(() => streamActive() && !showThinking()
    && props.items[props.items.length - 1]?.type === "thinking");
  const waitingForModel = createMemo(() => !props.compacting
    && (isTranscriptWaitingForModel(props.items, streamActive()) || thinkingHidden()));
  // Each completed turn can be copied or forked; regeneration stays on the latest answer.
  const showAssistantActions = (itemId: number) =>
    !streamActive() && (completedAnswerIds().has(itemId) || answerItemId() === itemId);

  return (
    <div class="transcript-container">
      <div
        class="transcript"
        ref={(element) => {
          transcriptElement = element;
          props.setTranscriptRef(element);
        }}
        onScroll={props.onScroll}
        onWheel={(event) => {
          if (!event.ctrlKey) props.onUserScroll(event.deltaY);
        }}
        onTouchStart={(event) => { previousTouchY = event.touches[0]?.clientY; }}
        onTouchMove={(event) => {
          const nextY = event.touches[0]?.clientY;
          if (previousTouchY !== undefined && nextY !== undefined) {
            props.onUserScroll(previousTouchY - nextY);
          }
          previousTouchY = nextY;
        }}
        onTouchEnd={() => { previousTouchY = undefined; }}
        onTouchCancel={() => { previousTouchY = undefined; }}
        onPointerDown={(event) => {
          if (event.target === event.currentTarget) {
            props.onUserScroll();
          }
        }}
        onKeyDown={(event) => {
          if (
            event.target === event.currentTarget
            && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)
          ) {
            const scrollingUp = ["ArrowUp", "PageUp", "Home"].includes(event.key)
              || (event.key === " " && event.shiftKey);
            props.onUserScroll(scrollingUp ? -1 : 1);
          }
        }}
        tabIndex={0}
      >
        <Show
          when={props.items.length > 0}
          fallback={
            <Show
              when={props.threadLoading}
              fallback={
                <Show
                  when={!props.currentThreadId}
                  fallback={<div class="empty">Send a message to continue this thread.</div>}
                >
                  <ChatWelcomeHero />
                </Show>
              }
            >
              <div class="empty">Loading thread...</div>
            </Show>
          }
        >
          <div class="transcript-virtual-list" style={{ height: `${virtualizer.getTotalSize()}px` }}>
            <For each={virtualKeys()}>
              {(key) => {
                const virtualRow = createMemo<VirtualItem>((previous) => virtualRows().get(key) ?? previous!);
                const entryKey = key.slice(key.lastIndexOf(":") + 1);
                // Retain the outgoing row until the virtualizer unmounts it.
                // Store reconciliation can replace all keys in the same batch.
                const entry = createMemo<TranscriptActivityEntry<ChatTranscriptItem> | undefined>(
                  (previous) => activityView().entries.get(entryKey) ?? previous,
                );
                const item = () => {
                  const current = entry();
                  if (!current || current.kind !== "item") {
                    return null;
                  }
                  return current.item;
                };
                const rows = () => {
                  const current = entry();
                  if (!current || current.kind !== "activity") {
                    return null;
                  }
                  return current.items;
                };
                return (
                  <div
                    class="transcript-virtual-row"
                    data-index={virtualRow().index}
                    ref={(element) => {
                      createEffect(() => {
                        virtualRow().index;
                        virtualizer.measureElement(element);
                      });
                      onCleanup(() => virtualizer.measureElement(null));
                    }}
                    style={{ transform: `translateY(${virtualRow().start - virtualizer.options.scrollMargin}px)` }}
                  >
                    <Show
                      when={item()}
                      fallback={
                        <Show when={rows()}>
                          {(groupRows) => (
                            <TranscriptActivityGroup
                              rows={groupRows()}
                              live={activityView().liveKey === entryKey}
                              isItemStreaming={isStreamingItem}
                              agents={props.agents}
                              activeAgentName={props.activeAgentName}
                              actionsDisabled={props.conversationBusy}
                              threadId={props.currentThreadId}
                              onEditMessage={props.onEditMessage}
                              onRegenerateMessage={props.onRegenerateMessage}
                              onBranchSelect={props.onBranchSelect}
                              onApprovePlanReview={props.onApprovePlanReview}
                              onRevisePlanReview={props.onRevisePlanReview}
                              onMessageClick={props.onMessageClick}
                            />
                          )}
                        </Show>
                      }
                    >
                      {(row) => (
                        <TranscriptItemView
                          item={row()}
                          itemId={row().id}
                          deferHighlight={isStreamingItem(row().id)}
                          streaming={isStreamingItem(row().id)}
                          showAssistantActions={showAssistantActions(row().id)}
                          showRegenerateAction={!props.hideRegenerateAction && answerItemId() === row().id}
                          forking={props.forkingItemId === row().id}
                          onForkMessage={completedAnswerIds().has(row().id) ? props.onForkMessage : undefined}
                          onResume={props.currentThreadId && resumeItemId() === row().id ? props.onResume : undefined}
                          agents={props.agents}
                          activeAgentName={props.activeAgentName}
                          actionsDisabled={props.conversationBusy || props.compacting || props.threadLoading}
                          threadId={props.currentThreadId}
                          onEditMessage={props.onEditMessage}
                          onRegenerateMessage={props.onRegenerateMessage}
                          onBranchSelect={props.onBranchSelect}
                          onApprovePlanReview={props.onApprovePlanReview}
                          onRevisePlanReview={props.onRevisePlanReview}
                          onMessageClick={props.onMessageClick}
                        />
                      )}
                    </Show>
                  </div>
                );
              }}
            </For>
          </div>
          <Show when={waitingForModel()}>
            <div class="message assistant transcript-thinking-indicator">
              <div class="message-bubble">
                <StatusIndicator text={THINKING_TEXT} />
              </div>
            </div>
          </Show>
          <Show when={props.compacting}>
            <div class="transcript-compacting-indicator" role="status" aria-live="polite">
              <div class="transcript-compacting-indicator-inner">
                <Loader2 size={16} class="spin-icon" />
                <span class="transcript-compacting-text">Compacting conversation...</span>
              </div>
            </div>
          </Show>
          <Show when={props.turnAnchorSpacerHeight > 0}>
            <div
              class="transcript-anchor-spacer"
              style={`height: ${props.turnAnchorSpacerHeight}px;`}
              aria-hidden="true"
            />
          </Show>
        </Show>
      </div>
      <Show when={props.items.length > 0 && props.showScrollToBottom}>
        <button
          class="scroll-to-bottom-btn"
          type="button"
          onClick={props.onScrollToBottomClick}
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
        >
          <ArrowDown size={18} />
        </button>
      </Show>
    </div>
  );
}
