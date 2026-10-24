import { hasToolCallResult } from "@/lib/toolCallStatus";
import { isShellTool, shellToolResult } from "@/lib/shellToolResult";

export interface ToolGroupItem {
  name?: string | null;
  args?: unknown;
  result: unknown;
  resultReceived?: boolean;
}

export interface ToolCategoryInfo {
  key: string;
  labelSingular: string;
  labelPlural: string;
}

const READ_TOOLS = new Set([
  "read_file",
  "list_dir",
  "grep",
  "glob",
  "search_code",
  "read_resource",
  "list_resources",
  "read",
]);

const COMMAND_TOOLS = new Set([
  "bash",
  "run_bash",
  "exec_command",
  "read_process_output",
  "write_process_input",
  "stop_process",
  "shell",
  "command",
]);

const EDIT_TOOLS = new Set([
  "write_file",
  "edit_file",
  "patch_file",
  "create_file",
  "delete_file",
  "edit",
  "write",
]);

const WEB_TOOLS = new Set([
  "web_search",
  "web_fetch",
  "search_web",
  "fetch_web",
  "web",
]);

export function isToolFailed(tool: ToolGroupItem): boolean {
  if (!tool || !hasToolCallResult(tool)) return false;
  if (isShellTool(tool.name)) {
    const res = shellToolResult(tool.result);
    return Boolean(res?.failed);
  }
  const result = tool.result;
  if (typeof result === "string") {
    const trimmed = result.trim();
    if (
      trimmed.startsWith("[error]") ||
      trimmed.startsWith("[Error]") ||
      trimmed.startsWith("Error:") ||
      trimmed.startsWith("ToolError:") ||
      trimmed.startsWith("Traceback (most recent call last):")
    ) {
      return true;
    }
    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (
          parsed.status === "error" ||
          parsed.failed === true ||
          (parsed.error && !parsed.success)
        ) {
          return true;
        }
      } catch {
        // ignore JSON parse error
      }
    }
    return false;
  }
  if (typeof result === "object" && result !== null) {
    const obj = result as Record<string, unknown>;
    if (
      obj.status === "error" ||
      obj.failed === true ||
      (obj.error && !obj.success)
    ) {
      return true;
    }
  }
  return false;
}

export function getToolCategory(name: string | null | undefined): ToolCategoryInfo {
  const raw = (name || "").toLowerCase().trim();
  if (raw.startsWith("mcp__")) {
    const parts = raw.split("__");
    const action = parts[parts.length - 1] || "mcp";
    return { key: action, labelSingular: action, labelPlural: `${action}s` };
  }
  if (READ_TOOLS.has(raw)) {
    return { key: "reads", labelSingular: "read", labelPlural: "reads" };
  }
  if (COMMAND_TOOLS.has(raw)) {
    return { key: "commands", labelSingular: "command", labelPlural: "commands" };
  }
  if (EDIT_TOOLS.has(raw)) {
    return { key: "edits", labelSingular: "edit", labelPlural: "edits" };
  }
  if (WEB_TOOLS.has(raw)) {
    return { key: "web", labelSingular: "web", labelPlural: "web" };
  }
  const clean = raw.replace(/_/g, " ").trim() || "other";
  return { key: clean, labelSingular: clean, labelPlural: `${clean}s` };
}

export interface ToolGroupSummary {
  total: number;
  totalFailed: number;
  hasFailed: boolean;
  totalText: string;
  breakdownText: string;
  statusText: string;
  fullLabel: string;
}

export interface FormatToolGroupSummaryOptions {
  live?: boolean;
  running?: boolean;
}

export function formatToolGroupSummary(
  tools: ToolGroupItem[],
  options?: FormatToolGroupSummaryOptions
): ToolGroupSummary {
  const live = options?.live ?? options?.running ?? false;

  if (tools.length === 0) {
    const statusText = live ? "loading" : "";
    return {
      total: 0,
      totalFailed: 0,
      hasFailed: false,
      totalText: "0 tools:",
      breakdownText: "",
      statusText,
      fullLabel: "0 tools",
    };
  }

  const categoriesMap = new Map<
    string,
    {
      labelSingular: string;
      labelPlural: string;
      count: number;
      failed: number;
    }
  >();

  let totalFailed = 0;

  for (const tool of tools) {
    const cat = getToolCategory(tool.name);
    let entry = categoriesMap.get(cat.key);
    if (!entry) {
      entry = {
        labelSingular: cat.labelSingular,
        labelPlural: cat.labelPlural,
        count: 0,
        failed: 0,
      };
      categoriesMap.set(cat.key, entry);
    }
    entry.count += 1;
    if (isToolFailed(tool)) {
      entry.failed += 1;
      totalFailed += 1;
    }
  }

  const total = tools.length;
  const totalText = `${total} tool${total === 1 ? "" : "s"}:`;

  const preferredOrder = ["reads", "commands", "edits", "web"];
  const keys = Array.from(categoriesMap.keys()).sort((a, b) => {
    const idxA = preferredOrder.indexOf(a);
    const idxB = preferredOrder.indexOf(b);
    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
    if (idxA !== -1) return -1;
    if (idxB !== -1) return 1;
    return a.localeCompare(b);
  });

  const breakdownParts: string[] = [];
  for (const key of keys) {
    const entry = categoriesMap.get(key)!;
    const label = entry.count === 1 ? entry.labelSingular : entry.labelPlural;
    if (entry.failed > 0) {
      breakdownParts.push(`${entry.count} ${label} (${entry.failed} failed)`);
    } else {
      breakdownParts.push(`${entry.count} ${label}`);
    }
  }
  const breakdownText = breakdownParts.join(", ");

  let statusText: string;
  let fullLabel: string;
  if (totalFailed > 0 && !live) {
    statusText = `${totalFailed} failed`;
    fullLabel = `${totalText} ${breakdownText} · ${statusText}`;
  } else if (live) {
    statusText = "loading";
    fullLabel = `${totalText} ${breakdownText}`;
  } else {
    statusText = "";
    fullLabel = `${totalText} ${breakdownText}`;
  }

  return {
    total,
    totalFailed,
    hasFailed: totalFailed > 0,
    totalText,
    breakdownText,
    statusText,
    fullLabel,
  };
}
