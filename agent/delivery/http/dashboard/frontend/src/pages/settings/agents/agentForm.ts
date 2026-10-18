import type { AgentCard, AgentsPayload } from "@/types";
import { sameJsonValue, uniqueSorted } from "@/lib/utils";

export type ToolConfigValue = string | number | boolean | null;
export type ToolConfigs = Record<string, Record<string, ToolConfigValue>>;

export type AgentToolGroup = {
  category: string;
  tools: string[];
};

export type AgentForm = {
  name: string;
  display_name: string;
  description: string;
  graph_type: string;
  provider: string;
  model: string;
  reasoning_effort: string | null;
  tools: string[];
  tool_permissions: AgentCard["tool_permissions"];
  tool_configs: ToolConfigs;
  sub_agents: string[];
  plan_approval_targets: string[];
  internal: boolean;
  context_compact_threshold: number;
  system_prompt: string;
};

export const AGENT_TABS = ["general", "tools", "prompt"] as const;
export type AgentTab = (typeof AGENT_TABS)[number];

export function isAgentTab(value: string | undefined): value is AgentTab {
  return typeof value === "string" && (AGENT_TABS as readonly string[]).includes(value);
}

export const DEFAULT_CONTEXT_COMPACT_THRESHOLD = 75;

export function manualToolNames(tools: readonly string[]): string[] {
  return tools.filter((name) => name !== "delegate_task" && name !== "call_agent");
}

export function buildToolGroups(payload: AgentsPayload, initialTools: string[]): AgentToolGroup[] {
  const groups = (payload.tool_groups || []).map((group) => ({
    category: group.category,
    tools: manualToolNames(group.tools),
  })).filter((group) => group.tools.length > 0);
  const known = new Set(groups.flatMap((group) => group.tools));
  const extras = manualToolNames(initialTools).filter((tool) => !known.has(tool) && !tool.startsWith("mcp__"));
  if (extras.length > 0) {
    const other = groups.find((group) => group.category === "unknown");
    if (other) {
      other.tools = uniqueSorted([...other.tools, ...extras]);
    } else {
      groups.push({ category: "unknown", tools: uniqueSorted(extras) });
    }
  }
  return groups;
}

export function agentFormPayload(form: AgentForm): AgentForm {
  const tools = manualToolNames(form.tools);
  return {
    ...form,
    tools,
    tool_configs: Object.fromEntries(
      Object.entries(form.tool_configs).filter(
        ([name, fields]) => tools.includes(name) && Object.keys(fields).length > 0,
      ),
    ),
  };
}

export function blankForm(workflow: string): AgentForm {
  return {
    name: "",
    display_name: "",
    description: "",
    graph_type: workflow,
    provider: "default",
    model: "",
    reasoning_effort: null,
    tools: [],
    tool_permissions: null,
    tool_configs: {},
    sub_agents: [],
    plan_approval_targets: [],
    internal: false,
    context_compact_threshold: DEFAULT_CONTEXT_COMPACT_THRESHOLD,
    system_prompt: "",
  };
}

export function cardToForm(card: AgentCard): AgentForm {
  return {
    name: card.name,
    display_name: card.display_name,
    description: card.description,
    graph_type: card.graph_type || "react_agent",
    provider: card.provider || "default",
    model: card.model || "",
    reasoning_effort: card.reasoning_effort ?? null,
    tools: manualToolNames(card.tools || []),
    tool_permissions: card.tool_permissions ?? null,
    tool_configs: normalizeToolConfigs(card.tool_configs),
    sub_agents: card.sub_agents || [],
    plan_approval_targets: card.plan_approval_targets || [],
    internal: card.internal || false,
    context_compact_threshold: card.context_compact_threshold || DEFAULT_CONTEXT_COMPACT_THRESHOLD,
    system_prompt: card.system_prompt || "",
  };
}

export function isFormDirty(a: AgentForm, b: AgentForm): boolean {
  return !sameJsonValue(a, b);
}

function normalizeToolConfigs(value: unknown): ToolConfigs {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const result: ToolConfigs = {};
  for (const [toolName, fields] of Object.entries(value)) {
    if (!fields || typeof fields !== "object" || Array.isArray(fields)) {
      continue;
    }
    result[toolName] = {};
    for (const [fieldName, fieldValue] of Object.entries(fields)) {
      if (
        typeof fieldValue === "string" ||
        typeof fieldValue === "number" ||
        typeof fieldValue === "boolean" ||
        fieldValue === null
      ) {
        result[toolName][fieldName] = fieldValue;
      }
    }
    if (Object.keys(result[toolName]).length === 0) {
      delete result[toolName];
    }
  }
  return result;
}

export function defaultWorkflow(workflows: readonly string[]): string {
  if (workflows.includes("react_agent")) {
    return "react_agent";
  }
  return workflows[0] || "";
}
