import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { navProjectsStore } from "@/lib/navProjectsStore";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { WorkspaceRef } from "@/types";

export function notifyProjectsChanged() {
  void navProjectsStore.refreshProjects();
  window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.PROJECTS_CHANGED));
}

export function projectChatState(workspace: WorkspaceRef) {
  return { newChatId: `${Date.now()}-${Math.random().toString(36).slice(2)}`, workspace };
}

export async function verifyProjectWorkspace(workspace: WorkspaceRef) {
  const params = new URLSearchParams({ backend: workspace.backend, locator: workspace.locator });
  if (workspace.metadata.root) params.set("root", String(workspace.metadata.root));
  await apiFetch(`${API_PATHS.workspaceTree}?${params}`);
}
