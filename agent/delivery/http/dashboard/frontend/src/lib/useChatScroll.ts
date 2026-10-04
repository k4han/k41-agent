import { createSignal, onCleanup } from "solid-js";

export function useChatScroll(
  getTranscriptRef: () => HTMLDivElement | undefined,
) {
  const [autoScroll, setAutoScroll] = createSignal(true);
  const [turnAnchorItemId, setTurnAnchorItemId] = createSignal<number | null>(null);
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = createSignal(0);
  let pendingScrollTimer: number | undefined;
  let pendingAnchorFrame: number | undefined;
  let pausedScrollTop: number | undefined;

  const cancelScheduledScroll = () => {
    if (pendingScrollTimer !== undefined) {
      window.clearTimeout(pendingScrollTimer);
      pendingScrollTimer = undefined;
    }
    if (pendingAnchorFrame !== undefined) {
      window.cancelAnimationFrame(pendingAnchorFrame);
      pendingAnchorFrame = undefined;
    }
  };

  onCleanup(cancelScheduledScroll);

  const clearTurnAnchor = () => {
    cancelScheduledScroll();
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
    cancelScheduledScroll();
    pendingAnchorFrame = window.requestAnimationFrame(() => {
      pendingAnchorFrame = undefined;
      if (!autoScroll() || turnAnchorItemId() !== id) {
        return;
      }
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

      pendingAnchorFrame = window.requestAnimationFrame(() => {
        pendingAnchorFrame = undefined;
        if (!autoScroll() || turnAnchorItemId() !== id) {
          return;
        }
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
    cancelScheduledScroll();
    pendingScrollTimer = window.setTimeout(() => {
      pendingScrollTimer = undefined;
      if (!autoScroll() && !force) {
        return;
      }
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
    // Removing the spacer can clamp scrollTop and emit a layout-driven scroll.
    // That event must not resume following immediately after opening a tool.
    if (pausedScrollTop === transcriptRef.scrollTop) {
      pausedScrollTop = undefined;
      return;
    }
    pausedScrollTop = undefined;
    const threshold = 50; // px
    const isAtBottom =
      transcriptRef.scrollHeight - transcriptRef.scrollTop - transcriptRef.clientHeight < threshold;
    if (isAtBottom) {
      if (!autoScroll()) {
        clearTurnAnchor();
      }
      setAutoScroll(true);
    } else {
      setAutoScroll(false);
      cancelScheduledScroll();
    }
  };

  const handleScrollToBottomClick = () => {
    pausedScrollTop = undefined;
    clearTurnAnchor();
    setAutoScroll(true);
    scrollToBottom(true);
  };

  // Stop following the stream and release the turn anchor so subsequent
  // streamed chunks do not yank the view back to the anchor. Used when the
  // user explicitly interacts with the transcript (for example expanding a
  // tool call to read its arguments/result). Scrolling back to the bottom
  // re-enables auto-scroll; the "scroll to bottom" button force-scrolls.
  const pauseAutoScroll = () => {
    setAutoScroll(false);
    clearTurnAnchor();
    pausedScrollTop = getTranscriptRef()?.scrollTop;
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
    pauseAutoScroll,
  };
}
