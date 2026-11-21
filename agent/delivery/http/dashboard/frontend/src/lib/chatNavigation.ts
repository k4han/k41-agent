import type { WorkspaceBinding, WorkspaceRef } from "@/types";

export type NewChatNavigationState = {
  newChatId: string;
  projectId?: number | null;
  workspace?: WorkspaceRef | null;
  workspaceBinding?: WorkspaceBinding;
};

export function newChatNavigationState(context: Omit<NewChatNavigationState, "newChatId"> = {}): NewChatNavigationState {
  return { newChatId: `${Date.now()}-${Math.random().toString(36).slice(2)}`, ...context };
}
