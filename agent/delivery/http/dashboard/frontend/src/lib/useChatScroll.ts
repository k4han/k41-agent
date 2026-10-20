import { createSignal, onCleanup } from "solid-js";

export function useChatScroll(
  getTranscriptRef: () => HTMLDivElement | undefined,
) {
  const [autoScroll, setAutoScroll] = createSignal(true);
  const [showScrollToBottom, setShowScrollToBottom] = createSignal(false);
  const [turnAnchorItemId, setTurnAnchorItemId] = createSignal<number | null>(null);
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = createSignal(0);
  let pendingScrollFrame: number | undefined;
  let pendingScrollForce = false;
  let pendingScrollAnchorId: number | null = null;
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
    resizeObserver = new ResizeObserver(() => {
      // Tool bodies and rich content can finish laying out after the stream
      // callback, so keep following without waiting for another text delta.
      scrollToBottom();
    });
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
    if (pendingScrollFrame !== undefined) {
      window.cancelAnimationFrame(pendingScrollFrame);
      pendingScrollFrame = undefined;
    }
    pendingScrollForce = false;
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

  const scheduleScroll = (anchorId: number | null, force = false) => {
    scheduleVisibilityUpdate();
    if (!autoScroll() && !force) {
      return;
    }
    // Coalesce stream updates without losing an explicit bottom request.
    pendingScrollForce ||= force;
    pendingScrollAnchorId = anchorId;
    if (pendingScrollFrame !== undefined) {
      return;
    }
    pendingScrollFrame = window.requestAnimationFrame(() => {
      pendingScrollFrame = undefined;
      const forceBottom = pendingScrollForce;
      const id = pendingScrollAnchorId;
      pendingScrollForce = false;
      if (!autoScroll() && !forceBottom) {
        return;
      }
      const transcriptRef = getTranscriptRef();
      if (!transcriptRef) {
        return;
      }
      if (forceBottom || id === null) {
        if (forceBottom) clearTurnAnchor();
        transcriptRef.scrollTop = transcriptRef.scrollHeight;
        updateScrollToBottomVisibility();
        return;
      }
      if (turnAnchorItemId() !== id) {
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

      // Apply the spacer and scroll before the same paint. Splitting these
      // writes across frames exposes the browser's intermediate clamped top.
      transcriptRef.scrollTop = getTranscriptItemScrollTop(target);
      updateScrollToBottomVisibility();
    });
  };

  const scrollToTurnAnchor = (id: number) => {
    cancelScheduledScroll();
    scheduleScroll(id);
  };

  const scrollToBottom = (force = false) => scheduleScroll(turnAnchorItemId(), force);

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
    // Wheel, touch, keyboard and scrollbar interactions pause following first.
    // A delayed event from our own scroll or a layout change must not cancel
    // the next stream update just because the content has since grown.
    if (autoScroll()) {
      return;
    }
    const threshold = 50; // px
    const spacerHeight = turnAnchorSpacerHeight();
    const isAtBottom =
      transcriptRef.scrollHeight - spacerHeight - transcriptRef.scrollTop - transcriptRef.clientHeight < threshold
      && (spacerHeight === 0 || scrollingDown);
    if (isAtBottom) {
      clearTurnAnchor();
      setAutoScroll(true);
    } else {
      cancelScheduledScroll();
    }
  };

  const handleScrollToBottomClick = () => {
    setShowScrollToBottom(false);
    pausedScrollTop = undefined;
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

  const handleUserScroll = (direction?: number) => {
    if (direction === 0) return;
    const ref = getTranscriptRef();
    // At the bottom, scrolling down may not emit a scroll event. Resume here
    // so this interaction cannot leave the next streamed reply unfollowed.
    if (direction !== undefined && direction > 0 && ref
      && ref.scrollHeight - turnAnchorSpacerHeight() - ref.scrollTop - ref.clientHeight < 50) {
      handleScrollToBottomClick();
      return;
    }
    pauseAutoScroll();
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
    handleUserScroll,
    handleScrollToBottomClick,
    pauseAutoScroll,
  };
}
