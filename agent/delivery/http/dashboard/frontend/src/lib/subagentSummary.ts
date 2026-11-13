import type { SubagentActivity, TranscriptItem, TranscriptTool } from "@/components/Transcript";
import { isChatStatusText } from "@/lib/chatStatus";
import { toolCallSummary } from "@/lib/toolCallSummary";
import { hasToolCallResult } from "@/lib/toolCallStatus";
import { formatToolGroupSummary, isToolFailed } from "@/lib/toolGroupSummary";

function latestActivityDetail(items: readonly TranscriptItem[]): string | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type === "tool") {
      const label = isToolFailed(item) ? "Failed tool" : "Last tool";
      return `${label}: ${toolCallSummary(item.name, item.args)}`;
    }
    if (item.type === "thinking" || (item.type === "message" && item.role === "assistant")) {
      const text = item.text.trim();
      if (!text || isChatStatusText(text)) continue;
      const paragraph = text.split(/\n\s*\n/).pop()!.replace(/\s+/g, " ");
      const label = item.type === "thinking"
        ? item.durationMs === undefined ? "Thinking" : "Last thought"
        : "Latest update";
      return `${label}: ${paragraph}`;
    }
  }
}

export function summarizeSubagent(activity: SubagentActivity, items: readonly TranscriptItem[]) {
  const live = activity.status === "started" || activity.status === "running";
  const tools: TranscriptTool[] = [];
  const callIndices = new Map<string, number>();
  for (const item of items) {
    if (item.type !== "tool") continue;
    const previousIndex = item.tool_call_id ? callIndices.get(item.tool_call_id) : undefined;
    if (previousIndex !== undefined) {
      tools[previousIndex] = { ...tools[previousIndex], ...item };
    } else {
      if (item.tool_call_id) callIndices.set(item.tool_call_id, tools.length);
      tools.push(item);
    }
  }
  const running = live ? tools.filter((tool) => {
    if (hasToolCallResult(tool) || tool.executionStatus === "completed" || tool.executionStatus === "failed") {
      return false;
    }
    return !tool.subagent || ["started", "running"].includes(tool.subagent.status);
  }) : [];
  const latest = running[running.length - 1];
  const statusLabels: Record<string, string> = {
    started: tools.length ? "Working..." : "Starting...",
    running: "Working...",
    completed: "Completed",
    interrupted: "Interrupted",
    stopped: "Stopped",
    failed: "Failed",
  };
  const statusText = statusLabels[activity.status] || activity.status;
  // Preserve fast calls and actual reasoning between events instead of
  // immediately replacing every finished activity with a generic status.
  const detail = !live ? statusText : latest
    ? `${toolCallSummary(latest.name, latest.args)}${running.length > 1 ? ` · +${running.length - 1}` : ""}`
    : latestActivityDetail(items) || statusText;
  return {
    live,
    statusText,
    detail,
    runningCount: running.length,
    summary: formatToolGroupSummary(tools, { live }),
  };
}
