import { createComputed, createMemo, createSignal, on, type Accessor } from "solid-js";

import type { ThreadSummary, ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { HISTORY_PAGE_SIZE } from "@/lib/uiConstants";

/** Bound rendered history rows while preserving workspace and thread identities. */
export function useHistoryPagination<T extends ThreadWorkspaceGroup>(
  groups: Accessor<T[]>,
  workspaceKey: Accessor<string>,
) {
  const [pageIndex, setPageIndex] = createSignal(0);
  const total = createMemo(() => groups().reduce((count, group) => count + group.threads.length, 0));
  const pageCount = createMemo(() => Math.max(1, Math.ceil(total() / HISTORY_PAGE_SIZE)));
  createComputed(on(workspaceKey, () => setPageIndex(0)));
  createComputed(() => {
    const lastPage = pageCount() - 1;
    setPageIndex((current) => Math.min(current, lastPage));
  });
  const pageRows = createMemo(() => {
    const start = pageIndex() * HISTORY_PAGE_SIZE;
    const end = start + HISTORY_PAGE_SIZE;
    const rows = new Map<T, ThreadSummary[]>();
    let offset = 0;
    for (const group of groups()) {
      const nextOffset = offset + group.threads.length;
      if (nextOffset > start && offset < end) {
        rows.set(group, group.threads.slice(Math.max(0, start - offset), end - offset));
      }
      offset = nextOffset;
      if (offset >= end) break;
    }
    return rows;
  });
  const visibleGroups = createMemo(() => [...pageRows().keys()]);
  return {
    total, pageIndex, pageCount, visibleGroups,
    threads: (group: T) => pageRows().get(group) ?? [],
    previous: () => setPageIndex((current) => Math.max(0, current - 1)),
    next: () => setPageIndex((current) => Math.min(pageCount() - 1, current + 1)),
  };
}
