import { createSignal } from "solid-js";

export function useChatScroll(
  getTranscriptRef: () => HTMLDivElement | undefined,
) {
  const [autoScroll, setAutoScroll] = createSignal(true);
  const [turnAnchorItemId, setTurnAnchorItemId] = createSignal<number | null>(null);
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = createSignal(0);

  const clearTurnAnchor = () => {
    setTurnAnchorItemId(null);
    setTurnAnchorSpacerHeight(0);
  };

  const getTranscriptItemElement = (id: number) =>
    getTranscriptRef()?.querySelector<HTMLElement>(`[data-transcript-item-id="${id}"]`);

  const getTranscriptItemScrollTop = (target: HTMLElement) => {
    const transcriptRef = getTranscriptRef();
    if (!transcriptRef) {
      return 0;
    }
    const transcriptRect = transcriptRef.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    return targetRect.top - transcriptRect.top + transcriptRef.scrollTop;
  };

  const scrollToTurnAnchor = (id: number) => {
    window.requestAnimationFrame(() => {
      const transcriptRef = getTranscriptRef();
      if (!transcriptRef) {
        return;
      }
      const target = getTranscriptItemElement(id);
      if (!target) {
        return;
      }

      const targetTop = getTranscriptItemScrollTop(target);
      const currentSpacerHeight = turnAnchorSpacerHeight();
      const contentBelowTargetTop =
        transcriptRef.scrollHeight - currentSpacerHeight - targetTop;
      const nextSpacerHeight = Math.max(
        0,
        Math.ceil(transcriptRef.clientHeight - contentBelowTargetTop),
      );

      if (Math.abs(nextSpacerHeight - currentSpacerHeight) > 1) {
        setTurnAnchorSpacerHeight(nextSpacerHeight);
      }

      window.requestAnimationFrame(() => {
        const ref = getTranscriptRef();
        if (!ref) {
          return;
        }
        const updatedTarget = getTranscriptItemElement(id);
        if (!updatedTarget) {
          return;
        }
        ref.scrollTop = getTranscriptItemScrollTop(updatedTarget);
      });
    });
  };

  const scrollToBottom = (force = false) => {
    if (!autoScroll() && !force) {
      return;
    }
    window.setTimeout(() => {
      const anchorId = turnAnchorItemId();
      if (anchorId !== null && !force) {
        scrollToTurnAnchor(anchorId);
        return;
      }
      if (force) {
        clearTurnAnchor();
      }
      const transcriptRef = getTranscriptRef();
      if (transcriptRef) {
        transcriptRef.scrollTop = transcriptRef.scrollHeight;
      }
    }, 0);
  };

  const handleTranscriptScroll = () => {
    const transcriptRef = getTranscriptRef();
    if (!transcriptRef) {
      return;
    }
    const threshold = 50; // px
    const isAtBottom =
      transcriptRef.scrollHeight - transcriptRef.scrollTop - transcriptRef.clientHeight < threshold;
    if (isAtBottom) {
      setAutoScroll(true);
    } else {
      setAutoScroll(false);
    }
  };

  const handleScrollToBottomClick = () => {
    clearTurnAnchor();
    setAutoScroll(true);
    scrollToBottom(true);
  };

  return {
    autoScroll,
    setAutoScroll,
    turnAnchorItemId,
    setTurnAnchorItemId,
    turnAnchorSpacerHeight,
    setTurnAnchorSpacerHeight,
    clearTurnAnchor,
    scrollToTurnAnchor,
    scrollToBottom,
    handleTranscriptScroll,
    handleScrollToBottomClick,
  };
}
