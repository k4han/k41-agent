import { createSelector, type Accessor } from "solid-js";

import type { ChatTranscriptItem } from "@/lib/chatStreamStore";

export function useStreamingTranscriptItem(
  items: Accessor<ChatTranscriptItem[]>,
  streaming: Accessor<boolean>,
) {
  // Only the trailing assistant message can receive text deltas. A subsequent
  // tool or user message releases completed Markdown for highlighting.
  return createSelector<number | null>(() => {
    if (!streaming()) return null;
    const transcript = items();
    const last = transcript[transcript.length - 1];
    return last?.type === "message" && last.role === "assistant" ? last.id : null;
  });
}
