import { query } from "@solidjs/router";
import { createResource, createSignal, onCleanup, type Accessor } from "solid-js";

import { apiFetch } from "@/lib/api";
import { trackDashboardCacheKey } from "@/lib/dashboardCache";

export const readDashboardData = query(
  (path: string) => {
    trackDashboardCacheKey(readDashboardData.keyFor(path));
    return apiFetch<unknown>(path);
  },
  "dashboard-data",
);

export function preloadDashboardData(...paths: string[]): void {
  for (const path of paths) {
    void Promise.resolve(readDashboardData(path)).catch(() => undefined);
  }
}

/** Cache initial reads and keep existing content visible during explicit refreshes. */
export function useDashboardData<T>(
  path: string | Accessor<string | false>,
  options: { defer?: boolean } = {},
) {
  const [enabled, setEnabled] = createSignal(!options.defer);
  const [error, setError] = createSignal("");
  let requestId = 0;
  let disposed = false;
  let inFlight: Promise<T | undefined> | undefined;
  const currentPath = () => typeof path === "function" ? path() : path;
  const [resource, { refetch, mutate }] = createResource<T | undefined, string>(
    () => enabled() && currentPath(),
    (requestPath, { value }) => {
      const id = ++requestId;
      setError("");
      const request = Promise.resolve(readDashboardData(requestPath))
        .then((payload) => {
          if (disposed || id !== requestId) {
            return value;
          }
          return payload as T;
        })
        .catch((failure: unknown) => {
          if (!disposed && id === requestId) {
            setError(failure instanceof Error ? failure.message : "Failed to load data");
          }
          return value;
        })
        .finally(() => {
          if (id === requestId) inFlight = undefined;
        });
      inFlight = request;
      return request;
    },
  );
  const load = async (): Promise<void> => {
    if (disposed) return;
    if (inFlight) {
      await inFlight;
      return;
    }
    if (!enabled()) {
      setEnabled(true);
      await inFlight;
      return;
    }
    const requestPath = currentPath();
    if (!requestPath) return;
    query.delete(readDashboardData.keyFor(requestPath));
    const pending = refetch();
    inFlight = pending as unknown as Promise<T | undefined>;
    await pending;
  };
  onCleanup(() => {
    disposed = true;
    requestId += 1;
  });
  return { data: () => resource.latest, error, load, setData: mutate, loading: () => resource.loading };
}
