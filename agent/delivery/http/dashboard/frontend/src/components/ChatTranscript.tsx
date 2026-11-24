import { ArrowDown, CirclePause, Loader2, Sparkles } from "lucide-solid";
import { createVirtualizer, defaultRangeExtractor, type Range, type VirtualItem } from "@tanstack/solid-virtual";
import { createEffect, createMemo, For, on, onCleanup, Show } from "solid-js";

import { TranscriptActivityGroup } from "@/components/TranscriptActivityGroup";
import { StatusIndicator } from "@/components/StatusIndicator";
import { TranscriptItemView, type ForkMessagePayload, type TranscriptAttachment, type TranscriptRole } from "@/components/Transcript";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import { liveAsyncTask, type AsyncTask } from "@/lib/asyncSubagents";
import { findTranscriptAnswerId, findTranscriptCompletedAnswerIds, findTranscriptResumeId, isTranscriptWaitingForModel, type TranscriptActivityEntry } from "@/lib/transcriptActivity";
import { THINKING_TEXT } from "@/lib/chatStatus";
import { useShowThinking } from "@/lib/useShowThinking";
import { useStreamingTranscriptItem } from "@/lib/useStreamingTranscriptItem";
import { useTranscriptActivityView } from "@/lib/useTranscriptActivityView";
import type { AgentCard } from "@/types";

const HISTORY_START_THRESHOLD = 1;

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
  onLoadEarlierMessages?: (preservePosition: (apply: () => void) => void) => void;
  loadingEarlierMessages?: boolean;
  items: ChatTranscriptItem[];
  filteredItems: ChatTranscriptItem[];
  threadLoading: boolean;
  currentThreadId: string;
  streaming: boolean;
  backgroundLive: boolean;
  asyncTasks?: readonly AsyncTask[];
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
  let previousScrollTop = 0;
  createEffect(on([() => props.currentThreadId, () => props.threadLoading], () => {
    previousScrollTop = 0;
  }));
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
  const answerItemId = createMemo(() => findTranscriptAnswerId(props.filteredItems));
  const completedAnswerIds = createMemo(() => streamActive() ? new Set<number>() : findTranscriptCompletedAnswerIds(props.items));
  const resumeItemId = createMemo(() => findTranscriptResumeId(props.items));
  const { showThinking } = useShowThinking();
  // Hidden reasoning still means the model is working on its reply.
  const thinkingHidden = createMemo(() => streamActive() && !showThinking()
    && props.items[props.items.length - 1]?.type === "thinking");
  const waitingForModel = createMemo(() => !props.compacting
    && (isTranscriptWaitingForModel(props.items, streamActive()) || thinkingHidden()));
  const pendingAgents = createMemo(() => (props.asyncTasks || []).filter((task) =>
    task.parent_thread_id === props.currentThreadId && !task.read_only && liveAsyncTask(task.status)));
  const waitingForAgents = createMemo(() => !streamActive() && !props.conversationBusy
    && !props.threadLoading && !props.compacting && pendingAgents().length > 0);
  const agentsNeedInput = () => pendingAgents().every((task) => task.status === "waiting");
  const agentWaitLabel = () => {
    const count = pendingAgents().length;
    const agents = `${count} background agent${count === 1 ? "" : "s"}`;
    return agentsNeedInput()
      ? `${agents} ${count === 1 ? "needs" : "need"} your response`
      : `Waiting for ${agents}...`;
  };
  // Each completed turn can be copied or forked; regeneration stays on the latest answer.
  const showAssistantActions = (itemId: number) =>
    !streamActive() && (completedAnswerIds().has(itemId) || answerItemId() === itemId);

  const preserveHistoryPosition = (apply: () => void) => {
    const scrollTop = Math.max(0, transcriptElement?.scrollTop ?? 0);
    const anchor = virtualizer.getVirtualItemForOffset(scrollTop);
    const view = activityView();
    const entry = anchor && view.entries.get(view.keys[anchor.index]);
    const offset = anchor ? scrollTop - anchor.start : 0;
    apply();
    if (!entry || !transcriptElement?.isConnected) return;

    const next = activityView();
    let index = next.keys.indexOf(entry.key);
    if (index < 0 && entry.kind === "activity") {
      // The older page may extend this group and change its leading item's key.
      const itemIds = new Set(entry.items.map(item => item.id));
      index = next.keys.findIndex(key => {
        const candidate = next.entries.get(key);
        return candidate?.kind === "activity" && candidate.items.some(item => itemIds.has(item.id));
      });
    }
    // The virtualizer has rebuilt its measurements for the prepended rows.
    const start = virtualizer.measurementsCache[index]?.start;
    if (start !== undefined) virtualizer.scrollToOffset(start + offset);
  };

  const handleScroll = (event: Event & { currentTarget: HTMLDivElement }) => {
    const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
    const reachedStart = previousScrollTop > HISTORY_START_THRESHOLD && scrollTop <= HISTORY_START_THRESHOLD
      && scrollHeight - clientHeight > HISTORY_START_THRESHOLD;
    previousScrollTop = scrollTop;
    props.onScroll();
    if (reachedStart) props.onLoadEarlierMessages?.(preserveHistoryPosition);
  };

  const handleUserScroll = (direction: number) => {
    props.onUserScroll(direction);
    // At the top, an upward gesture can leave scrollTop unchanged.
    if (direction < 0 && transcriptElement && transcriptElement.scrollTop <= HISTORY_START_THRESHOLD) {
      props.onLoadEarlierMessages?.(preserveHistoryPosition);
    }
  };

  return (
    <div class="transcript-container">
      <div
        class="transcript"
        ref={(element) => {
          transcriptElement = element;
          props.setTranscriptRef(element);
        }}
        onScroll={handleScroll}
        onWheel={(event) => {
          if (!event.ctrlKey) handleUserScroll(event.deltaY);
        }}
        onTouchStart={(event) => { previousTouchY = event.touches[0]?.clientY; }}
        onTouchMove={(event) => {
          const nextY = event.touches[0]?.clientY;
          if (previousTouchY !== undefined && nextY !== undefined) {
            handleUserScroll(previousTouchY - nextY);
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
            handleUserScroll(scrollingUp ? -1 : 1);
          }
        }}
        tabIndex={0}
      >
        <Show
          when={props.items.length > 0 || pendingAgents().length > 0}
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
                    class="transcript-virtual-row chat-column"
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
          <Show when={waitingForAgents()}>
            <div class="transcript-async-wait" role="status" aria-live="polite" aria-atomic="true"
              title={`${agentWaitLabel()}\n${pendingAgents().map((task) => task.agent_name).join(", ")}`}>
              <span class="transcript-async-wait-content">
                <Show when={agentsNeedInput()} fallback={<Loader2 size={14} class="spin-icon" aria-hidden="true" />}>
                  <CirclePause size={14} aria-hidden="true" />
                </Show>
                <span class="transcript-async-wait-label">{agentWaitLabel()}</span>
              </span>
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
      <Show when={props.loadingEarlierMessages}>
        <div class="transcript-history-loading" role="status" aria-live="polite">
          <Loader2 size={16} class="spin-icon" aria-hidden="true" />
          <span class="sr-only">Loading earlier messages...</span>
        </div>
      </Show>
      <Show when={props.items.length > 0 && props.showScrollToBottom}>
        <button
          class="scroll-to-bottom-btn"
          classList={{ "is-streaming": streamActive() }}
          type="button"
          onClick={props.onScrollToBottomClick}
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
          data-streaming={streamActive() ? "true" : undefined}
        >
          <Show
            when={streamActive()}
            fallback={<ArrowDown size={18} />}
          >
            <span class="scroll-to-bottom-dots" aria-hidden="true">
              <span class="scroll-to-bottom-dot" />
              <span class="scroll-to-bottom-dot" />
              <span class="scroll-to-bottom-dot" />
            </span>
          </Show>
        </button>
      </Show>
    </div>
  );
}
