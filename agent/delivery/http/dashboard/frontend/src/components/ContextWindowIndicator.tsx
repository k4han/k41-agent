import { useToast } from "@/components/Toast";

export interface ContextWindowData {
  maxTokens: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  totalPercent: number;
  reservedPercent: number;
  systemPercent: string;
  toolPercent: string;
  messagesPercent: string;
  filePercent: string;
  formattedUsed: string;
  formattedMax: string;
}

export interface ContextWindowIndicatorProps {
  data: ContextWindowData;
  onCompactClick?: () => void;
}

export function ContextWindowIndicator(props: ContextWindowIndicatorProps) {
  const { showToast } = useToast();

  const handleCompactClick = (e: MouseEvent) => {
    e.preventDefault();
    if (props.onCompactClick) {
      props.onCompactClick();
    } else {
      showToast("Tính năng Compact Conversation đang được phát triển!", "warning");
    }
  };

  return (
    <div class="context-window-wrapper">
      <button
        class="context-window-circle-btn"
        type="button"
        title="Xem chi tiết Context Window"
        aria-label="Xem chi tiết Context Window"
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
          <span class="cw-tokens-value">
            {props.data.formattedUsed} / {props.data.formattedMax} tokens
          </span>
          <span class="cw-tokens-percent">
            {Math.round(props.data.totalPercent)}%
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

        <div class="cw-section-title">System</div>
        <div class="cw-row">
          <span class="cw-label">System Instructions</span>
          <span class="cw-value">{props.data.systemPercent}</span>
        </div>
        <div class="cw-row">
          <span class="cw-label">Tool Definitions</span>
          <span class="cw-value">{props.data.toolPercent}</span>
        </div>

        <div class="cw-section-title">User Context</div>
        <div class="cw-row">
          <span class="cw-label">Messages</span>
          <span class="cw-value">{props.data.messagesPercent}</span>
        </div>
        <div class="cw-row">
          <span class="cw-label">Files</span>
          <span class="cw-value">{props.data.filePercent}</span>
        </div>

        <button 
          class="cw-compact-btn" 
          type="button" 
          onClick={handleCompactClick}
        >
          Compact Conversation
        </button>
      </div>
    </div>
  );
}
