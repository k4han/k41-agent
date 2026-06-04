import type { AgentCard } from "@/types";

export type AgentForm = {
  name: string;
  display_name: string;
  description: string;
  graph_type: string;
  provider: string;
  model: string;
  tools: string[];
  mcp_servers: string[];
  sub_agents: string[];
  plan_approval_targets: string[];
  hidden: boolean;
  context_trim_threshold: number;
  system_prompt: string;
};

export const AGENT_TABS = ["general", "tools", "prompt"] as const;
export type AgentTab = (typeof AGENT_TABS)[number];

export function isAgentTab(value: string | undefined): value is AgentTab {
  return typeof value === "string" && (AGENT_TABS as readonly string[]).includes(value);
}

export const DEFAULT_CONTEXT_TRIM_THRESHOLD = 50000;

export function blankForm(workflow: string): AgentForm {
  return {
    name: "",
    display_name: "",
    description: "",
    graph_type: workflow,
    provider: "default",
    model: "",
    tools: [],
    mcp_servers: [],
    sub_agents: [],
    plan_approval_targets: [],
    hidden: false,
    context_trim_threshold: DEFAULT_CONTEXT_TRIM_THRESHOLD,
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
    tools: card.tools || [],
    mcp_servers: card.mcp_servers || [],
    sub_agents: card.sub_agents || [],
    plan_approval_targets: card.plan_approval_targets || [],
    hidden: card.hidden || false,
    context_trim_threshold: card.context_trim_threshold || DEFAULT_CONTEXT_TRIM_THRESHOLD,
    system_prompt: card.system_prompt || "",
  };
}

export function isFormDirty(a: AgentForm, b: AgentForm): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

export function defaultWorkflow(workflows: readonly string[]): string {
  if (workflows.includes("react_agent")) {
    return "react_agent";
  }
  return workflows[0] || "";
}
