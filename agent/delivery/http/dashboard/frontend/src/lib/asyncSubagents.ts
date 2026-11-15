import type { SubagentActivity, TranscriptItem } from "@/components/Transcript";
import { normalizeUserInputRequest } from "@/lib/userInputRequest";

export type AsyncTask = SubagentActivity & {
  task_id: string;
  run_id: string;
  version: number;
  result?: string;
  queued_updates?: number;
  interrupts?: Array<{ id: string; value: Record<string, unknown> }>;
};

export const ASYNC_TOOL_NAMES = ["start_async_task", "check_async_task", "list_async_tasks", "update_async_task", "cancel_async_task"];
export const liveAsyncTask = (status: string) => ["pending", "running", "waiting"].includes(status);

export function missingAsyncTasks(tasks: readonly AsyncTask[], items: readonly TranscriptItem[]): AsyncTask[] {
  const represented = new Set(items.flatMap((item) => item.type === "tool" && item.subagent
    && item.subagent.execution_mode === "async" && !item.subagent.read_only
    ? [`${item.subagent.parent_thread_id}/${item.subagent.task_id || item.subagent.delegation_id}`] : []));
  return tasks.filter((task) => !represented.has(`${task.parent_thread_id}/${task.task_id}`));
}

export function asyncTaskPath(parent: string, taskId?: string): string {
  return `/v2/api/chat/${encodeURIComponent(parent)}/async-tasks${taskId ? `/${encodeURIComponent(taskId)}` : ""}`;
}

export function mergeAsyncTask<T extends { version?: number; interrupts?: unknown[] }>(current: T, incoming: Partial<T>): T {
  if ((incoming.version ?? 0) < (current.version ?? 0)) return current;
  const merged = { ...current, ...incoming };
  if (current.interrupts && incoming.interrupts && JSON.stringify(current.interrupts) === JSON.stringify(incoming.interrupts)) {
    merged.interrupts = current.interrupts;
  }
  return merged;
}

export function asyncInputRequest(interrupt: { id: string; value: Record<string, unknown> }) {
  const value = interrupt.value;
  if (value.type === "permission_request") {
    return normalizeUserInputRequest({
      tool_call_id: value.tool_call_id, interrupt_id: interrupt.id, title: "Tool permission approval",
      questions: [{ id: `permission:${String(value.request_id)}`, question: `${value.action}: ${value.resource}\n${JSON.stringify(value.metadata || {})}`,
        selection_mode: "single", required: true, free_text: { enabled: false }, options: [
          { id: "allow_once", label: "Allow once" }, { id: "allow_thread", label: "Allow in this conversation" }, { id: "deny", label: "Deny" },
        ] }], submit_label: "Apply decision",
    });
  }
  return normalizeUserInputRequest({ ...value, interrupt_id: interrupt.id });
}
