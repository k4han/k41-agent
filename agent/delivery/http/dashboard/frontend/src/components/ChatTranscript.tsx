import { ArrowDown } from "lucide-solid";
import { For, Show } from "solid-js";

import { TranscriptItemView } from "@/components/Transcript";
import { WorkspaceSelector } from "@/components/WorkspaceSelector";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import type { WorkspaceRef } from "@/types";

export interface ChatTranscriptProps {
  setTranscriptRef: (el: HTMLDivElement) => void;
  onScroll: () => void;
  items: ChatTranscriptItem[];
  filteredItems: ChatTranscriptItem[];
  threadLoading: boolean;
  currentThreadId: string;
  streaming: boolean;
  backgroundLive: boolean;
  autoScroll: boolean;
  turnAnchorSpacerHeight: number;
  onScrollToBottomClick: () => void;
  workingDir: string;
  defaultWorkingDir: string;
  workspace: WorkspaceRef | null;
  conversationBusy: boolean;
  onWorkspaceResolved: (value: WorkspaceRef | string | null) => void;
  onEditMessage: (payload: {
    itemId?: number;
    messageIndex: number;
    sourceCheckpointId: string;
    text: string;
  }) => void;
  onBranchSelect: (checkpointId: string) => void;
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
                  <div class="chat-workspace-empty">
                    <WorkspaceSelector
                      workingDir={props.workingDir}
                      defaultWorkingDir={props.defaultWorkingDir}
                      threadId={props.currentThreadId}
                      workspace={props.workspace}
                      locked={false}
                      disabled={props.conversationBusy}
                      onResolved={props.onWorkspaceResolved}
                    />
                  </div>
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
                actionsDisabled={props.conversationBusy}
                onEditMessage={props.onEditMessage}
                onBranchSelect={props.onBranchSelect}
              />
            )}
          </For>
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
