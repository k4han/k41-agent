import type { AcpAgentSettings, AgentCard, AgentsPayload } from "@/types";
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
  permission_mode: AgentCard["permission_mode"];
  tools: string[];
  tool_permissions: AgentCard["tool_permissions"];
  tool_configs: ToolConfigs;
  sub_agents: string[];
  plan_approval_targets: string[];
  internal: boolean;
  context_compact_threshold: number;
  system_prompt: string;
  acp: AcpAgentSettings | null;
};

export const AGENT_TABS = [
  "general",
  "tools",
  "mcp",
  "delegation",
  "permissions",
  "prompt",
] as const;
export type AgentTab = (typeof AGENT_TABS)[number];
export type AgentCapabilityTab = Exclude<AgentTab, "general" | "prompt">;

export function isAgentTab(value: string | undefined): value is AgentTab {
  return typeof value === "string" && (AGENT_TABS as readonly string[]).includes(value);
}

export const DEFAULT_CONTEXT_COMPACT_THRESHOLD = 75;

const ACP_TOOL_PREFIX = "acp:";

export function acpToolName(tool: string): string | undefined {
  return tool.startsWith(ACP_TOOL_PREFIX) ? tool.slice(ACP_TOOL_PREFIX.length) : undefined;
}

export function normalizeAcpSettings(value: AcpAgentSettings | null): AcpAgentSettings {
  const legacy = value as (AcpAgentSettings & { use_default_tools?: boolean }) | null;
  const agent = value?.agent === "opencode" ? "opencode" : "antigravity-cli";
  return {
    agent,
    tools: value && Object.hasOwn(value, "tools") ? value.tools ?? null : legacy?.use_default_tools === false ? [] : null,
    config_options: value?.config_options ?? {},
  };
}

export function isToolSelected(form: AgentForm, tool: string, payload: Pick<AgentsPayload, "acp_tools">): boolean {
  const name = acpToolName(tool);
  if (name === undefined) return form.tools.includes(tool);
  const settings = normalizeAcpSettings(form.acp);
  return Boolean(payload.acp_tools?.find((item) => item.name === name)?.always_enabled)
    || settings.tools == null || settings.tools.includes(name);
}

export function setToolSelection(form: AgentForm, tools: string[], checked: boolean, payload: Pick<AgentsPayload, "acp_tools">): AgentForm {
  const native = tools.map(acpToolName).filter((name): name is string => name !== undefined);
  const manual = tools.filter((tool) => acpToolName(tool) === undefined);
  const selected = new Set(form.tools);
  const tool_configs = { ...form.tool_configs };
  for (const tool of manual) {
    if (checked) selected.add(tool);
    else {
      selected.delete(tool);
      delete tool_configs[tool];
    }
  }
  let acp = form.acp;
  if (native.length) {
    const settings = normalizeAcpSettings(acp);
    const available = (payload.acp_tools ?? []).filter((tool) => !tool.always_enabled).map((tool) => tool.name);
    const names = new Set(settings.tools ?? available);
    for (const name of native) {
      if (!available.includes(name)) continue;
      if (checked) names.add(name);
      else names.delete(name);
    }
    acp = { ...settings, tools: available.every((name) => names.has(name)) ? null : [...names].sort() };
  }
  return { ...form, tools: [...selected].sort(), tool_configs, acp };
}

export function manualToolNames(tools: readonly string[]): string[] {
  return tools.filter((name) => acpToolName(name) === undefined && !["delegate_task", "call_agent", "start_async_task", "check_async_task", "list_async_tasks",
    "update_async_task", "cancel_async_task"].includes(name));
}

export function buildToolGroups(payload: AgentsPayload, initialTools: string[], isAcp = false, acpAgent?: string): AgentToolGroup[] {
  const unavailable = new Set(isAcp ? payload.acp_unavailable_tools || [] : []);
  const groups = (payload.tool_groups || []).map((group) => ({
    category: group.category,
    tools: manualToolNames(group.tools).filter((tool) => !unavailable.has(tool)),
  })).filter((group) => group.tools.length > 0);
  const known = new Set(groups.flatMap((group) => group.tools));
  const extras = manualToolNames(initialTools).filter((tool) => !known.has(tool) && !unavailable.has(tool) && !tool.startsWith("mcp__"));
  if (extras.length > 0) {
    const other = groups.find((group) => group.category === "unknown");
    if (other) {
      other.tools = uniqueSorted([...other.tools, ...extras]);
    } else {
      groups.push({ category: "unknown", tools: uniqueSorted(extras) });
    }
  }
  if (isAcp && acpAgent !== "opencode" && payload.acp_tools?.length) {
    groups.unshift({ category: "acp", tools: payload.acp_tools.map((tool) => `${ACP_TOOL_PREFIX}${tool.name}`) });
  }
  return groups;
}

export function agentFormPayload(form: AgentForm): AgentForm {
  const tools = manualToolNames(form.tools);
  return {
    ...form,
    permission_mode: form.permission_mode ?? null,
    acp: form.acp || form.graph_type === "acp_agent" ? normalizeAcpSettings(form.acp) : null,
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
    permission_mode: workflow === "acp_agent" ? "ask" : null,
    tools: [],
    tool_permissions: null,
    tool_configs: {},
    sub_agents: [],
    plan_approval_targets: [],
    internal: false,
    context_compact_threshold: DEFAULT_CONTEXT_COMPACT_THRESHOLD,
    system_prompt: "",
    acp: workflow === "acp_agent" ? normalizeAcpSettings(null) : null,
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
    permission_mode: card.permission_mode ?? null,
    tools: manualToolNames(card.tools || []),
    tool_permissions: card.tool_permissions ?? null,
    tool_configs: normalizeToolConfigs(card.tool_configs),
    sub_agents: card.sub_agents || [],
    plan_approval_targets: card.plan_approval_targets || [],
    internal: card.internal || false,
    context_compact_threshold: card.context_compact_threshold || DEFAULT_CONTEXT_COMPACT_THRESHOLD,
    system_prompt: card.system_prompt || "",
    acp: card.acp || card.graph_type === "acp_agent" ? normalizeAcpSettings(card.acp ?? null) : null,
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
