import { createSignal, onCleanup } from "solid-js";

export function useChatScroll(
  getTranscriptRef: () => HTMLDivElement | undefined,
) {
  const [autoScroll, setAutoScroll] = createSignal(true);
  const [showScrollToBottom, setShowScrollToBottom] = createSignal(false);
  const [turnAnchorItemId, setTurnAnchorItemId] = createSignal<number | null>(null);
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = createSignal(0);
  let pendingScrollTimer: number | undefined;
  let pendingAnchorFrame: number | undefined;
  let pendingVisibilityFrame: number | undefined;
  let resizeObserver: ResizeObserver | undefined;
  let mutationObserver: MutationObserver | undefined;
  let pausedScrollTop: number | undefined;
  let previousScrollTop: number | undefined;

  const updateScrollToBottomVisibility = () => {
    const ref = getTranscriptRef();
    // The turn spacer is blank space, not unread conversation content.
    const distanceToBottom = ref
      ? ref.scrollHeight - turnAnchorSpacerHeight() - ref.scrollTop - ref.clientHeight
      : 0;
    setShowScrollToBottom(!!ref && ref.clientHeight > 0 && distanceToBottom > 200);
  };

  const scheduleVisibilityUpdate = () => {
    if (pendingVisibilityFrame !== undefined) {
      return;
    }
    pendingVisibilityFrame = window.requestAnimationFrame(() => {
      pendingVisibilityFrame = undefined;
      updateScrollToBottomVisibility();
    });
  };

  const observeTranscript = (ref: HTMLDivElement) => {
    resizeObserver?.disconnect();
    mutationObserver?.disconnect();
    resizeObserver = new ResizeObserver(scheduleVisibilityUpdate);
    resizeObserver.observe(ref);
    // The viewport stays the same size when a tool opens or a message grows.
    // Observe its rows as well so layout changes refresh the scroll controls.
    for (const child of ref.children) {
      resizeObserver.observe(child);
    }
    mutationObserver = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.removedNodes) {
          if (node.nodeType === 1) {
            resizeObserver?.unobserve(node as Element);
          }
        }
        for (const node of record.addedNodes) {
          if (node.nodeType === 1) {
            resizeObserver?.observe(node as Element);
          }
        }
      }
      scheduleVisibilityUpdate();
    });
    mutationObserver.observe(ref, { childList: true });
    scheduleVisibilityUpdate();
  };

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

  onCleanup(() => {
    cancelScheduledScroll();
    resizeObserver?.disconnect();
    mutationObserver?.disconnect();
    if (pendingVisibilityFrame !== undefined) {
      window.cancelAnimationFrame(pendingVisibilityFrame);
    }
  });

  const clearTurnAnchor = () => {
    cancelScheduledScroll();
    setTurnAnchorItemId(null);
    setTurnAnchorSpacerHeight(0);
    scheduleVisibilityUpdate();
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
        updateScrollToBottomVisibility();
      });
    });
  };

  const scrollToBottom = (force = false) => {
    scheduleVisibilityUpdate();
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
      updateScrollToBottomVisibility();
    }, 0);
  };

  const handleTranscriptScroll = () => {
    updateScrollToBottomVisibility();
    const transcriptRef = getTranscriptRef();
    if (!transcriptRef) {
      return;
    }
    const scrollingDown =
      previousScrollTop !== undefined && transcriptRef.scrollTop > previousScrollTop;
    previousScrollTop = transcriptRef.scrollTop;
    // Layout-driven scroll events must not resume following after opening a tool.
    if (pausedScrollTop === transcriptRef.scrollTop) {
      pausedScrollTop = undefined;
      return;
    }
    pausedScrollTop = undefined;
    const threshold = 50; // px
    const spacerHeight = turnAnchorSpacerHeight();
    const isAtBottom =
      transcriptRef.scrollHeight - spacerHeight - transcriptRef.scrollTop - transcriptRef.clientHeight < threshold
      && (autoScroll() || spacerHeight === 0 || scrollingDown);
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
    setShowScrollToBottom(false);
    pausedScrollTop = undefined;
    clearTurnAnchor();
    setAutoScroll(true);
    scrollToBottom(true);
  };

  // Release the anchor without shrinking the scroll range during an interaction.
  // Removing its spacer here can cancel out an expanded tool's height, clamp
  // scrollTop, and change the scrollbar range while the user is dragging it.
  // Reaching the content bottom or using the bottom button clears the spacer.
  const pauseAutoScroll = () => {
    setAutoScroll(false);
    cancelScheduledScroll();
    setTurnAnchorItemId(null);
    pausedScrollTop = getTranscriptRef()?.scrollTop;
    previousScrollTop = pausedScrollTop;
    scheduleVisibilityUpdate();
  };

  return {
    autoScroll,
    showScrollToBottom,
    observeTranscript,
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
