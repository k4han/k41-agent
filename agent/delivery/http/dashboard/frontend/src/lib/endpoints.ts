export const API_PATHS = {
  catalog: "/dashboard-api/catalog",
  config: "/dashboard-api/config",
  providers: "/dashboard-api/providers",
  backends: "/dashboard-api/backends",
  scheduler: "/dashboard-api/scheduler",
  agents: "/dashboard-api/agents",
  tasks: "/dashboard-api/tasks",
  sessions: "/dashboard-api/sessions",
  sessionsEvents: "/dashboard-api/sessions/events",
  sessionsStop: "/dashboard-api/sessions/stop",
  chatHistory: "/dashboard-api/chat-history",
  promptVariables: "/dashboard-api/prompt-variables",
  mcpServers: "/dashboard-api/mcp/servers",
  mcpPopular: "/dashboard-api/mcp/popular",
  mcpTest: "/dashboard-api/mcp/test",
  githubRepositories: "/dashboard-api/github/repositories",
  usage: "/dashboard-api/usage",
  usageWorkspaces: "/dashboard-api/usage/workspaces",
  usageThreads: "/dashboard-api/usage/threads",
  channels: "/dashboard-api/channels",
  github: "/dashboard-api/github",
  githubSync: "/dashboard-api/github/sync",
  channelsRuntime: (name: string) =>
    `/dashboard-api/channels/${encodeURIComponent(name)}/runtime`,
  channelsRuntimeStart: (name: string) =>
    `/dashboard-api/channels/${encodeURIComponent(name)}/runtime/start`,
  channelsRuntimeStop: (name: string) =>
    `/dashboard-api/channels/${encodeURIComponent(name)}/runtime/stop`,
  providersUpdateCatalog: "/dashboard-api/providers/update-catalog",
  settings: "/settings",
  settingsKey: (key: string) => `/settings/${encodeURIComponent(key)}`,
  thread: (threadId: string, checkpointId?: string) => {
    const path = `/dashboard-api/chat-history/${encodeURIComponent(threadId)}`;
    if (!checkpointId) {
      return path;
    }
    return `${path}?checkpoint_id=${encodeURIComponent(checkpointId)}`;
  },
  task: (taskId: string) => `/tasks/${encodeURIComponent(taskId)}`,
  taskCancel: (taskId: string) => `/tasks/${encodeURIComponent(taskId)}/cancel`,
  tasksList: "/tasks/list",
  tasksSubmit: "/tasks",
  schedulerJob: (jobId: string) => `/scheduler/jobs/${encodeURIComponent(jobId)}`,
  schedulerJobAction: (jobId: string, action: "run" | "pause" | "resume") =>
    `/scheduler/jobs/${encodeURIComponent(jobId)}/${action}`,
  schedulerJobs: "/scheduler/jobs",
  promptVariableItem: (name: string) => `/prompt-variables/${encodeURIComponent(name)}`,
  promptVariablesResource: "/prompt-variables",
  provider: (name: string) => `/dashboard-api/providers/${encodeURIComponent(name)}`,
  providerModels: "/providers/models",
  chatEvents: "/api/chat/events",
  chatEventsReconnect: "/api/chat/events/reconnect",
  chatEventsEdit: "/api/chat/events/edit",
  chatStream: "/api/chat/stream",
  chatStreamRun: "/api/chat/stream/run",
  backgroundTaskStream: (threadId: string) =>
    `/dashboard-api/background-task-events?thread_id=${encodeURIComponent(threadId)}`,
  mcpServer: (name: string) => `/dashboard-api/mcp/servers/${encodeURIComponent(name)}`,
  githubRepository: (bindingId: number) =>
    `/dashboard-api/github/repositories/${encodeURIComponent(String(bindingId))}`,
  githubRepositoryActivity: (bindingId: number) =>
    `/dashboard-api/github/repositories/${encodeURIComponent(String(bindingId))}/activity`,
  workspaceDefault: "/dashboard-api/workspace/default",
  workspaceResolve: "/dashboard-api/workspace/resolve",
  workspaceTree: "/dashboard-api/workspace/tree",
  workspaceChanges: "/dashboard-api/workspace/changes",
  workspaceDiff: "/dashboard-api/workspace/diff",
  workspaceFile: "/dashboard-api/workspace/file",
  workspaceBrowse: "/dashboard-api/workspace/browse",
  workspaceCreate: "/dashboard-api/workspace/create",
  workspaceTest: "/dashboard-api/workspace/test",
  workspaceRename: "/dashboard-api/workspace/rename",
  workspaceDelete: "/dashboard-api/workspace/delete",
  agentsCards: "/agents/cards",
  agentCard: (name: string) => `/agents/cards/${encodeURIComponent(name)}`,
  agentCardClone: (name: string) => `/agents/cards/${encodeURIComponent(name)}/clone`,
  agentsReload: "/agents/reload",
  serviceStart: (name: string) => `/services/${encodeURIComponent(name)}/start`,
  serviceStop: (name: string) => `/services/${encodeURIComponent(name)}/stop`,
  serviceTest: (name: string) => `/services/${encodeURIComponent(name)}/test`,
  channelPair: "/channels/pair",
  channelIdentity: (id: number) => `/channels/identities/${encodeURIComponent(String(id))}`,
} as const;

export const SSE_URLS = {
  sessions: "/dashboard-api/sessions/events",
} as const;
