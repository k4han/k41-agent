import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type {
  AgentChatPayload,
  AgentCardsPayload,
  AgentMcpOptionsPayload,
  AgentProviderOptionsPayload,
  AgentsPayload,
  AgentToolsPayload,
  AgentWorkflowsPayload,
} from "@/types";

export function fetchAgentCards(): Promise<AgentCardsPayload> {
  return apiFetch<AgentCardsPayload>(API_PATHS.agentCards);
}

export function fetchAgentTools(): Promise<AgentToolsPayload> {
  return apiFetch<AgentToolsPayload>(API_PATHS.agentTools);
}

export function fetchAgentWorkflows(): Promise<AgentWorkflowsPayload> {
  return apiFetch<AgentWorkflowsPayload>(API_PATHS.agentWorkflows);
}

export function fetchAgentProviderOptions(): Promise<AgentProviderOptionsPayload> {
  return apiFetch<AgentProviderOptionsPayload>(API_PATHS.agentProviders);
}

export function fetchAgentMcpOptions(): Promise<AgentMcpOptionsPayload> {
  return apiFetch<AgentMcpOptionsPayload>(API_PATHS.agentMcpOptions);
}

export async function fetchAgentChatOptions(): Promise<AgentChatPayload> {
  const [cards, providers] = await Promise.all([
    fetchAgentCards(),
    fetchAgentProviderOptions(),
  ]);
  return {
    ...cards,
    ...providers,
  };
}

export async function fetchAgentEditorOptions(): Promise<AgentsPayload> {
  const [cards, tools, workflows, providers, mcp] = await Promise.all([
    fetchAgentCards(),
    fetchAgentTools(),
    fetchAgentWorkflows(),
    fetchAgentProviderOptions(),
    fetchAgentMcpOptions(),
  ]);
  return {
    ...cards,
    ...tools,
    ...workflows,
    ...providers,
    ...mcp,
  };
}
