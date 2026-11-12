export const WORKSPACE_IDLE_POLL_MS = 120_000;
export const WORKSPACE_RUNNING_POLL_MS = 60_000;
export const WORKSPACE_REFRESH_DELAY_MS = 300;

export type WorkspaceEventIdentity = {
  threadId?: string;
  thread_id?: string;
  project_id?: number;
  project_workspace_id?: number;
  backend?: string;
  locator?: string;
};

/** Follow loaded tree edges so relative, POSIX and Windows paths behave alike. */
export function visibleWorkspaceDirectories(
  rootEntries: readonly { path: string; kind: string }[],
  entriesByPath: Readonly<Record<string, readonly { path: string; kind: string }[]>>,
  expandedByPath: Readonly<Record<string, boolean>>,
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  const visit = (entries: readonly { path: string; kind: string }[]) => {
    for (const entry of entries) {
      if (entry.kind !== "directory" || !expandedByPath[entry.path] || seen.has(entry.path)) continue;
      seen.add(entry.path);
      result.push(entry.path);
      visit(entriesByPath[entry.path] || []);
    }
  };
  visit(rootEntries);
  return result;
}

export function matchesWorkspaceEvent(detail: WorkspaceEventIdentity | undefined, current: WorkspaceEventIdentity): boolean {
  if (!detail) return true;
  if (detail.project_workspace_id !== undefined) return detail.project_workspace_id === current.project_workspace_id;
  if (detail.backend && detail.locator) return detail.backend === current.backend && detail.locator === current.locator;
  const thread = detail.thread_id || detail.threadId;
  if (thread) return thread === (current.thread_id || current.threadId);
  if (detail.project_id !== undefined) return detail.project_id === current.project_id;
  return true;
}

/** Share a read already in flight; invalidations request at most one follow-up. */
export function createRefreshQueue(run: () => Promise<void>) {
  let running: Promise<void> | undefined;
  let pending = false;
  let disposed = false;
  const refresh = (invalidate = false): Promise<void> => {
    if (disposed) return Promise.resolve();
    if (running) {
      pending ||= invalidate;
      return running;
    }
    running = (async () => {
      do {
        pending = false;
        try { await run(); }
        catch (error) { if (!pending || disposed) throw error; }
      } while (pending && !disposed);
    })().finally(() => {
      running = undefined;
      if (pending && !disposed) return refresh();
    });
    return running;
  };
  return { refresh, dispose: () => { disposed = true; pending = false; } };
}

export function singleFlight<T>(requests: Map<string, Promise<T>>, key: string, run: () => Promise<T>): Promise<T> {
  const current = requests.get(key);
  if (current) return current;
  const request = run().finally(() => { if (requests.get(key) === request) requests.delete(key); });
  requests.set(key, request);
  return request;
}
