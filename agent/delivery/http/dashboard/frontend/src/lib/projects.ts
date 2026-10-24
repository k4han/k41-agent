import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { navProjectsStore } from "@/lib/navProjectsStore";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { ProjectSummary, WorkspaceBinding, WorkspaceRef } from "@/types";

export function notifyProjectsChanged() {
  void navProjectsStore.refreshProjects();
  window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.PROJECTS_CHANGED));
}

export function projectChatState(workspace: WorkspaceRef | WorkspaceBinding) {
  return { newChatId: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    workspace: "execution" in workspace ? workspace.execution : workspace };
}

export async function verifyProjectWorkspace(workspace: WorkspaceRef, projectId?: number): Promise<WorkspaceRef | WorkspaceBinding> {
  if (projectId) {
    const result = await apiFetch<ProjectSummary>(`${API_PATHS.projects}/${projectId}`);
    return result.workspace;
  }
  const params = new URLSearchParams({ backend: workspace.backend, locator: workspace.locator });
  if (workspace.metadata.root) params.set("root", String(workspace.metadata.root));
  await apiFetch(`${API_PATHS.workspaceTree}?${params}`);
  return workspace;
}
