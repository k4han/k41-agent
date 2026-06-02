import { stripTrailingEllipsis } from "@/lib/chatStatus";

export function StatusIndicator(props: { text: string }) {
  return (
    <div class="status-indicator" role="status" aria-live="polite">
      <span class="status-indicator-text">{stripTrailingEllipsis(props.text)}</span>
      <span class="status-indicator-dots" aria-hidden="true">
        <span class="status-indicator-dot" />
        <span class="status-indicator-dot" />
        <span class="status-indicator-dot" />
      </span>
    </div>
  );
}
