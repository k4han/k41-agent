export type WorkspaceTreeEntry = {
  name: string;
  path: string;
  kind: "directory" | "file";
  size: number | null;
  modified_at: number;
};

export type WorkspaceTreeRow = {
  key: string;
  depth: number;
} & ({ kind: "entry"; entry: WorkspaceTreeEntry } | { kind: "status"; text: string });

export function flattenWorkspaceTree(
  entriesByPath: Record<string, WorkspaceTreeEntry[]>,
  expandedByPath: Record<string, boolean>,
  loadingByPath: Record<string, boolean>,
  truncatedByPath: Record<string, boolean>,
  rootPath = "",
): WorkspaceTreeRow[] {
  const rows: WorkspaceTreeRow[] = [];
  const pending: WorkspaceTreeRow[] = [];
  const pushDirectory = (path: string, depth: number) => {
    if (truncatedByPath[path]) {
      pending.push({ key: `truncated:${path}`, kind: "status", depth, text: "Tree truncated" });
    }
    const entries = entriesByPath[path];
    if (entries) {
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const entry = entries[index];
        pending.push({ key: `entry:${entry.path}`, kind: "entry", entry, depth });
      }
    } else if (loadingByPath[path]) {
      pending.push({ key: `loading:${path}`, kind: "status", depth, text: "Loading..." });
    }
  };
  pushDirectory(rootPath, 0);
  while (pending.length > 0) {
    const row = pending.pop()!;
    rows.push(row);
    if (row.kind === "entry" && row.entry.kind === "directory" && expandedByPath[row.entry.path]) {
      pushDirectory(row.entry.path, row.depth + 1);
    }
  }
  return rows;
}
