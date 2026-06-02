export const INITIALIZING_ENVIRONMENT_TEXT = "Initializing environment...";
export const THINKING_TEXT = "Thinking...";

const CHAT_STATUS_TEXTS: ReadonlySet<string> = new Set([
  INITIALIZING_ENVIRONMENT_TEXT,
  THINKING_TEXT,
]);

export function isChatStatusText(text: string | undefined | null): boolean {
  if (!text) {
    return false;
  }
  return CHAT_STATUS_TEXTS.has(text);
}

export function stripTrailingEllipsis(text: string): string {
  return text.endsWith("...") ? text.slice(0, -3) : text;
}
