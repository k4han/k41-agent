import { batch, createComputed, createSignal, on, type Accessor } from "solid-js";
import { createStore, reconcile, unwrap, type SetStoreFunction } from "solid-js/store";

import { groupThreadsByWorkspace, type ThreadSummary, type ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { cloneValue } from "@/lib/utils";

type WorkspaceGroup = ThreadWorkspaceGroup & { isRepo: boolean };

/** Preserve group and thread identities across refreshed or reordered snapshots. */
export function useThreadWorkspaceGroups(threads: Accessor<ThreadSummary[]>) {
  const [groups, setGroups] = createSignal<WorkspaceGroup[]>([]);
  const entries = new Map<string, [WorkspaceGroup, SetStoreFunction<WorkspaceGroup>]>();

  createComputed(on(threads, (rows) => {
    batch(() => {
      const snapshots = groupThreadsByWorkspace(rows);
      const next = snapshots.map((snapshot) => {
        let entry = entries.get(snapshot.key);
        if (!entry) {
          entry = createStore<WorkspaceGroup>({ ...snapshot, threads: [], isRepo: false });
          entries.set(snapshot.key, entry);
        }
        const [group, setGroup] = entry;
        setGroup({
          label: snapshot.label,
          isRepo: snapshot.threads.some((thread) => thread.workspace?.scope.kind === "github"),
        });
        setGroup("threads", reconcile(cloneValue(unwrap(snapshot.threads)), { key: "thread_id" }));
        return group;
      });
      const keys = new Set(snapshots.map((snapshot) => snapshot.key));
      for (const key of entries.keys()) {
        if (!keys.has(key)) entries.delete(key);
      }
      setGroups(next);
    });
  }));

  return groups;
}
