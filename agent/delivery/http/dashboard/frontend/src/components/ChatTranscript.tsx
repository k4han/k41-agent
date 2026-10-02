import { ArrowDown, Loader2, Sparkles } from "lucide-solid";
import { For, Show } from "solid-js";

import { TranscriptItemView, type TranscriptAttachment, type TranscriptRole } from "@/components/Transcript";
import { WorkspaceSelector } from "@/components/WorkspaceSelector";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import type { AgentCard, WorkspaceRef } from "@/types";
import type { WorkspaceSelectionDraft } from "@/components/WorkspaceSelector";

export interface ChatWelcomeHeroProps {
  workingDir: string;
  defaultWorkingDir: string;
  workspace: WorkspaceRef | null;
  workspaceSelection: WorkspaceSelectionDraft | null;
  conversationBusy: boolean;
  onWorkspaceSelectionChange: (value: WorkspaceSelectionDraft) => void;
  onSelectPrompt?: (promptText: string) => void;
}

export function ChatWelcomeHero(props: ChatWelcomeHeroProps) {
  return (
    <div class="chat-welcome-hero">
      <div class="chat-welcome-hero-header">
        <div class="chat-welcome-hero-badge">
          <Sparkles size={20} />
        </div>
        <h1 class="chat-welcome-hero-title">How can Kai help you today?</h1>
        <p class="chat-welcome-hero-desc">
          Select a workspace context below, or write your own instructions.
        </p>
      </div>

      <div class="chat-workspace-empty">
        <div class="chat-workspace-empty-inner">
          <WorkspaceSelector
            workingDir={props.workingDir}
            defaultWorkingDir={props.defaultWorkingDir}
            workspace={props.workspace}
            selection={props.workspaceSelection}
            locked={false}
            disabled={props.conversationBusy}
            onSelectionChange={props.onWorkspaceSelectionChange}
          />
        </div>
      </div>
    </div>
  );
}

export interface ChatTranscriptProps {
  setTranscriptRef: (el: HTMLDivElement) => void;
  onScroll: () => void;
  items: ChatTranscriptItem[];
  filteredItems: ChatTranscriptItem[];
  threadLoading: boolean;
  currentThreadId: string;
  streaming: boolean;
  backgroundLive: boolean;
  compacting?: boolean;
  autoScroll: boolean;
  turnAnchorSpacerHeight: number;
  onScrollToBottomClick: () => void;
  workingDir: string;
  defaultWorkingDir: string;
  workspace: WorkspaceRef | null;
  workspaceSelection: WorkspaceSelectionDraft | null;
  conversationBusy: boolean;
  agents: AgentCard[];
  activeAgentName: string;
  onWorkspaceSelectionChange: (value: WorkspaceSelectionDraft) => void;
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
  return (
    <div class="transcript-container">
      <div class="transcript" ref={props.setTranscriptRef} onScroll={props.onScroll}>
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
                  <ChatWelcomeHero
                    workingDir={props.workingDir}
                    defaultWorkingDir={props.defaultWorkingDir}
                    workspace={props.workspace}
                    workspaceSelection={props.workspaceSelection}
                    conversationBusy={props.conversationBusy}
                    onWorkspaceSelectionChange={props.onWorkspaceSelectionChange}
                    onSelectPrompt={props.onSelectPrompt}
                  />
                </Show>
              }
            >
              <div class="empty">Loading thread...</div>
            </Show>
          }
        >
          <For each={props.filteredItems}>
            {(item) => (
              <TranscriptItemView
                item={item}
                itemId={item.id}
                deferMermaid={props.streaming || props.backgroundLive}
                deferHighlight={props.streaming || props.backgroundLive}
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
          </For>
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
      <Show when={!props.autoScroll}>
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
