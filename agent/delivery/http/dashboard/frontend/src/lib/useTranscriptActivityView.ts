import { createMemo, mapArray, untrack, type Accessor } from "solid-js";

import {
  buildTranscriptActivityView,
  isEmptyAssistantMessage,
  isTranscriptActivityBoundary,
  type TranscriptActivitySource,
} from "@/lib/transcriptActivity";

/** Track row classification separately from the text that determines it. */
export function useTranscriptActivityView<T extends TranscriptActivitySource>(
  items: Accessor<T[]>,
  streaming: Accessor<boolean>,
) {
  const rows = mapArray(items, (item) => ({
    item,
    kind: createMemo(() => isEmptyAssistantMessage(item)
      ? "hidden"
      : isTranscriptActivityBoundary(item) ? "item" : "activity"),
  }));
  const visibleItems = createMemo(() => rows()
    .filter((row) => row.kind() !== "hidden")
    .map((row) => row.item));

  return createMemo(() => {
    const visible = visibleItems();
    const active = streaming();
    // The per-row memos above already track all structural dependencies,
    // including empty-to-visible transitions and rich-content boundaries.
    return untrack(() => buildTranscriptActivityView(visible, active));
  });
}
