import { Brain, ChevronRight } from "lucide-solid";
import { createEffect, createSignal, createUniqueId, onCleanup, Show } from "solid-js";

import { Markdown } from "@/components/Markdown";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { formatThinkingLabel } from "@/lib/thinkingDurations";

export interface TranscriptThinkingBlockProps {
  text: string;
  durationMs?: number;
  /** True while reasoning deltas still stream into this block. */
  live: boolean;
  threadId?: string | null;
}

/**
 * Collapsible model reasoning. It opens while the model thinks and follows the
 * newest text, then collapses into a short summary once the model moves on.
 * Historical blocks start collapsed, and a manual toggle always wins.
 */
export function TranscriptThinkingBlock(props: TranscriptThinkingBlockProps) {
  // null means "follow the automatic state".
  const [userOpen, setUserOpen] = createSignal<boolean | null>(null);
  const open = () => userOpen() ?? props.live;
  const [visited, setVisited] = createSignal(open());
  const mounted = () => visited() || open();
  const bodyId = createUniqueId();
  let blockRef: HTMLDivElement | undefined;
  let bodyRef: HTMLDivElement | undefined;
  let contentRef: HTMLDivElement | undefined;
  let following = true;
  let previousScrollTop = 0;
  let touchY: number | undefined;
  let pendingScrollFrame: number | undefined;

  const isAtBottom = () => !!bodyRef
    && bodyRef.scrollHeight - bodyRef.clientHeight - bodyRef.scrollTop <= 2;
  const pauseFollowing = () => {
    if (bodyRef && bodyRef.scrollHeight > bodyRef.clientHeight) following = false;
  };
  const scheduleFollow = () => {
    if (!following || !props.live || !open() || pendingScrollFrame !== undefined) return;
    pendingScrollFrame = window.requestAnimationFrame(() => {
      pendingScrollFrame = undefined;
      if (!following || !props.live || !open() || !bodyRef) return;
      bodyRef.scrollTop = bodyRef.scrollHeight;
      previousScrollTop = bodyRef.scrollTop;
    });
  };
  const handleBodyScroll = (event: Event) => {
    event.stopPropagation();
    if (!bodyRef || !open()) return;
    const scrollTop = bodyRef.scrollTop;
    if (scrollTop < previousScrollTop && !isAtBottom()) {
      pauseFollowing();
    } else if (scrollTop > previousScrollTop && isAtBottom()) {
      following = true;
    }
    previousScrollTop = scrollTop;
  };

  createEffect(() => {
    if (open()) setVisited(true);
    if (props.live && open()) scheduleFollow();
  });
  createEffect(() => {
    if (!mounted()) return;
    // Observe the content, since the viewport stops growing at its height cap.
    const observer = new ResizeObserver(scheduleFollow);
    if (bodyRef) observer.observe(bodyRef);
    if (contentRef) observer.observe(contentRef);
    onCleanup(() => {
      observer.disconnect();
      if (pendingScrollFrame !== undefined) window.cancelAnimationFrame(pendingScrollFrame);
    });
  });

  const toggle = () => {
    // Pause transcript following before the layout changes.
    blockRef?.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.TRANSCRIPT_TOOL_TOGGLE, { bubbles: true }),
    );
    setUserOpen(!open());
  };

  const label = () => props.live ? "Thinking…" : formatThinkingLabel(props.durationMs);

  return (
    <div
      class="thinking-block"
      classList={{ "is-live": props.live, "is-open": open() }}
      ref={blockRef}
    >
      <button
        type="button"
        class="thinking-block-summary"
        aria-expanded={open()}
        aria-controls={bodyId}
        title={open() ? "Hide thinking" : "Show thinking"}
        onClick={toggle}
      >
        <ChevronRight size={16} class="thinking-block-chevron" />
        <Brain size={14} class="thinking-block-icon" />
        <span class="thinking-block-label">{label()}</span>
      </button>
      <div
        id={bodyId}
        ref={bodyRef}
        class="thinking-block-body"
        hidden={!open()}
        role="region"
        aria-label="Model thinking"
        tabIndex={0}
        onWheel={(event) => {
          event.stopPropagation();
          if (event.ctrlKey) return;
          if (event.deltaY < 0) pauseFollowing();
          else if (event.deltaY > 0 && isAtBottom()) {
            following = true;
            scheduleFollow();
          }
        }}
        onTouchStart={(event) => { touchY = event.touches[0]?.clientY; }}
        onTouchMove={(event) => {
          event.stopPropagation();
          const nextY = event.touches[0]?.clientY;
          if (touchY !== undefined && nextY !== undefined && nextY > touchY) pauseFollowing();
          touchY = nextY;
        }}
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
            pauseFollowing();
          }
        }}
        onScroll={handleBodyScroll}
      >
        <Show when={mounted()}>
          <div class="thinking-block-content" ref={contentRef}>
            <Markdown
              text={props.text}
              class="message-markdown thinking-markdown"
              deferHighlight={props.live}
              threadId={props.threadId}
            />
          </div>
        </Show>
      </div>
    </div>
  );
}
