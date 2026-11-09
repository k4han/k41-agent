/**
 * Thinking durations are measured while a response streams, but the saved
 * history only keeps the reasoning text. This in-memory cache lets a thread
 * reload right after its run keep the "Thought for Ns" label.
 */
const MAX_ENTRIES = 200;
const durations = new Map<string, number>();

const cacheKey = (threadId: string, text: string) => `${threadId}\u001f${text.trim()}`;

export function rememberThinkingDuration(
  threadId: string | null | undefined,
  text: string,
  durationMs: number,
): void {
  if (!threadId || !text.trim() || !Number.isFinite(durationMs) || durationMs < 0) return;
  const key = cacheKey(threadId, text);
  durations.delete(key);
  durations.set(key, durationMs);
  while (durations.size > MAX_ENTRIES) {
    const oldest = durations.keys().next().value;
    if (oldest === undefined) break;
    durations.delete(oldest);
  }
}

export function thinkingDurationFor(
  threadId: string | null | undefined,
  text: string,
): number | undefined {
  if (!threadId || !text.trim()) return undefined;
  return durations.get(cacheKey(threadId, text));
}

/** Short label for a finished thinking block. Sub-second runs omit the time. */
export function formatThinkingLabel(durationMs: number | undefined): string {
  if (durationMs === undefined || durationMs < 1000) return "Thought";
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `Thought for ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `Thought for ${minutes}m ${rest}s` : `Thought for ${minutes}m`;
}
