import { query } from "@solidjs/router";

const keys = new Set<string>();

export function trackDashboardCacheKey(key: string): void {
  keys.add(key);
}

/** A successful write must not leave preloaded pages with stale server data. */
export function invalidateDashboardCache(): void {
  for (const key of keys) query.delete(key);
  keys.clear();
}
