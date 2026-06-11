import { createSignal } from "solid-js";

import {
  createTranscriptTool,
  createTranscriptUserInputRequest,
  findTranscriptPlanReviewTarget,
  findTranscriptToolTarget,
  findTranscriptUserInputRequestTarget,
  parsePlanReviewToolResult,
  parseUserInputRequestToolResult,
  type TranscriptItem,
  type TranscriptPlanReview,
  type TranscriptUserInputRequest,
} from "@/components/Transcript";
import {
  allocItemId,
  persistedStreams,
  type ChatTranscriptItem,
} from "@/lib/chatStreamStore";
import type { AppendScrollMode } from "@/lib/chatTypes";
import {
  GENERATE_IMAGE_TOOL_NAME,
  generatedImageAttachmentFromToolResult,
} from "@/lib/generatedImages";
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

  const replaceMessage = (id: number, text: string, targetThreadId?: string) => {
    setItems(
      (current) =>
        current.map((item) =>
          item.id === id && item.type === "message"
            ? { ...item, text }
            : item,
        ),
      targetThreadId,
    );
    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      scroll.scrollToBottom();
    }
  };

  const removeItem = (id: number, targetThreadId?: string) => {
    setItems((current) => current.filter((item) => item.id !== id), targetThreadId);
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
      if (name === GENERATE_IMAGE_TOOL_NAME) {
        const attachment = generatedImageAttachmentFromToolResult(result);
        const pendingTarget = current.find(
          (item) =>
            item.type === "message" &&
            item.generatedImagePending &&
            (
              item.generatedImageToolCallId === toolCallId ||
              !item.generatedImageToolCallId
            ),
        );
        if (!attachment) {
          if (!pendingTarget) {
            return current;
          }
          return current.map((item) =>
            item.id === pendingTarget.id && item.type === "message"
              ? {
                  ...item,
                  generatedImagePending: false,
                  text: "Image generation did not return an image.",
                }
              : item,
          );
        }
        const exists = current.some(
          (item) =>
            item.type === "message" &&
            item.attachments?.some(
              (existing) => existing.preview_url === attachment.preview_url,
            ),
        );
        if (exists) {
          return pendingTarget
            ? current.filter((item) => item.id !== pendingTarget.id)
            : current;
        }
        if (pendingTarget) {
          return current.map((item) =>
            item.id === pendingTarget.id && item.type === "message"
              ? {
                  ...item,
                  generatedImagePending: false,
                  attachments: [attachment],
                }
              : item,
          );
        }
        return [
          ...current,
          {
            id: allocItemId(),
            type: "message",
            role: "assistant",
            text: "",
            attachments: [attachment],
          } satisfies ChatTranscriptItem,
        ];
      }

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

  const updatePlanReviewResult = (
    toolCallId: string,
    result: unknown,
    targetThreadId?: string,
  ) => {
    setItems((current) => {
      const target = findTranscriptPlanReviewTarget(current, toolCallId);
      if (!target) {
        return current;
      }
      const parsed = parsePlanReviewToolResult(result);
      return current.map((item) =>
        item.id === target.id && item.type === "plan_review"
          ? { ...item, ...parsed }
          : item,
      );
    }, targetThreadId);

    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      scroll.scrollToBottom();
    }
  };

  const updateUserInputRequestResult = (
    toolCallId: string,
    result: unknown,
    targetThreadId?: string,
  ) => {
    setItems((current) => {
      const target = findTranscriptUserInputRequestTarget(current, toolCallId);
      const parsed = parseUserInputRequestToolResult(result);
      if (!parsed.valid) {
        return current;
      }
      const shouldAppendSummary = Boolean(parsed.summary) && (
        !target || target.status !== "answered" || target.summary !== parsed.summary
      );
      const nextItems = target
        ? current.map((item) =>
            item.id === target.id && item.type === "user_input_request"
              ? { ...item, ...parsed }
              : item,
          )
        : [
            ...current,
            {
              id: allocItemId(),
              ...createTranscriptUserInputRequest({
                toolCallId,
                status: "answered",
                answers: parsed.answers,
                summary: parsed.summary,
                result,
              }),
            } satisfies ChatTranscriptItem,
          ];
      if (!shouldAppendSummary) {
        return nextItems;
      }
      return [
        ...nextItems,
        {
          id: allocItemId(),
          type: "message",
          role: "user",
          text: parsed.summary,
        } satisfies ChatTranscriptItem,
      ];
    }, targetThreadId);

    const isCurrent = !getIsUnmounting() && (!targetThreadId || targetThreadId === getCurrentThreadId());
    if (isCurrent) {
      scroll.scrollToBottom();
    }
  };

  const updatePlanReview = (
    toolCallId: string | null | undefined,
    patch: Partial<TranscriptPlanReview>,
    targetThreadId?: string,
  ) => {
    if (!toolCallId) {
      return;
    }
    setItems(
      (current) =>
        current.map((item) =>
          item.type === "plan_review" && item.tool_call_id === toolCallId
            ? { ...item, ...patch }
            : item,
        ),
      targetThreadId,
    );
  };

  const updateUserInputRequest = (
    toolCallId: string | null | undefined,
    patch: Partial<TranscriptUserInputRequest>,
    targetThreadId?: string,
  ) => {
    if (!toolCallId) {
      return;
    }
    setItems(
      (current) =>
        current.map((item) =>
          item.type === "user_input_request" && item.tool_call_id === toolCallId
            ? { ...item, ...patch }
            : item,
        ),
      targetThreadId,
    );
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
    replaceMessage,
    removeItem,
    updateToolResult,
    updatePlanReview,
    updatePlanReviewResult,
    updateUserInputRequest,
    updateUserInputRequestResult,
  };
}
