import { batch, createSignal } from "solid-js";

import { apiFetch } from "@/lib/api";
import { threadWorkspaceKey } from "@/lib/chatThreads";
import type { ThreadSummary, ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { API_PATHS } from "@/lib/endpoints";
import { HISTORY_PAGE_SIZE, NAV_PROJECT_THREAD_LIMIT } from "@/lib/uiConstants";
import { isTempWorkspace } from "@/lib/workspace";

export type NavProjectsPayload = {
  projects: ThreadWorkspaceGroup[];
  has_more: boolean;
  next_cursor: string | null;
};

type ProjectThreadsPayload = {
  threads: ThreadSummary[];
  has_more: boolean;
  next_offset: number;
};

type ProjectThreadsState = {
  expanded: boolean;
  loading: boolean;
  error: string;
  hasMore: boolean;
  nextOffset: number;
  threadIds: string[];
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

/** Keep the selected chat visible and show running chats first, including expanded lists. */
export function navProjectThreads(
  threads: ThreadSummary[], activeId: string, runningIds: ReadonlySet<string>, expanded = false,
) {
  const priority = (thread: ThreadSummary) => thread.thread_id === activeId ? 0 : runningIds.has(thread.thread_id) ? 1 : 2;
  const selected = new Set([...threads].sort((left, right) => priority(left) - priority(right))
    .slice(0, NAV_PROJECT_THREAD_LIMIT).map((thread) => thread.thread_id));
  return threads.filter((thread) => expanded || selected.has(thread.thread_id))
    .sort((left, right) => Number(runningIds.has(right.thread_id)) - Number(runningIds.has(left.thread_id)));
}

export function newProjectWorkspace(group: ThreadWorkspaceGroup) {
  const workspace = group.workspace?.execution ?? group.threads.find((thread) => thread.workspace?.execution)?.workspace?.execution;
  return group.key === "temp" || isTempWorkspace(workspace) ? null : workspace ?? null;
}

/** Shared across shell mounts so delayed requests have a single state owner. */
export function createNavProjectsStore(
  fetchProjects = apiFetch<NavProjectsPayload>,
  fetchThreads = apiFetch<ProjectThreadsPayload>,
) {
  const [threads, setThreads] = createSignal<ThreadSummary[]>([]);
  const [groups, setGroups] = createSignal<ThreadWorkspaceGroup[]>([]);
  const [loaded, setLoaded] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [hasMore, setHasMore] = createSignal(true);
  const [projectStates, setProjectStates] = createSignal<Record<string, ProjectThreadsState>>({});
  const projectRequests = new Map<string, Promise<void>>();
  let nextCursor: string | null = null;
  let loadedThrough: ThreadWorkspaceGroup | null = null;
  let activeThreadId = "";
  let revision = 0;
  let catalogRevision = 0;
  let pendingRefresh = false;
  let request: Promise<void> | null = null;

  const syncThreads = (next: ThreadSummary[]) => {
    const rows = uniqueThreads(next);
    batch(() => {
      setThreads(rows);
      setGroups((current) => current.map((group) => ({
        ...group, threads: rows.filter((thread) => threadWorkspaceKey(thread) === group.key),
      })));
    });
  };
  const groupBefore = (left: ThreadWorkspaceGroup, right: ThreadWorkspaceGroup) =>
    Boolean(left.is_pinned) !== Boolean(right.is_pinned)
      ? Boolean(left.is_pinned)
      : projectKeyBefore(left.key, right.key);

  const setProjectState = (key: string, state: ProjectThreadsState) => {
    setProjectStates((current) => ({ ...current, [key]: state }));
  };
  const readProjectThreads = (key: string, offset: number, limit = HISTORY_PAGE_SIZE) => {
    const params = new URLSearchParams({ project_key: key, limit: String(limit), offset: String(offset) });
    return fetchThreads(`${API_PATHS.navProjectThreads}?${params}`);
  };

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
          } while (version === revision && refresh && payload.has_more && cursor && boundary
            && projects.length > 0 && groupBefore(projects.at(-1)!, boundary));

          const expandedStates = new Map<string, ProjectThreadsState>();
          if (refresh) {
            await Promise.all(projects.map(async (project) => {
              const state = projectStates()[project.key];
              if (!state?.expanded) return;
              const rows: ThreadSummary[] = [];
              let page: ProjectThreadsPayload;
              let offset = 0;
              do {
                page = await readProjectThreads(project.key, offset, Math.min(100, Math.max(1, state.nextOffset - offset)));
                rows.push(...page.threads);
                offset = page.next_offset;
              } while (version === revision && page.has_more && offset < state.nextOffset);
              project.threads = uniqueThreads([...rows, ...project.threads]);
              expandedStates.set(project.key, {
                ...state, hasMore: page.has_more, nextOffset: offset,
                threadIds: rows.map((thread) => thread.thread_id),
              });
            }));
          }

          if (version !== revision) {
            pendingRefresh = true;
            continue;
          }
          const rows = projects.flatMap((project) => project.threads);
          batch(() => {
            setGroups((current) => [...new Map((refresh ? projects : [...current, ...projects])
              .map((project) => [project.key, project])).values()]);
            syncThreads(refresh ? rows : [...threads(), ...rows]);
            if (refresh) {
              const keys = new Set(projects.map((project) => project.key));
              setProjectStates((current) => Object.fromEntries(Object.entries(current)
                .filter(([key]) => keys.has(key))
                .map(([key, state]) => [key, expandedStates.has(key)
                  ? { ...expandedStates.get(key)!, loading: state.loading, error: state.error }
                  : state])));
            }
            setHasMore(payload.has_more);
            setLoaded(true);
            setError("");
          });
          nextCursor = cursor;
          loadedThrough = projects.at(-1) ?? (refresh ? null : loadedThrough);
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

  const loadProjectThreads = (key: string): Promise<void> => {
    const pending = projectRequests.get(key);
    if (pending) return pending;
    const initial = projectStates()[key] ?? {
      expanded: false, loading: false, error: "", hasMore: true, nextOffset: 0, threadIds: [],
    };
    if (!initial.hasMore) return Promise.resolve();
    setProjectState(key, { ...initial, loading: true, error: "" });
    const catalogVersion = catalogRevision;
    const pendingRequest = (async () => {
      try {
        while (true) {
          const version = revision;
          const state = projectStates()[key] ?? initial;
          const page = await readProjectThreads(key, state.nextOffset);
          if (catalogVersion !== catalogRevision) return;
          if (version !== revision) continue;
          const current = threads();
          const previous = current.filter((thread) => threadWorkspaceKey(thread) === key);
          const byId = new Map([...previous, ...page.threads].map((thread) => [thread.thread_id, thread]));
          const ids = [...new Set([...state.threadIds, ...page.threads.map((thread) => thread.thread_id)])];
          const rows = uniqueThreads([
            ...ids.flatMap((id) => byId.has(id) ? [byId.get(id)!] : []),
            ...previous.map((thread) => byId.get(thread.thread_id)!),
          ]);
          let inserted = false;
          const next = current.flatMap((thread) => {
            if (threadWorkspaceKey(thread) !== key) return [thread];
            if (inserted) return [];
            inserted = true;
            return rows;
          });
          revision += 1;
          if (request) pendingRefresh = true;
          batch(() => {
            syncThreads(inserted ? next : [...next, ...rows]);
            setProjectState(key, {
              expanded: true, loading: false, error: "", hasMore: page.has_more,
              nextOffset: page.next_offset, threadIds: ids,
            });
          });
          return;
        }
      } catch (err) {
        if (catalogVersion !== catalogRevision) return;
        setProjectState(key, {
          ...(projectStates()[key] ?? initial), loading: false,
          error: err instanceof Error ? err.message : "Failed to load conversations",
        });
      }
    })().finally(() => {
      projectRequests.delete(key);
      const state = projectStates()[key];
      if (state?.loading) setProjectState(key, { ...state, loading: false });
    });
    projectRequests.set(key, pendingRequest);
    return pendingRequest;
  };

  return {
    threads, groups, loaded, loading, error, hasMore, load,
    refreshProjects() {
      catalogRevision += 1;
      loadedThrough = null;
      return load(true);
    },
    projectState: (key: string) => projectStates()[key],
    loadProjectThreads,
    updateThreads(next: ThreadSummary[]) {
      revision += 1;
      if (request) pendingRefresh = true;
      syncThreads(next);
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
