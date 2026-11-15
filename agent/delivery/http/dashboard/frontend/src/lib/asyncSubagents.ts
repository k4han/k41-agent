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
  const launches = new Set(items.flatMap((item) => item.type === "tool" && item.name === "start_async_task"
    && !item.subagent?.read_only && item.tool_call_id ? [item.tool_call_id] : []));
  return tasks.filter((task) => !represented.has(`${task.parent_thread_id}/${task.task_id}`)
    && !launches.has(task.tool_call_id));
}

/** Bind recovered tasks to their original call before its result arrives. */
export function attachAsyncTaskActivities<T extends TranscriptItem>(items: T[], tasks: readonly AsyncTask[], parent: string): T[] {
  const calls = new Map(tasks.filter((task) => task.parent_thread_id === parent)
    .map((task) => [task.tool_call_id, task]));
  let changed = false;
  const linked = items.map((item) => {
    if (item.type !== "tool" || item.name !== "start_async_task" || item.subagent || !item.tool_call_id) return item;
    const task = calls.get(item.tool_call_id);
    if (!task) return item;
    changed = true;
    return { ...item, subagent: task };
  });
  return changed ? linked : items;
}

export type SubagentTranscriptItem = TranscriptItem & { key: string };

function transcriptKey(item: TranscriptItem & { key?: string; id?: number }, index: number): string {
  if (item.type === "async_wakeup") return `async_wakeup:${item.wakeup_id}`;
  if ((item.type === "message" || item.type === "thinking") && item.messageId) {
    return `${item.type}:${item.messageId.replace(/^lc_run--/, "")}`;
  }
  if ("tool_call_id" in item && item.tool_call_id) return `${item.type}:${item.tool_call_id}`;
  return item.key || `${item.type}:${item.id ?? index}`;
}

/** Merge replay with checkpoints without shrinking already visible content. */
export function mergeSubagentTranscript(saved: readonly TranscriptItem[], live: readonly TranscriptItem[]): SubagentTranscriptItem[] {
  const history = saved.map((item, index) => ({ ...item, key: transcriptKey(item, index) }));
  const existing = new Map(history.map((item) => [item.key, item]));
  const before = new Map<string, SubagentTranscriptItem[]>();
  let pending: SubagentTranscriptItem[] = [];
  live.forEach((item, index) => {
    const key = transcriptKey(item, index);
    const previous = existing.get(key);
    if (!previous) {
      pending.push({ ...item, key });
      return;
    }
    before.set(key, pending);
    pending = [];
    let merged = { ...previous, ...item, key };
    if ((previous.type === "message" && item.type === "message")
      || (previous.type === "thinking" && item.type === "thinking")) {
      if (previous.text.startsWith(item.text)) merged = { ...merged, text: previous.text } as SubagentTranscriptItem;
    } else if (previous.type === "tool" && item.type === "tool" && !item.resultReceived && item.result == null) {
      merged = { ...merged, result: previous.result, resultReceived: previous.resultReceived } as SubagentTranscriptItem;
    }
    existing.set(key, merged);
  });
  return [...history.flatMap((item) => [...(before.get(item.key) || []), existing.get(item.key)!]), ...pending];
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
