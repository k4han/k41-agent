import type { WorkspaceRef } from "../types";

export function localWorkspaceRef(locator: string): WorkspaceRef | null {
  const trimmed = locator.trim();
  if (!trimmed) {
    return null;
  }
  return {
    backend: "local",
    locator: trimmed,
    label: trimmed,
    metadata: {},
  };
}
