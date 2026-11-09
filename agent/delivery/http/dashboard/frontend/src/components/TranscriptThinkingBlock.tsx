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
  let pendingScrollFrame: number | undefined;

  const isAtBottom = () => !!bodyRef
    && bodyRef.scrollHeight - bodyRef.clientHeight - bodyRef.scrollTop <= 2;
  const scheduleFollow = () => {
    if (!following || !props.live || !open() || pendingScrollFrame !== undefined) return;
    pendingScrollFrame = window.requestAnimationFrame(() => {
      pendingScrollFrame = undefined;
      if (!following || !props.live || !open() || !bodyRef) return;
      bodyRef.scrollTop = bodyRef.scrollHeight;
    });
  };

  createEffect(() => {
    if (open()) setVisited(true);
    if (props.live && open()) scheduleFollow();
  });
  createEffect(() => {
    if (!mounted()) return;
    // Observe the content, since the viewport stops growing at its height cap.
    const observer = new ResizeObserver(scheduleFollow);
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
          if (event.deltaY < 0) following = false;
          else if (event.deltaY > 0 && isAtBottom()) {
            following = true;
            scheduleFollow();
          }
        }}
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        onScroll={(event) => event.stopPropagation()}
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
