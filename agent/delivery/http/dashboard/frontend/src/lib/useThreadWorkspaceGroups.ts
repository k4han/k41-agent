import { batch, createComputed, createSignal, on, type Accessor } from "solid-js";
import { createStore, reconcile, unwrap, type SetStoreFunction } from "solid-js/store";

import {
  groupThreadsByWorkspace,
  sortThreadsByLatest,
  sortWorkspaceGroups,
  type ThreadSummary,
  type ThreadWorkspaceGroup,
} from "@/lib/chatThreads";
import { cloneValue } from "@/lib/utils";

type WorkspaceGroup = ThreadWorkspaceGroup & { isRepo: boolean };

/** Preserve group and thread identities across refreshed or reordered snapshots. */
export function useThreadWorkspaceGroups(threads: Accessor<ThreadSummary[]>, projects?: Accessor<ThreadWorkspaceGroup[]>) {
  const [groups, setGroups] = createSignal<WorkspaceGroup[]>([]);
  const entries = new Map<string, [WorkspaceGroup, SetStoreFunction<WorkspaceGroup>]>();

  createComputed(on(() => projects ? projects() : groupThreadsByWorkspace(threads()), (snapshots) => {
    batch(() => {
      const next = snapshots.map((snapshot) => {
        let entry = entries.get(snapshot.key);
        if (!entry) {
          entry = createStore<WorkspaceGroup>({ ...snapshot, threads: [], isRepo: false });
          entries.set(snapshot.key, entry);
        }
        const [group, setGroup] = entry;
        const { threads: snapshotThreads, ...metadata } = snapshot;
        const sortedThreads = sortThreadsByLatest(snapshotThreads);
        setGroup({
          ...metadata,
          isRepo: snapshot.workspace?.scope.kind === "github"
            || sortedThreads.some((thread) => thread.workspace?.scope.kind === "github"),
        });
        setGroup("threads", reconcile(cloneValue(unwrap(sortedThreads)), { key: "thread_id" }));
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
