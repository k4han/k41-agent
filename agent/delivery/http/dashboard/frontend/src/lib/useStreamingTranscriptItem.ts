import { createSelector, type Accessor } from "solid-js";

import type { ChatTranscriptItem } from "@/lib/chatStreamStore";

export function useStreamingTranscriptItem(
  items: Accessor<ChatTranscriptItem[]>,
  streaming: Accessor<boolean>,
) {
  // Only the trailing assistant message or thinking block can receive deltas.
  // A subsequent tool or user message releases completed Markdown for
  // highlighting and lets a thinking block collapse.
  return createSelector<number | null>(() => {
    if (!streaming()) return null;
    const transcript = items();
    const last = transcript[transcript.length - 1];
    if (last?.type === "thinking") return last.id;
    return last?.type === "message" && last.role === "assistant" ? last.id : null;
  });
}
