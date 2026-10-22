import { batch, createSignal } from "solid-js";

import { apiFetch } from "@/lib/api";
import type { ThreadSummary, ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { API_PATHS } from "@/lib/endpoints";
import { HISTORY_PAGE_SIZE, NAV_PROJECT_THREAD_LIMIT } from "@/lib/uiConstants";
import { isTempWorkspace } from "@/lib/workspace";

export type NavProjectsPayload = {
  projects: ThreadWorkspaceGroup[];
  has_more: boolean;
  next_cursor: string | null;
};

function uniqueThreads(threads: ThreadSummary[]): ThreadSummary[] {
  return [...new Map(threads.map((thread) => [thread.thread_id, thread])).values()];
}

/** Match the database's binary ordering, including non-BMP directory names. */
function projectKeyBefore(left: string, right: string): boolean {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0)!);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] < rightPoints[index];
  }
  return leftPoints.length < rightPoints.length;
}

/** Keep the active and running chats within the same four-row preview. */
export function navProjectThreads(threads: ThreadSummary[], activeId: string, runningIds: ReadonlySet<string>) {
  const priority = (thread: ThreadSummary) => thread.thread_id === activeId ? 0 : runningIds.has(thread.thread_id) ? 1 : 2;
  const selected = new Set([...threads].sort((left, right) => priority(left) - priority(right))
    .slice(0, NAV_PROJECT_THREAD_LIMIT).map((thread) => thread.thread_id));
  return threads.filter((thread) => selected.has(thread.thread_id));
}

export function newProjectWorkspace(group: ThreadWorkspaceGroup) {
  const workspace = group.threads.find((thread) => thread.workspace?.execution)?.workspace?.execution;
  return group.key === "temp" || isTempWorkspace(workspace) ? null : workspace ?? null;
}

/** Shared across shell mounts so delayed requests have a single state owner. */
export function createNavProjectsStore(fetchProjects = apiFetch<NavProjectsPayload>) {
  const [threads, setThreads] = createSignal<ThreadSummary[]>([]);
  const [loaded, setLoaded] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [hasMore, setHasMore] = createSignal(true);
  let nextCursor: string | null = null;
  let loadedThrough: string | null = null;
  let activeThreadId = "";
  let revision = 0;
  let pendingRefresh = false;
  let request: Promise<void> | null = null;

  const load = (reset = false): Promise<void> => {
    if (reset) {
      revision += 1;
      pendingRefresh = true;
    }
    if (request) return request;
    if (loaded() && !hasMore() && !pendingRefresh) return Promise.resolve();

    setLoading(true);
    setError("");
    request = (async () => {
      do {
        const refresh = pendingRefresh || !loaded();
        pendingRefresh = false;
        const version = revision;
        const boundary = refresh ? loadedThrough : null;
        let cursor = refresh ? null : nextCursor;
        let projects: ThreadWorkspaceGroup[] = [];
        let payload: NavProjectsPayload;
        try {
          do {
            const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
            if (cursor) params.set("after", cursor);
            if (activeThreadId) params.set("active_thread_id", activeThreadId);
            payload = await fetchProjects(`${API_PATHS.navProjects}?${params}`);
            projects = [...projects, ...payload.projects];
            cursor = payload.next_cursor;
          } while (version === revision && refresh && payload.has_more && cursor && boundary && projectKeyBefore(cursor, boundary));

          if (version !== revision) {
            pendingRefresh = true;
            continue;
          }
          const rows = projects.flatMap((project) => project.threads);
          batch(() => {
            setThreads(uniqueThreads(refresh ? rows : [...threads(), ...rows]));
            setHasMore(payload.has_more);
            setLoaded(true);
            setError("");
          });
          nextCursor = cursor;
          loadedThrough = projects.at(-1)?.key ?? (refresh ? null : loadedThrough);
        } catch (err) {
          if (version === revision) setError(err instanceof Error ? err.message : "Failed to load projects");
        }
      } while (pendingRefresh);
    })().finally(() => {
      request = null;
      setLoading(false);
    });
    return request;
  };

  return {
    threads, loaded, loading, error, hasMore, load,
    updateThreads(next: ThreadSummary[]) {
      revision += 1;
      if (request) pendingRefresh = true;
      setThreads(uniqueThreads(next));
    },
    invalidate() {
      revision += 1;
      if (request) pendingRefresh = true;
      setLoaded(false);
    },
    setActiveThreadId(threadId: string) {
      if (activeThreadId === threadId) return;
      activeThreadId = threadId;
      if (loaded() || request) void load(true);
    },
  };
}

export const navProjectsStore = createNavProjectsStore();
