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
  sub_agents: string[] | null;
  max_context_tokens: number;
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
  sub_agents: string[] | null;
  max_context_tokens: number;
  system_prompt: string;
};

export type AgentsPayload = {
  cards: AgentCard[];
  tools: string[];
  workflows: string[];
  agent_names: string[];
  provider_names: string[];
  default_provider: string;
  model_catalogs: ModelCatalog[];
  model_catalog_error: string;
};

export type BackgroundTask = {
  task_id: string;
  request: string;
  agent_name: string;
  working_dir: string | null;
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
