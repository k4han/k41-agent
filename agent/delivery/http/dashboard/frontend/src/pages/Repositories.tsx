import { A, useNavigate, useParams } from "@solidjs/router";
import { createMemo, createSignal, For, JSX, onMount, Show } from "solid-js";
import {
  ArrowLeft,
  Bot,
  GitBranch,
  GitPullRequest,
  MessageSquare,
  Play,
  Save,
  Settings2,
  SlidersHorizontal,
} from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { Dialog } from "@/components/Dialog";
import { IdentityPicker } from "@/components/IdentityPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { SelectControl } from "@/components/SelectControl";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import type {
  BackgroundTask,
  GitHubPayload,
  GitHubRepositoryBinding,
  GitHubRepositoryDetailPayload,
  Identity,
} from "@/types";

type RepositoryDraft = {
  enabled: boolean;
  agent_name: string;
  trigger_label: string;
  mention_triggers_text: string;
  notify_identity: string;
  issue_label_enabled: boolean;
  issue_comment_enabled: boolean;
  pr_review_comment_enabled: boolean;
  repository_instructions: string;
  provider_name: string;
  model_name: string;
  context_trim_threshold_text: string;
  tool_policy_mode: "inherit" | "custom";
  allowed_tools: string[];
  branch_prefix: string;
};

type RepositoryTab = "overview" | "automation" | "optimization" | "activity";

function notifyIdentity(repo: GitHubRepositoryBinding): string {
  return repo.notify_platform && repo.notify_external_id
    ? `${repo.notify_platform}:${repo.notify_external_id}`
    : "";
}

function toDraft(repo: GitHubRepositoryBinding): RepositoryDraft {
  return {
    enabled: repo.enabled,
    agent_name: repo.agent_name,
    trigger_label: repo.trigger_label,
    mention_triggers_text: repo.mention_triggers.join(", "),
    notify_identity: notifyIdentity(repo),
    issue_label_enabled: repo.issue_label_enabled ?? true,
    issue_comment_enabled: repo.issue_comment_enabled ?? true,
    pr_review_comment_enabled: repo.pr_review_comment_enabled ?? true,
    repository_instructions: repo.repository_instructions || "",
    provider_name: repo.provider_name || "",
    model_name: repo.model_name || "",
    context_trim_threshold_text: repo.context_trim_threshold
      ? String(repo.context_trim_threshold)
      : "",
    tool_policy_mode: repo.tool_policy_mode === "custom" ? "custom" : "inherit",
    allowed_tools: repo.allowed_tools || [],
    branch_prefix: repo.branch_prefix || "kaka",
  };
}

function splitList(value: string): string[] {
  return value
    .replace(/\n/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseNotify(value: string): {
  notify_platform: string;
  notify_external_id: string;
  notify_channel_id: string;
} {
  if (!value) {
    return { notify_platform: "", notify_external_id: "", notify_channel_id: "" };
  }
  const [platform, externalId] = value.split(":", 2);
  return {
    notify_platform: platform || "",
    notify_external_id: externalId || "",
    notify_channel_id: externalId || "",
  };
}

function bindingPayload(draft: RepositoryDraft) {
  const threshold = Number(draft.context_trim_threshold_text);
  const contextTrimThreshold = Number.isFinite(threshold) && threshold > 0
    ? Math.trunc(threshold)
    : null;
  const customTools = draft.tool_policy_mode === "custom"
    ? Array.from(new Set(draft.allowed_tools)).sort()
    : [];
  return {
    enabled: draft.enabled,
    agent_name: draft.agent_name,
    trigger_label: draft.trigger_label,
    mention_triggers: splitList(draft.mention_triggers_text),
    ...parseNotify(draft.notify_identity),
    issue_label_enabled: draft.issue_label_enabled,
    issue_comment_enabled: draft.issue_comment_enabled,
    pr_review_comment_enabled: draft.pr_review_comment_enabled,
    repository_instructions: draft.repository_instructions,
    provider_name: draft.provider_name,
    model_name: draft.model_name,
    context_trim_threshold: contextTrimThreshold,
    tool_policy_mode: customTools.length ? "custom" : "inherit",
    allowed_tools: customTools,
    branch_prefix: draft.branch_prefix,
  };
}

function formatDate(value: string | null): string {
  if (!value) {
    return "never";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function repoName(fullName: string): string {
  const parts = fullName.split("/");
  return parts[parts.length - 1] || fullName;
}

function repoOwner(fullName: string): string {
  return fullName.split("/", 1)[0] || "";
}

function taskRepository(task: BackgroundTask): string {
  const value = task.workspace?.metadata?.repository_full_name;
  return typeof value === "string" ? value : "";
}

function taskText(task: BackgroundTask): string {
  const marker = "\nRequest:\n";
  const markerIndex = task.request.indexOf(marker);
  if (markerIndex >= 0) {
    const afterMarker = task.request.slice(markerIndex + marker.length);
    return afterMarker.split("\n\n", 1)[0] || task.request;
  }
  const lines = task.request.split("\n").filter(Boolean);
  return lines[lines.length - 1] || task.request;
}

export function RepositoriesPage() {
  const params = useParams();
  const repositoryId = () => params.repositoryId || "";

  return (
    <Show
      when={repositoryId()}
      keyed
      fallback={<RepositoryListPage />}
    >
      {(id) => <RepositoryDetailPage repositoryId={id} />}
    </Show>
  );
}

function RepositoryListPage() {
  const [data, setData] = createSignal<GitHubPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [statusFilter, setStatusFilter] = createSignal("all");
  const [ownerFilter, setOwnerFilter] = createSignal("all");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<GitHubPayload>("/dashboard-api/github"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load repositories");
    }
  };

  const sync = async () => {
    try {
      await postJson("/dashboard-api/github/sync");
      showToast("GitHub repositories synced.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to sync GitHub", "error");
    }
  };

  const ownerOptions = createMemo(() => {
    const owners = new Set((data()?.repositories || []).map((repo) => repo.account_login || repoOwner(repo.full_name)));
    return [
      { value: "all", label: "All owners" },
      ...Array.from(owners)
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b))
        .map((owner) => ({ value: owner, label: owner })),
    ];
  });

  const filteredRepositories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    const owner = ownerFilter();
    const status = statusFilter();
    return payload.repositories.filter((repo) => {
      if (owner !== "all" && (repo.account_login || repoOwner(repo.full_name)) !== owner) {
        return false;
      }
      if (status === "enabled" && !repo.enabled) {
        return false;
      }
      if (status === "disabled" && repo.enabled) {
        return false;
      }
      if (status === "private" && !repo.private) {
        return false;
      }
      if (status === "public" && repo.private) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return [
        repo.full_name,
        repo.account_login,
        repo.default_branch,
        repo.private ? "private" : "public",
        repo.enabled ? "enabled" : "disabled",
        repo.agent_name,
        repo.trigger_label,
        repo.mention_triggers.join(" "),
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  });

  const activityFor = (repo: GitHubRepositoryBinding) =>
    data()?.repository_activity?.[String(repo.repository_id)] || {
      active_count: 0,
      recent_count: 0,
      tasks: [],
    };

  onMount(load);

  return (
    <AppShell
      title="Repositories"
      subtitle="Review synced GitHub repositories and tune repo-specific automation."
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search repositories..."
              onSearchInput={setQuery}
              actions={
                <>
                  <SelectControl
                    class="repository-toolbar-select"
                    value={ownerFilter()}
                    options={ownerOptions()}
                    onChange={setOwnerFilter}
                    ariaLabel="Owner filter"
                  />
                  <SelectControl
                    class="repository-toolbar-select"
                    value={statusFilter()}
                    options={[
                      { value: "all", label: "All states" },
                      { value: "enabled", label: "Enabled" },
                      { value: "disabled", label: "Disabled" },
                      { value: "private", label: "Private" },
                      { value: "public", label: "Public" },
                    ]}
                    onChange={setStatusFilter}
                    ariaLabel="Repository status filter"
                  />
                  <button class="btn btn-primary" type="button" onClick={sync}>
                    <GitPullRequest size={14} />
                    Sync GitHub
                  </button>
                </>
              }
            />

            <section class="panel repository-list-panel">
              <div class="panel-header">
                <div class="panel-title">Synced repositories</div>
                <span class="hint">
                  Showing {filteredRepositories().length} of {payload.repositories.length}
                </span>
              </div>
              <div class="repository-list">
                <For
                  each={filteredRepositories()}
                  fallback={
                    <div class="empty">
                      {query().trim() ? "No repositories found." : "No repositories synced."}
                    </div>
                  }
                >
                  {(repo) => {
                    const activity = () => activityFor(repo);
                    return (
                      <A
                        href={`/repositories/${repo.repository_id}`}
                        class="repository-row"
                      >
                        <div class="repository-row-main">
                          <div class="repository-row-icon" data-enabled={repo.enabled}>
                            <GitBranch size={16} />
                          </div>
                          <div class="repository-row-title">
                            <div class="repository-row-name">
                              <span class="muted">{repoOwner(repo.full_name)}/</span>
                              <span>{repoName(repo.full_name)}</span>
                            </div>
                            <div class="chips">
                              <span class="chip">{repo.default_branch}</span>
                              <span class="chip">{repo.private ? "private" : "public"}</span>
                              <span class="chip">agent: {repo.agent_name || payload.default_agent}</span>
                            </div>
                          </div>
                        </div>
                        <div class="repository-row-meta">
                          <span class={`badge ${repo.enabled ? "badge-success" : "badge-warning"}`}>
                            {repo.enabled ? "enabled" : "disabled"}
                          </span>
                          <span class="badge">trigger: {repo.trigger_label || payload.trigger_label}</span>
                          <span class={activity().active_count ? "badge badge-info" : "badge"}>
                            {activity().active_count
                              ? `${activity().active_count} active`
                              : `${activity().recent_count} recent`}
                          </span>
                          <span class="hint">synced {formatDate(repo.last_synced_at)}</span>
                        </div>
                      </A>
                    );
                  }}
                </For>
              </div>
            </section>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}

function RepositoryDetailPage(props: { repositoryId: string }) {
  const navigate = useNavigate();
  const [data, setData] = createSignal<GitHubRepositoryDetailPayload>();
  const [draft, setDraft] = createSignal<RepositoryDraft | null>(null);
  const [error, setError] = createSignal("");
  const [activeTab, setActiveTab] = createSignal<RepositoryTab>("overview");
  const [runDialogOpen, setRunDialogOpen] = createSignal(false);
  const [taskRequest, setTaskRequest] = createSignal("");
  const [taskNotify, setTaskNotify] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<GitHubRepositoryDetailPayload>(
        `/dashboard-api/github/repositories/${encodeURIComponent(props.repositoryId)}`,
      );
      setData(payload);
      const nextDraft = toDraft(payload.repository);
      setDraft(nextDraft);
      setTaskNotify(nextDraft.notify_identity);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load repository");
    }
  };

  const updateDraft = <K extends keyof RepositoryDraft>(key: K, value: RepositoryDraft[K]) => {
    setDraft((current) => current && { ...current, [key]: value });
  };

  const toggleTool = (tool: string, checked: boolean) => {
    setDraft((current) => {
      if (!current) {
        return current;
      }
      const values = new Set(current.allowed_tools);
      if (checked) {
        values.add(tool);
      } else {
        values.delete(tool);
      }
      return { ...current, allowed_tools: Array.from(values).sort() };
    });
  };

  const save = async () => {
    const current = draft();
    if (!current) {
      return;
    }
    setBusy("save");
    try {
      await putJson(
        `/dashboard-api/github/repositories/${encodeURIComponent(props.repositoryId)}/binding`,
        bindingPayload(current),
      );
      showToast("Repository settings saved.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save repository", "error");
    } finally {
      setBusy("");
    }
  };

  const sync = async () => {
    setBusy("sync");
    try {
      await postJson("/dashboard-api/github/sync");
      showToast("GitHub repositories synced.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to sync GitHub", "error");
    } finally {
      setBusy("");
    }
  };

  const submitTask = async () => {
    const request = taskRequest().trim();
    if (!request) {
      showToast("Enter a task request.", "warning");
      return;
    }
    setBusy("task");
    try {
      const result = await postJson<{ task_id: string }>(
        `/dashboard-api/github/repositories/${encodeURIComponent(props.repositoryId)}/tasks`,
        {
          request,
          ...parseNotify(taskNotify()),
        },
      );
      showToast(`Task ${result.task_id} submitted.`);
      setTaskRequest("");
      setRunDialogOpen(false);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to submit task", "error");
    } finally {
      setBusy("");
    }
  };

  const selectedTools = createMemo(() => new Set(draft()?.allowed_tools || []));
  const visibleAgentNames = createMemo(() => {
    const names = new Set(data()?.agent_names || []);
    const current = draft()?.agent_name;
    if (current) {
      names.add(current);
    }
    return Array.from(names).sort();
  });

  onMount(load);

  return (
    <AppShell
      title={data()?.repository.full_name || "Repository"}
      subtitle="Repo-specific automation, optimization, and activity."
      actions={
        <>
          <button class="btn" type="button" onClick={() => navigate("/repositories")}>
            <ArrowLeft size={14} />
            Back
          </button>
          <button class="btn" type="button" disabled={busy() === "sync"} onClick={sync}>
            <GitPullRequest size={14} />
            {busy() === "sync" ? "Syncing..." : "Sync"}
          </button>
          <button class="btn" type="button" onClick={() => setRunDialogOpen(true)}>
            <Play size={14} />
            Run Task
          </button>
          <button class="btn btn-primary" type="button" disabled={busy() === "save"} onClick={save}>
            <Save size={14} />
            {busy() === "save" ? "Saving..." : "Save"}
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <Show when={draft()} keyed>
            {(currentDraft) => (
              <div class="stack repository-detail">
                <div class="repository-detail-banner panel">
                  <div class="repository-detail-identity">
                    <div class="repository-row-icon" data-enabled={currentDraft.enabled}>
                      <GitBranch size={18} />
                    </div>
                    <div>
                      <div class="repository-detail-title">{payload.repository.full_name}</div>
                      <div class="chips">
                        <span class="chip">{payload.repository.default_branch}</span>
                        <span class="chip">{payload.repository.private ? "private" : "public"}</span>
                        <span class="chip">installation {payload.repository.installation_id}</span>
                      </div>
                    </div>
                  </div>
                  <div class="row-wrap">
                    <span class={`badge ${currentDraft.enabled ? "badge-success" : "badge-warning"}`}>
                      {currentDraft.enabled ? "enabled" : "disabled"}
                    </span>
                    <span class="badge">agent: {currentDraft.agent_name}</span>
                    <span class={payload.activity.active_count ? "badge badge-info" : "badge"}>
                      {payload.activity.active_count} active
                    </span>
                  </div>
                </div>

                <div class="tab-bar">
                  <For
                    each={[
                      { key: "overview", label: "Overview", icon: <Settings2 size={13} /> },
                      { key: "automation", label: "Automation", icon: <Bot size={13} /> },
                      { key: "optimization", label: "Optimization", icon: <SlidersHorizontal size={13} /> },
                      { key: "activity", label: "Activity", icon: <MessageSquare size={13} /> },
                    ] as { key: RepositoryTab; label: string; icon: JSX.Element }[]}
                  >
                    {(tab) => (
                      <button
                        class={`btn btn-sm ${activeTab() === tab.key ? "btn-primary" : ""}`}
                        type="button"
                        onClick={() => setActiveTab(tab.key)}
                      >
                        {tab.icon}
                        {tab.label}
                      </button>
                    )}
                  </For>
                </div>

                <Show when={activeTab() === "overview"}>
                  <RepositoryOverview
                    repository={payload.repository}
                    draft={currentDraft}
                    activity={payload.activity}
                  />
                </Show>

                <Show when={activeTab() === "automation"}>
                  <RepositoryAutomation
                    draft={currentDraft}
                    identities={payload.identities}
                    agentNames={visibleAgentNames()}
                    onChange={updateDraft}
                  />
                </Show>

                <Show when={activeTab() === "optimization"}>
                  <RepositoryOptimization
                    payload={payload}
                    draft={currentDraft}
                    selectedTools={selectedTools()}
                    onChange={updateDraft}
                    onToggleTool={toggleTool}
                  />
                </Show>

                <Show when={activeTab() === "activity"}>
                  <RepositoryActivity tasks={payload.activity.tasks} />
                </Show>

                <Dialog
                  open={runDialogOpen()}
                  title={`Run task in ${payload.repository.full_name}`}
                  onClose={() => setRunDialogOpen(false)}
                  footer={
                    <>
                      <button class="btn" type="button" onClick={() => setRunDialogOpen(false)}>
                        Close
                      </button>
                      <button
                        class="btn btn-primary"
                        type="button"
                        disabled={busy() === "task" || !taskRequest().trim()}
                        onClick={submitTask}
                      >
                        <Play size={14} />
                        {busy() === "task" ? "Submitting..." : "Run Task"}
                      </button>
                    </>
                  }
                >
                  <div class="stack">
                    <div class="field">
                      <label>Request</label>
                      <textarea
                        class="textarea"
                        rows={5}
                        value={taskRequest()}
                        placeholder="Describe what the agent should do in this repository..."
                        onInput={(event) => setTaskRequest(event.currentTarget.value)}
                      />
                    </div>
                    <div class="field">
                      <label>Notification</label>
                      <IdentityPicker
                        value={taskNotify()}
                        onChange={setTaskNotify}
                        identities={payload.identities}
                      />
                    </div>
                  </div>
                </Dialog>
              </div>
            )}
          </Show>
        )}
      </DataGate>
    </AppShell>
  );
}

function RepositoryOverview(props: {
  repository: GitHubRepositoryBinding;
  draft: RepositoryDraft;
  activity: { active_count: number; recent_count: number; tasks: BackgroundTask[] };
}) {
  return (
    <div class="grid-3 repository-overview-grid">
      <section class="panel metric">
        <div class="metric-value">{props.draft.enabled ? "On" : "Off"}</div>
        <div class="metric-label">Automation</div>
      </section>
      <section class="panel metric">
        <div class="metric-value">{props.activity.recent_count}</div>
        <div class="metric-label">Recent tasks</div>
      </section>
      <section class="panel metric">
        <div class="metric-value">{props.activity.active_count}</div>
        <div class="metric-label">Active tasks</div>
      </section>
      <section class="panel repository-overview-card">
        <div class="panel-header">
          <div class="panel-title">Automation Summary</div>
        </div>
        <div class="panel-body stack">
          <div class="repository-summary-row">
            <span class="hint">Agent</span>
            <span class="mono">{props.draft.agent_name}</span>
          </div>
          <div class="repository-summary-row">
            <span class="hint">Trigger label</span>
            <span class="mono">{props.draft.trigger_label}</span>
          </div>
          <div class="repository-summary-row">
            <span class="hint">Mentions</span>
            <span class="mono">{props.draft.mention_triggers_text || "none"}</span>
          </div>
          <div class="repository-summary-row">
            <span class="hint">Branch prefix</span>
            <span class="mono">{props.draft.branch_prefix || "kaka"}</span>
          </div>
        </div>
      </section>
      <section class="panel repository-overview-card">
        <div class="panel-header">
          <div class="panel-title">Optimization Summary</div>
        </div>
        <div class="panel-body stack">
          <div class="repository-summary-row">
            <span class="hint">Model override</span>
            <span class="mono">
              {props.draft.provider_name || props.draft.model_name
                ? `${props.draft.provider_name || "default"}/${props.draft.model_name || "provider default"}`
                : "agent default"}
            </span>
          </div>
          <div class="repository-summary-row">
            <span class="hint">Context trim</span>
            <span class="mono">{props.draft.context_trim_threshold_text || "agent default"}</span>
          </div>
          <div class="repository-summary-row">
            <span class="hint">Tool policy</span>
            <span class="mono">
              {props.draft.tool_policy_mode === "custom"
                ? `${props.draft.allowed_tools.length} custom tools`
                : "inherit agent"}
            </span>
          </div>
        </div>
      </section>
      <section class="panel repository-overview-card">
        <div class="panel-header">
          <div class="panel-title">Repository Instructions</div>
        </div>
        <div class="panel-body">
          <Show
            when={props.draft.repository_instructions.trim()}
            fallback={<div class="empty">No repo-specific instructions.</div>}
          >
            <p class="repository-instructions-preview">
              {truncateText(props.draft.repository_instructions, 480)}
            </p>
          </Show>
        </div>
      </section>
    </div>
  );
}

function RepositoryAutomation(props: {
  draft: RepositoryDraft;
  identities: Identity[];
  agentNames: string[];
  onChange: <K extends keyof RepositoryDraft>(key: K, value: RepositoryDraft[K]) => void;
}) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Automation</div>
      </div>
      <div class="panel-body stack">
        <div class="grid-2">
          <label class="repository-toggle-panel">
            <input
              type="checkbox"
              checked={props.draft.enabled}
              onChange={(event) => props.onChange("enabled", event.currentTarget.checked)}
            />
            <span>
              <strong>Enable repository automation</strong>
              <span class="hint">Allow webhook events to submit agent work for this repo.</span>
            </span>
          </label>
          <div class="field">
            <label>Agent</label>
            <SelectControl
              value={props.draft.agent_name}
              options={props.agentNames.map((agent) => ({ value: agent, label: agent }))}
              onChange={(value) => props.onChange("agent_name", value)}
              ariaLabel="Repository agent"
              icon={<Bot size={14} />}
            />
          </div>
        </div>

        <div class="grid-2">
          <div class="field">
            <label>Issue label trigger</label>
            <input
              class="input"
              value={props.draft.trigger_label}
              onInput={(event) => props.onChange("trigger_label", event.currentTarget.value)}
            />
          </div>
          <div class="field">
            <label>Mention triggers</label>
            <input
              class="input"
              value={props.draft.mention_triggers_text}
              onInput={(event) => props.onChange("mention_triggers_text", event.currentTarget.value)}
            />
          </div>
        </div>

        <div class="grid-3">
          <label class="repository-toggle-panel">
            <input
              type="checkbox"
              checked={props.draft.issue_label_enabled}
              onChange={(event) => props.onChange("issue_label_enabled", event.currentTarget.checked)}
            />
            <span>
              <strong>Issue label</strong>
              <span class="hint">Run when an issue has the trigger label.</span>
            </span>
          </label>
          <label class="repository-toggle-panel">
            <input
              type="checkbox"
              checked={props.draft.issue_comment_enabled}
              onChange={(event) => props.onChange("issue_comment_enabled", event.currentTarget.checked)}
            />
            <span>
              <strong>Issue comment</strong>
              <span class="hint">Run when a comment includes a mention trigger.</span>
            </span>
          </label>
          <label class="repository-toggle-panel">
            <input
              type="checkbox"
              checked={props.draft.pr_review_comment_enabled}
              onChange={(event) => props.onChange("pr_review_comment_enabled", event.currentTarget.checked)}
            />
            <span>
              <strong>PR review comment</strong>
              <span class="hint">Run on pull request review feedback.</span>
            </span>
          </label>
        </div>

        <div class="field">
          <label>Completion notification</label>
          <IdentityPicker
            value={props.draft.notify_identity}
            onChange={(value) => props.onChange("notify_identity", value)}
            identities={props.identities}
          />
        </div>
      </div>
    </section>
  );
}

function RepositoryOptimization(props: {
  payload: GitHubRepositoryDetailPayload;
  draft: RepositoryDraft;
  selectedTools: Set<string>;
  onChange: <K extends keyof RepositoryDraft>(key: K, value: RepositoryDraft[K]) => void;
  onToggleTool: (tool: string, checked: boolean) => void;
}) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Optimization</div>
      </div>
      <div class="panel-body stack">
        <div class="field">
          <label>Repository instructions</label>
          <textarea
            class="textarea repository-instructions-input"
            value={props.draft.repository_instructions}
            placeholder="Add repo-specific conventions, test commands, release rules, or review preferences..."
            onInput={(event) => props.onChange("repository_instructions", event.currentTarget.value)}
          />
        </div>

        <div class="grid-2">
          <div class="field">
            <label>Model override</label>
            <ModelPicker
              catalogs={props.payload.model_catalogs}
              providerNames={props.payload.provider_names}
              defaultProvider={props.payload.default_provider}
              defaultModel={props.payload.default_model}
              provider={props.draft.provider_name}
              model={props.draft.model_name}
              onChange={(provider, model) => {
                props.onChange("provider_name", provider === "default" ? "" : provider);
                props.onChange("model_name", model);
              }}
            />
            <Show when={props.payload.model_catalog_error}>
              <div class="hint">{props.payload.model_catalog_error}</div>
            </Show>
          </div>
          <div class="field">
            <label>Context trim threshold</label>
            <input
              class="input"
              type="number"
              min="1"
              value={props.draft.context_trim_threshold_text}
              placeholder="Agent default"
              onInput={(event) => props.onChange("context_trim_threshold_text", event.currentTarget.value)}
            />
          </div>
        </div>

        <div class="grid-2">
          <div class="field">
            <label>Branch prefix</label>
            <input
              class="input"
              value={props.draft.branch_prefix}
              placeholder="kaka"
              onInput={(event) => props.onChange("branch_prefix", event.currentTarget.value)}
            />
          </div>
          <div class="field">
            <label>Tool policy</label>
            <div class="repository-segmented">
              <button
                class={`btn btn-sm ${props.draft.tool_policy_mode === "inherit" ? "btn-primary" : ""}`}
                type="button"
                onClick={() => props.onChange("tool_policy_mode", "inherit")}
              >
                Inherit agent
              </button>
              <button
                class={`btn btn-sm ${props.draft.tool_policy_mode === "custom" ? "btn-primary" : ""}`}
                type="button"
                onClick={() => props.onChange("tool_policy_mode", "custom")}
              >
                Custom tools
              </button>
            </div>
          </div>
        </div>

        <Show when={props.draft.tool_policy_mode === "custom"}>
          <div class="agent-config-tools repository-tool-policy">
            <For each={props.payload.tool_groups}>
              {(group) => {
                const checkedCount = () => group.tools.filter((tool) => props.selectedTools.has(tool)).length;
                return (
                  <section class="agent-config-section">
                    <div class="agent-config-section-header">
                      <div>
                        <div class="agent-config-eyebrow">Tools</div>
                        <h3>{group.category}</h3>
                      </div>
                      <span class="badge">{checkedCount()}/{group.tools.length}</span>
                    </div>
                    <div class="agent-config-section-body">
                      <div class="agent-config-option-grid">
                        <For each={group.tools}>
                          {(tool) => {
                            const checked = () => props.selectedTools.has(tool);
                            return (
                              <label class={`agent-config-option ${checked() ? "active" : ""}`}>
                                <input
                                  type="checkbox"
                                  checked={checked()}
                                  onChange={(event) => props.onToggleTool(tool, event.currentTarget.checked)}
                                />
                                <span class="agent-config-option-text mono">{tool}</span>
                              </label>
                            );
                          }}
                        </For>
                      </div>
                    </div>
                  </section>
                );
              }}
            </For>
          </div>
        </Show>
      </div>
    </section>
  );
}

function RepositoryActivity(props: { tasks: BackgroundTask[] }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Recent Activity</div>
      </div>
      <div class="panel-body stack">
        <For each={props.tasks} fallback={<div class="empty">No tasks for this repository yet.</div>}>
          {(task) => (
            <article class="repository-task-row">
              <div>
                <div class="repository-task-title">{truncateText(taskText(task), 180)}</div>
                <div class="chips">
                  <span class="chip">agent: {task.agent_name}</span>
                  <Show when={taskRepository(task)}>
                    <span class="chip">{taskRepository(task)}</span>
                  </Show>
                  <span class="chip">#{task.task_id}</span>
                </div>
              </div>
              <div class="row-wrap repository-task-actions">
                <StatusBadge status={task.status} />
                <span class="badge">{task.elapsed_display}</span>
                <Show when={task.thread_id}>
                  <A class="btn btn-sm" href={`/c/${encodeURIComponent(task.thread_id)}`}>
                    <MessageSquare size={13} />
                    Open Chat
                  </A>
                </Show>
              </div>
            </article>
          )}
        </For>
      </div>
    </section>
  );
}
