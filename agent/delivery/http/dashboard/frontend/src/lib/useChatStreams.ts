import { createSignal } from "solid-js";

import {
  createTranscriptTool,
  findTranscriptToolTarget,
  type TranscriptItem,
} from "@/components/Transcript";
import {
  allocItemId,
  persistedStreams,
  type ChatTranscriptItem,
} from "@/lib/chatStreamStore";
import type { AppendScrollMode } from "@/lib/chatTypes";
import type { useChatScroll } from "@/lib/useChatScroll";

type ChatScroll = ReturnType<typeof useChatScroll>;

type ItemsUpdater =
  | ChatTranscriptItem[]
  | ((prev: ChatTranscriptItem[]) => ChatTranscriptItem[]);

export interface UseChatStreamsParams {
  scroll: ChatScroll;
  getCurrentThreadId: () => string;
  getIsUnmounting: () => boolean;
}

export function useChatStreams(params: UseChatStreamsParams) {
  const { scroll, getCurrentThreadId, getIsUnmounting } = params;

  const [localItems, setLocalItems] = createSignal<ChatTranscriptItem[]>([]);
  const [localStreaming, setLocalStreaming] = createSignal(false);
  const [localController, setLocalController] = createSignal<AbortController | null>(null);
  const [currentStreamThreadId, setCurrentStreamThreadId] = createSignal<string | null>(null);

  const items = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.items[0]();
    }
    return localItems();
  };

  const setItems = (v: ItemsUpdater, targetThreadId?: string) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.items[1](v as any);
      return;
    }
    setLocalItems(v as any);
  };

  const streaming = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.streaming[0]();
    }
    return localStreaming();
  };

  const setStreaming = (v: boolean, targetThreadId?: string) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.streaming[1](v);
      return;
    }
    setLocalStreaming(v);
  };

  const controller = () => {
    const tid = currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      return persistedStreams.get(tid)!.controller[0]();
    }
    return localController();
  };

  const setController = (v: AbortController | null, targetThreadId?: string) => {
    const tid = targetThreadId || currentStreamThreadId();
    if (tid && persistedStreams.has(tid)) {
      persistedStreams.get(tid)!.controller[1](v);
      return;
    }
    setLocalController(v);
  };

  const appendItem = (
    item: TranscriptItem,
    scrollMode: AppendScrollMode = "bottom",
    targetThreadId?: string,
  ): number => {
    const id = allocItemId();
    setItems((current) => [...current, { ...item, id } as ChatTranscriptItem], targetThreadId);

    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      if (scrollMode === "turn-start") {
        scroll.setTurnAnchorItemId(id);
        scroll.setTurnAnchorSpacerHeight(0);
        scroll.setAutoScroll(true);
        scroll.scrollToTurnAnchor(id);
      } else if (scrollMode === "bottom") {
        scroll.scrollToBottom();
      }
    }
    return id;
  };

  const updateMessage = (id: number, chunk: string, targetThreadId?: string) => {
    setItems(
      (current) =>
        current.map((item) =>
          item.id === id && item.type === "message"
            ? { ...item, text: item.text + chunk }
            : item,
        ),
      targetThreadId,
    );
    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      scroll.scrollToBottom();
    }
  };

  const updateToolResult = (
    toolCallId: string,
    name: string,
    result: unknown,
    targetThreadId?: string,
  ) => {
    setItems((current) => {
      const target = findTranscriptToolTarget(current, toolCallId, name);
      if (!target) {
        return [
          ...current,
          {
            id: allocItemId(),
            ...createTranscriptTool({ toolCallId, name, result }),
          } satisfies ChatTranscriptItem,
        ];
      }
      return current.map((item) =>
        item.id === target.id && item.type === "tool" ? { ...item, result } : item,
      );
    }, targetThreadId);

    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      scroll.scrollToBottom();
    }
  };

  return {
    items,
    setItems,
    streaming,
    setStreaming,
    controller,
    setController,
    currentStreamThreadId,
    setCurrentStreamThreadId,
    setLocalItems,
    appendItem,
    updateMessage,
    updateToolResult,
  };
}
