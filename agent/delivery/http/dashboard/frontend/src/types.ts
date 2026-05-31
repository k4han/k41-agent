export type ServiceStatus = {
  name: string;
  status: string;
  error: string | null;
};

export type Identity = {
  id: number | null;
  user_id: number | null;
  platform: string;
  external_id: string;
  created_at: string | null;
  updated_at: string | null;
};

export type ModelOption = {
  id: string;
  label: string;
  source: string;
  context_window?: number;
  input_types?: string[] | null;
};

export type ModelCatalog = {
  provider: string;
  provider_type: string;
  default_model: string;
  can_list_models: boolean;
  models: ModelOption[];
  error: string | null;
};

export type AgentCard = {
  name: string;
  display_name: string;
  description: string;
  graph_type: string;
  provider: string;
  model: string;
  tools: string[];
  mcp_servers?: string[];
  sub_agents: string[] | null;
  hidden: boolean;
  context_trim_threshold: number;
  system_prompt: string;
  source: "builtin" | "user";
  path: string;
  editable: boolean;
  overrides_builtin: boolean;
  valid: boolean;
  error: string;
};

export type AgentConfig = {
  name: string;
  display_name: string;
  description: string;
  graph_type: string;
  provider: string;
  model: string;
  tools: string[];
  mcp_servers?: string[];
  sub_agents: string[] | null;
  hidden: boolean;
  context_trim_threshold: number;
  system_prompt: string;
};

export type ToolGroup = {
  category: string;
  tools: string[];
};

export type AgentsPayload = {
  cards: AgentCard[];
  tools: string[];
  tool_groups?: ToolGroup[];
  workflows: string[];
  agent_names: string[];
  provider_names: string[];
  default_provider: string;
  default_model: string;
  model_catalogs: ModelCatalog[];
  model_catalog_error: string;
  mcp_server_options?: string[];
};

export type PromptVariable = {
  name: string;
  value: string;
  placeholder: string;
  created_at: string | null;
  updated_at: string | null;
  is_system?: boolean;
};

export type PromptVariablesPayload = {
  variables: PromptVariable[];
};

export type WorkspaceRef = {
  backend: "local";
  locator: string;
  label: string;
  metadata: Record<string, unknown>;
};

export type BackgroundTask = {
  task_id: string;
  request: string;
  agent_name: string;
  workspace: WorkspaceRef | null;
  status: string;
  result: string;
  error: string;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
  elapsed_seconds: number;
  elapsed_display: string;
  thread_id: string;
  notify_channel: {
    platform: string;
    external_id: string;
    channel_id: string;
  } | null;
};

export type ActiveSession = {
  thread_id: string;
  session_id: string;
  platform: string;
  user_id: string;
  channel_id: string;
  agent_name: string;
  started_at: number;
  elapsed_seconds: number;
  elapsed_display: string;
  current_step: string;
  tools_called: string[];
};

export type SchedulerJob = {
  id: string;
  task: string;
  platform: string;
  user_id: string;
  trigger_type: string;
  trigger_args: Record<string, unknown>;
  next_run_time: string | null;
  paused: boolean;
};

export type SettingInfo = {
  key: string;
  value: unknown;
  source: string;
  input_type: string;
  description: string;
  category: string;
  label: string;
  min?: number;
  max?: number;
  step?: number;
};

export type SourceValue = {
  key: string;
  value: unknown;
  source: string;
};

export type ProviderRow = {
  name: string;
  fields: Record<string, { key: string; info: SettingInfo }>;
  type: string;
  type_label: string;
  requires_base_url: boolean;
  enabled: boolean;
  is_default: boolean;
  ready: boolean;
  can_delete: boolean;
  delete_block_reason: string;
  can_set_default: boolean;
  default_block_reason: string;
};

export type ProviderTypeOption = {
  value: string;
  label: string;
  description: string;
  requires_base_url: boolean;
};

export type SettingsPayload = {
  active_nav: "config" | "providers";
  page_title: string;
  page_subtitle: string;
  settings: Record<string, SettingInfo>;
  by_category: Record<string, Record<string, SettingInfo>>;
  settings_sources: Record<string, SourceValue[]>;
  provider_rows?: ProviderRow[];
  provider_name_options?: string[];
  provider_field_order?: string[];
  provider_type_options?: ProviderTypeOption[];
  providers_catalog?: Record<string, any>;
};

export type UsageSummary = {
  event_count: number;
  known_usage_count: number;
  missing_usage_count: number;
  internal_event_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

export type UsageRow = {
  platform: string;
  user_id: string;
  channel_id: string;
  identity_label: string;
  event_count: number;
  missing_usage_count: number;
  internal_event_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  last_used_at: string | null;
};

export type UsageFilterOption = {
  platform: string;
  user_id: string;
  channel_id?: string;
  label: string;
};

export interface WorkspaceUsageDetail {
  backend: string;
  locator: string;
  label: string;
  thread_count: number;
  event_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  last_used_at: string | null;
  models: {
    model: string;
    provider: string;
    total_tokens: number;
    percentage: number;
  }[];
}

export interface ThreadUsageDetail {
  thread_id: string;
  title: string;
  agent_name: string;
  event_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  last_used_at: string | null;
  models: {
    model: string;
    provider: string;
    total_tokens: number;
    percentage: number;
  }[];
}

export type UsagePayload = {
  summary: UsageSummary;
  rows: UsageRow[];
  workspaces: WorkspaceUsageDetail[];
  threads: ThreadUsageDetail[];
  filters: {
    platforms: string[];
    users: UsageFilterOption[];
    channels: UsageFilterOption[];
    agents: string[];
    providers: string[];
    models: string[];
  };
  pagination: {
    limit: number;
    offset: number;
    total: number;
    has_more: boolean;
    next_offset: number | null;
  };
  range: {
    start: string;
    end: string;
  };
};

export type GitHubRepositoryBinding = {
  id: number;
  repository_id: number;
  installation_id: number;
  full_name: string;
  account_login: string;
  private: boolean;
  default_branch: string;
  enabled: boolean;
  agent_name: string;
  trigger_label: string;
  mention_triggers: string[];
  notify_platform: string;
  notify_external_id: string;
  notify_channel_id: string;
  last_synced_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type GitHubPayload = {
  configured: boolean;
  enabled: boolean;
  app_slug: string;
  webhook_url: string;
  install_url: string;
  default_agent: string;
  trigger_label: string;
  mention_triggers: string[];
  repositories: GitHubRepositoryBinding[];
  agent_names: string[];
};

export type McpTransport = "stdio" | "streamable_http";

export type McpEnvField = {
  key: string;
  label: string;
  description: string;
  required: boolean;
  secret: boolean;
};

export type McpPopularServer = {
  id: string;
  name: string;
  description: string;
  transport: McpTransport;
  command: string;
  args: string[];
  url: string;
  homepage: string;
  env_fields: McpEnvField[];
};

export type McpPopularPayload = {
  servers: McpPopularServer[];
};

export type McpToolInfo = {
  name: string;
  prefixed_name: string;
  description: string;
};

export type McpServerStatus = {
  name: string;
  transport: McpTransport;
  enabled: boolean;
  loaded: boolean;
  tool_count: number;
  tools: McpToolInfo[];
  error: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
};

export type McpServersPayload = {
  servers: McpServerStatus[];
};

export type McpServerInput = {
  name: string;
  transport: McpTransport;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  enabled: boolean;
};

export type McpTestResult = {
  ok: boolean;
  error: string;
  tools: McpToolInfo[];
};

export interface ModelUsageDetail {
  model: string;
  provider: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  percentage: number;
}

export interface ThreadUsagePayload {
  thread_id: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  current_context_tokens?: number;
  latest_input_tokens?: number;
  latest_output_tokens?: number;
  latest_total_tokens?: number;
  latest_model?: string;
  latest_provider?: string;
  latest_used_at?: string | null;
  models: ModelUsageDetail[];
}

export interface WorkspaceUsagePayload {
  backend: string;
  locator: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  models: ModelUsageDetail[];
}
