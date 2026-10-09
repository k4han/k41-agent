import { For, createSignal, onCleanup, onMount } from "solid-js";
import { useToast } from "@/components/Toast";
import type { ContextCategory } from "@/types";

export interface ContextWindowCategory {
  key: ContextCategory;
  label: string;
  tokens: number | null;
  formattedValue: string;
}

export interface ContextWindowData {
  maxTokens: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  totalPercent: number;
  reservedPercent: number;
  categories: ContextWindowCategory[];
  formattedUsed: string;
  formattedMax: string;
  hasReportedUsage: boolean;
  estimated: boolean;
}

export interface ContextWindowIndicatorProps {
  data: ContextWindowData;
  onCompactClick?: () => void;
  compacting?: boolean;
}

export function ContextWindowIndicator(props: ContextWindowIndicatorProps) {
  const { showToast } = useToast();
  const [isOpen, setIsOpen] = createSignal(false);
  let containerRef: HTMLDivElement | undefined;

  const handleToggle = (e: MouseEvent) => {
    e.stopPropagation();
    setIsOpen(!isOpen());
  };

  const handleDocumentClick = (e: MouseEvent) => {
    if (isOpen() && containerRef && !containerRef.contains(e.target as Node)) {
      setIsOpen(false);
    }
  };

  onMount(() => {
    document.addEventListener("click", handleDocumentClick);
  });

  onCleanup(() => {
    document.removeEventListener("click", handleDocumentClick);
  });

  const handleCompactClick = (e: MouseEvent) => {
    e.preventDefault();
    if (props.compacting) {
      return;
    }
    if (props.onCompactClick) {
      props.onCompactClick();
    } else {
      showToast("Compact Conversation feature is under development!", "warning");
    }
  };

  return (
    <div ref={containerRef} class={`context-window-wrapper ${isOpen() ? "is-open" : ""}`}>
      <button
        class="context-window-circle-btn"
        type="button"
        onClick={handleToggle}
        title="View Context Window details"
        aria-label="View Context Window details"
      >
        <svg width="18" height="18" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255, 255, 255, 0.15)" stroke-width="2.5" />
          <circle
            cx="10"
            cy="10"
            r="8"
            fill="none"
            stroke={(() => {
              const p = props.data.totalPercent;
              if (p >= 80) return "var(--danger)";
              if (p >= 50) return "var(--warning)";
              return "var(--info)";
            })()}
            stroke-width="2.5"
            stroke-dasharray="50.26"
            stroke-dashoffset={50.26 - (50.26 * Math.min(100, props.data.totalPercent)) / 100}
            stroke-linecap="round"
            transform="rotate(-90 10 10)"
            style="transition: stroke-dashoffset 0.3s ease, stroke 0.3s ease;"
          />
        </svg>
      </button>

      <div class="context-window-popover">
        <div class="cw-title">Context Window</div>
        
        <div class="cw-tokens-row">
          <span class="cw-tokens-value" title={props.data.estimated
            ? "Estimated retained history after compaction plus the latest system, tools, skills and subagent prompt estimates."
            : "Input and response tokens reported by the provider for the last completed model call"}>
            {props.data.formattedUsed} / {props.data.formattedMax} tokens
          </span>
          <span class="cw-tokens-percent">
            {props.data.hasReportedUsage || props.data.estimated
              ? `${props.data.estimated ? "~" : ""}${Math.round(props.data.totalPercent)}%` : "—"}
          </span>
        </div>

        <div class="cw-progress-container">
          <div class="cw-progress-used" style={{ width: `${Math.min(100, props.data.totalPercent)}%` }} />
          <div class="cw-progress-reserved" style={{ width: `${Math.min(100 - props.data.totalPercent, props.data.reservedPercent)}%` }} />
        </div>

        <div class="cw-legend">
          <div class="cw-legend-stripe" />
          <span>Reserved for response</span>
        </div>

        <div class="cw-section-title" title={props.data.estimated
          ? "Retained messages are recounted after compaction. System, tools, skills and subagent prompt estimates come from the latest available context. Percentages use the model context window capacity."
          : "Estimated shares of the last model input and its response, scaled to provider-reported tokens. Percentages use the model context window capacity."}>
          Context breakdown (Estimated)
        </div>
        <For each={props.data.categories}>
          {(category) => (
            <div class="cw-row">
              <span class="cw-label">{category.label}</span>
              <span class="cw-value">{category.formattedValue}</span>
            </div>
          )}
        </For>

        <button 
          class="cw-compact-btn" 
          type="button" 
          disabled={props.compacting}
          onClick={handleCompactClick}
        >
          {props.compacting ? "Compacting..." : "Compact Conversation"}
        </button>
      </div>
    </div>
  );
}
