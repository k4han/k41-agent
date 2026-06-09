import type { ThreadSummary } from "@/lib/chatThreads";

export type ServiceStatus = {
  name: string;
  status: string;
  error: string | null;
};

export type SystemHealth = {
  status: "healthy" | "degraded" | "down";
  uptime_seconds: number;
  uptime_display: string;
  started_at: string | null;
  version: string;
};

export type HomeCounters = {
  channels: { total: number; running: number; error: number };
  agents: number;
  tasks: { total: number; active: number; failed: number };
  scheduler: { total: number; upcoming: number };
  sessions_active: number;
  providers: { total: number; ready: number };
  mcp_servers: { total: number; connected: number };
};

export type ProviderHealth = {
  name: string;
  type: string;
  enabled: boolean;
  ready: boolean;
  has_api_key: boolean;
  default_model: string;
  model_count: number;
};

export type UpcomingJob = {
  id: string;
  task: string;
  platform: string;
  user_id: string;
  trigger_type: string;
  trigger_args: Record<string, unknown>;
  next_run_time: string | null;
  paused: boolean;
};

export type OnboardingState = {
  show_checklist: boolean;
  needs_provider: boolean;
  needs_channel: boolean;
  needs_agent: boolean;
};

export type HomePayload = {
  services: ServiceStatus[];
  system: SystemHealth;
  counters: HomeCounters;
  recent: {
    tasks: BackgroundTask[];
    threads: ThreadSummary[];
    upcoming_jobs: UpcomingJob[];
  };
  active_sessions: ActiveSession[];
  providers_health: ProviderHealth[];
  scheduler_timezone: string;
  onboarding: OnboardingState;
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
  plan_approval_targets: string[];
  hidden: boolean;
  context_trim_threshold: number;
  max_context_tokens?: number | null;
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
  plan_approval_targets: string[];
  hidden: boolean;
  context_trim_threshold: number;
  max_context_tokens?: number | null;
  system_prompt: string;
};

export type ToolGroup = {
  category: string;
  tools: string[];
};

export type SkillInfo = {
  name: string;
  description: string;
  path: string;
  skill_file?: string;
  license?: string | null;
  compatibility?: string | null;
  metadata?: Record<string, string>;
  allowed_tools?: string[];
  resources?: string[];
  content?: string;
};

export type SkillsPayload = {
  skills_root: string;
  settings: Record<string, SettingInfo>;
  settings_sources: Record<string, SourceValue[]>;
  skills: SkillInfo[];
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
  mcp_installs?: Record<string, AgentMcpInstall[]>;
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

export type WorkspaceBackendKey = "local" | "daytona" | "modal";

export function isSandboxBackend(backend: string): boolean {
  return backend !== "local";
}

export type WorkspaceRef = {
  backend: WorkspaceBackendKey;
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
  thread_deleted: boolean;
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
  restart_required?: boolean;
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

export type ChannelCatalogSection = {
  id: string;
  title: string;
  subtitle?: string;
  default_collapsed?: boolean;
};

export type ChannelCatalogSetting = {
  name: string;
  key: string;
  label: string;
  description: string;
  input_type: string;
  required: boolean;
  secret: boolean;
  section: string;
  default: unknown;
};

export type ChannelCatalogItem = {
  name: string;
  title: string;
  required_env: string[];
  summary?: string;
  tagline?: string;
  capabilities?: string[];
  settings?: ChannelCatalogSetting[];
  sections?: ChannelCatalogSection[];
};

export type BackendCatalogItem = {
  name: string;
  title: string;
  summary?: string;
  capabilities?: string[];
  availability?: Record<string, unknown>;
  install_extra?: string;
};

export type SelectOption = {
  value: string;
  label: string;
};

export type CatalogResponse = {
  provider_types: ProviderTypeOption[];
  channels: ChannelCatalogItem[];
  backends: BackendCatalogItem[];
  trigger_types: SelectOption[];
  channel_statuses: SelectOption[];
  platforms: SelectOption[];
  mcp_transports: SelectOption[];
  prompt_variable_name_pattern: string;
  system_variable_names: string[];
};

export type SettingsPayload = {
  active_nav: "config" | "providers" | "backends";
  page_title: string;
  page_subtitle: string;
  settings: Record<string, SettingInfo>;
  by_category: Record<string, Record<string, SettingInfo>>;
  settings_sources: Record<string, SourceValue[]>;
  provider_rows?: ProviderRow[];
  provider_name_options?: string[];
  provider_names?: string[];
  provider_field_order?: string[];
  provider_type_options?: ProviderTypeOption[];
  providers_catalog?: Record<string, any>;
  model_catalogs?: ModelCatalog[];
  model_catalog_error?: string;
  default_provider?: string;
  default_model?: string;
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
  thread_count?: number;
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
  view?: "all" | "users" | "workspaces" | "threads";
  display_timezone?: string;
  filters: {
    platforms: string[];
    users: UsageFilterOption[];
    channels: UsageFilterOption[];
    agents: string[];
    providers: string[];
    models: string[];
    call_kinds: string[];
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
  issue_label_enabled: boolean;
  issue_comment_enabled: boolean;
  pr_review_comment_enabled: boolean;
  repository_instructions: string;
  provider_name: string;
  model_name: string;
  context_trim_threshold: number | null;
  tool_policy_mode: "inherit" | "custom";
  allowed_tools: string[];
  allowed_skills: string[];
  branch_prefix: string;
  workspace_backend: "local" | "daytona" | "modal";
  last_synced_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type RepositoryActivity = {
  active_count: number;
  recent_count: number;
  tasks: BackgroundTask[];
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
  repository_activity?: Record<string, RepositoryActivity>;
  agent_names: string[];
};

export type GitHubRepositoryDetailPayload = {
  repository: GitHubRepositoryBinding;
  activity: RepositoryActivity;
  identities: Identity[];
  agent_names: string[];
  tools: string[];
  tool_groups: ToolGroup[];
  skills: SkillInfo[];
  repository_skill_dir: string;
  provider_names: string[];
  default_provider: string;
  default_model: string;
  model_catalogs: ModelCatalog[];
  model_catalog_error: string;
};

export type McpTransport = "stdio" | "streamable_http";

export type McpRegistryInput = {
  key: string;
  label: string;
  description: string;
  required: boolean;
  secret: boolean;
  default: string;
  placeholder: string;
  source: string;
};

export type McpInstallTarget = {
  id: string;
  label: string;
  transport: McpTransport;
  registry_type: string;
  runtime_hint: string;
  command: string;
  args: string[];
  url: string;
  env_template: Record<string, string>;
  headers_template: Record<string, string>;
  required_inputs: McpRegistryInput[];
};

export type McpSearchResult = {
  registry_name: string;
  title: string;
  description: string;
  version: string;
  is_latest: boolean;
  verified: boolean;
  repository_url: string;
  website_url: string;
  install_targets: McpInstallTarget[];
  required_inputs: McpRegistryInput[];
  auth_summary: string;
};

export type McpSearchPayload = {
  servers: McpSearchResult[];
  next_cursor: string;
  count: number;
};

export type AgentMcpInstall = {
  id: number;
  install_id: number;
  agent_name: string;
  server_name: string;
  registry_name: string;
  registry_version: string;
  source_type: string;
  title: string;
  description: string;
  verified: boolean;
  transport: McpTransport;
  enabled: boolean;
  agent_enabled: boolean;
};

export type AgentMcpInstallsPayload = {
  installs: AgentMcpInstall[];
};

export type McpInstallResponse = {
  status: "installed" | "auth_required";
  install_id: number | null;
  credential_ref: string;
  redirect_url?: string;
  server_name?: string;
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

export type SandboxStatus =
  | "started"
  | "starting"
  | "stopped"
  | "archived"
  | "destroyed"
  | "error"
  | "unknown";

export type SandboxBackendKey = Exclude<WorkspaceBackendKey, "local">;

export function sandboxBackendDefaultRoot(backend: SandboxBackendKey): string {
  if (backend === "modal") return "/workspace";
  return "workspace";
}

export interface SandboxSummary {
  sandbox_id: string;
  backend: SandboxBackendKey;
  label: string;
  root: string;
  status: SandboxStatus;
  thread_id: string | null;
  thread_alive: boolean;
  repository_full_name: string | null;
  last_used_at: string | null;
  last_started_at: string | null;
  last_stopped_at: string | null;
  last_archived_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  on_cloud: boolean;
  is_orphan: boolean;
  metadata: Record<string, unknown>;
}

export interface SandboxListPayload {
  backend: SandboxBackendKey;
  include_all: boolean;
  count: number;
  sandboxes: SandboxSummary[];
}

export interface SandboxDeleteResult {
  status: "deleted";
  backend: SandboxBackendKey;
  sandbox_id: string;
  cloud_status: string;
  detached_threads: string[];
}
