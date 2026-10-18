import { ArrowDown, Loader2, Sparkles } from "lucide-solid";
import { createMemo, For, Show } from "solid-js";

import { TranscriptActivityGroup } from "@/components/TranscriptActivityGroup";
import { StatusIndicator } from "@/components/StatusIndicator";
import { TranscriptItemView, type TranscriptAttachment, type TranscriptRole } from "@/components/Transcript";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import { findTranscriptAnswerId, isTranscriptWaitingForModel } from "@/lib/transcriptActivity";
import { THINKING_TEXT } from "@/lib/chatStatus";
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
          Write a message below. Use the project picker beside the + button to choose a project and backend, or start with a temporary workspace.
        </p>
      </div>
    </div>
  );
}

export interface ChatTranscriptProps {
  setTranscriptRef: (el: HTMLDivElement) => void;
  onScroll: () => void;
  onUserScroll: () => void;
  items: ChatTranscriptItem[];
  filteredItems: ChatTranscriptItem[];
  threadLoading: boolean;
  currentThreadId: string;
  streaming: boolean;
  backgroundLive: boolean;
  compacting?: boolean;
  showScrollToBottom: boolean;
  turnAnchorSpacerHeight: number;
  onScrollToBottomClick: () => void;
  conversationBusy: boolean;
  agents: AgentCard[];
  activeAgentName: string;
  onSelectPrompt?: (promptText: string) => void;
  onEditMessage: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
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
  const streamActive = () => props.streaming || props.backgroundLive;
  const isStreamingItem = useStreamingTranscriptItem(
    () => props.items,
    streamActive,
  );
  const activityView = useTranscriptActivityView(
    () => props.filteredItems,
    streamActive,
  );
  const answerItemId = createMemo(() => findTranscriptAnswerId(props.filteredItems));
  const waitingForModel = createMemo(() => !props.compacting
    && isTranscriptWaitingForModel(props.items, streamActive()));
  // Answer actions stay hidden while the agent runs and then belong only to
  // the final answer of the thread.
  const showAssistantActions = (itemId: number) =>
    !streamActive() && answerItemId() === itemId;

  return (
    <div class="transcript-container">
      <div
        class="transcript"
        ref={props.setTranscriptRef}
        onScroll={props.onScroll}
        onWheel={props.onUserScroll}
        onTouchMove={props.onUserScroll}
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
            props.onUserScroll();
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
          <For each={activityView().keys}>
            {(key) => {
              const entry = () => activityView().entries.get(key);
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
                <Show
                  when={item()}
                  fallback={
                    <Show when={rows()}>
                      {(groupRows) => (
                        <TranscriptActivityGroup
                          rows={groupRows()}
                          live={activityView().liveKey === key}
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
                      deferMermaid={isStreamingItem(row().id)}
                      deferHighlight={isStreamingItem(row().id)}
                      showAssistantActions={showAssistantActions(row().id)}
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
              );
            }}
          </For>
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
