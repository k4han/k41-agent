import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Play, RefreshCw, Square, Trash2 } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { MetricCard, MetricsRow } from "@/components/Metrics";
import { IdentityPicker } from "@/components/IdentityPicker";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import type { ActiveSession, AgentConfig, BackgroundTask, Identity } from "@/types";

type TasksPayload = {
  tasks: BackgroundTask[];
  agents: AgentConfig[];
  identities: Identity[];
  sessions: ActiveSession[];
};

type TaskOptionsPayload = {
  tasks: BackgroundTask[];
  agents: AgentConfig[];
  identities: Identity[];
};

type SessionsPayload = {
  sessions: ActiveSession[];
  count: number;
};

const activeTaskStatuses = new Set(["running", "pending"]);

export function TasksPage() {
  const [data, setData] = createSignal<TasksPayload>();
  const [error, setError] = createSignal("");
  const [request, setRequest] = createSignal("");
  const [agentName, setAgentName] = createSignal("default");
  const [notify, setNotify] = createSignal("");
  const [expanded, setExpanded] = createSignal<Record<string, boolean>>({});
  const { showToast } = useToast();
  let timer: number | undefined;

  const load = async () => {
    setError("");
    try {
      const [taskPayload, sessionPayload] = await Promise.all([
        apiFetch<TaskOptionsPayload>("/dashboard-api/tasks"),
        apiFetch<SessionsPayload>("/dashboard-api/sessions"),
      ]);
      setData({ ...taskPayload, sessions: sessionPayload.sessions });
      if (!taskPayload.agents.some((agent) => agent.name === agentName()) && taskPayload.agents[0]) {
        setAgentName(taskPayload.agents[0].name);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    }
  };

  const refreshTasks = async () => {
    try {
      const [taskPayload, sessionPayload] = await Promise.all([
        apiFetch<{ tasks: BackgroundTask[] }>("/tasks/list"),
        apiFetch<SessionsPayload>("/dashboard-api/sessions"),
      ]);
      setData((current) => current && {
        ...current,
        tasks: taskPayload.tasks,
        sessions: sessionPayload.sessions,
      });
    } catch {
      return;
    }
  };

  const submitTask = async () => {
    const text = request().trim();
    if (!text) {
      showToast("Enter a task request.", "warning");
      return;
    }
    const payload: Record<string, string> = {
      request: text,
      agent_name: agentName(),
    };
    if (notify()) {
      const [platform, externalId] = notify().split(":", 2);
      payload.notify_platform = platform;
      payload.notify_external_id = externalId;
    }

    try {
      const result = await postJson<{ task_id: string }>("/tasks", payload);
      setRequest("");
      showToast(`Task ${result.task_id} submitted.`);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to submit task", "error");
    }
  };

  const cancelTask = async (taskId: string) => {
    try {
      await postJson(`/tasks/${encodeURIComponent(taskId)}/cancel`);
      showToast("Task cancelled.");
      await refreshTasks();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to cancel task", "error");
    }
  };

  const removeTask = async (taskId: string) => {
    try {
      await deleteJson(`/tasks/${encodeURIComponent(taskId)}`);
      showToast("Task removed.");
      await refreshTasks();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to remove task", "error");
    }
  };

  const toggleExpanded = (taskId: string) => {
    setExpanded((current) => ({ ...current, [taskId]: !current[taskId] }));
  };

  onMount(() => {
    void load();
    timer = window.setInterval(refreshTasks, 5000);
  });
  onCleanup(() => {
    if (timer) {
      window.clearInterval(timer);
    }
  });

  return (
    <AppShell
      title="Background Tasks"
      subtitle="Submit long-running agent work and inspect task history."
      actions={
        <button class="btn" type="button" onClick={load}>
          <RefreshCw size={14} />
          Refresh
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => {
          const active = payload.tasks.filter((task) => activeTaskStatuses.has(task.status)).length;
          const completed = payload.tasks.filter((task) => task.status === "completed").length;
          const failed = payload.tasks.filter((task) => ["failed", "cancelled"].includes(task.status)).length;
          const liveSessionsByThread = new Map(
            payload.sessions.map((session) => [session.thread_id, session]),
          );
          return (
            <div class="stack">
              <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">New Task</div>
                </div>
                <div class="panel-body stack">
                  <textarea
                    class="textarea"
                    rows={3}
                    value={request()}
                    placeholder="Describe what the agent should do..."
                    onInput={(event) => setRequest(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.ctrlKey && event.key === "Enter") {
                        event.preventDefault();
                        void submitTask();
                      }
                    }}
                  />
                  <div class="row-wrap">
                    <select
                      class="select"
                      style={{ width: "220px" }}
                      value={agentName()}
                      onChange={(event) => setAgentName(event.currentTarget.value)}
                    >
                      <For each={payload.agents}>
                        {(agent) => <option value={agent.name}>{agent.display_name || agent.name}</option>}
                      </For>
                    </select>
                    <IdentityPicker
                      value={notify()}
                      onChange={setNotify}
                      identities={payload.identities}
                    />
                    <button class="btn btn-primary" type="button" onClick={submitTask}>
                      <Play size={14} />
                      Run Task
                    </button>
                  </div>
                </div>
              </section>

              <MetricsRow>
                <MetricCard value={active} label="Active" />
                <MetricCard value={completed} label="Completed" />
                <MetricCard value={failed} label="Failed or cancelled" />
              </MetricsRow>

              <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">Task History</div>
                  <span class="hint">Auto-refresh every 5s</span>
                </div>
                <div class="panel-body stack">
                  <For each={payload.tasks} fallback={<div class="empty">No tasks yet.</div>}>
                    {(task) => {
                      const isActive = activeTaskStatuses.has(task.status);
                      const hasDetails = Boolean(task.result || task.error);
                      const liveSession = liveSessionsByThread.get(task.thread_id);
                      return (
                        <article class="panel">
                          <div class="panel-body stack">
                            <div class="split">
                              <div>
                                <div class="mono">#{task.task_id}</div>
                                <div>{truncateText(task.request, 220)}</div>
                                <div class="hint">
                                  {task.agent_name} - {task.thread_id}
                                </div>
                              </div>
                              <div class="row-wrap">
                                <StatusBadge status={task.status} />
                                <span class="badge">{task.elapsed_display}</span>
                              </div>
                            </div>
                            <Show
                              when={liveSession}
                              fallback={
                                isActive ? (
                                  <div class="task-live task-live-muted">
                                    <span class="badge">Live session</span>
                                    <span class="hint">Waiting for runtime details...</span>
                                  </div>
                                ) : null
                              }
                            >
                              {(session) => (
                                <div class="task-live">
                                  <div class="task-live-header">
                                    <div class="row-wrap">
                                      <span class="badge badge-info">Live session</span>
                                      <span class="badge">{session().elapsed_display}</span>
                                    </div>
                                    <span class="hint">Auto-refresh every 5s</span>
                                  </div>
                                  <div class="task-live-grid">
                                    <div class="task-live-cell">
                                      <div class="hint">Step</div>
                                      <div class="mono task-live-value">{session().current_step}</div>
                                    </div>
                                    <div class="task-live-cell">
                                      <div class="hint">Session</div>
                                      <div class="mono task-live-value">
                                        {truncateText(session().session_id, 28)}
                                      </div>
                                    </div>
                                    <div class="task-live-cell">
                                      <div class="hint">Origin</div>
                                      <div class="task-live-value">
                                        {session().platform} - {session().user_id}
                                      </div>
                                    </div>
                                  </div>
                                  <div class="chips">
                                    <For each={session().tools_called} fallback={<span class="chip">no tools yet</span>}>
                                      {(tool) => <span class="chip">{tool}</span>}
                                    </For>
                                  </div>
                                </div>
                              )}
                            </Show>
                            <div class="row-wrap">
                              <Show when={hasDetails}>
                                <button class="btn btn-sm" type="button" onClick={() => toggleExpanded(task.task_id)}>
                                  {expanded()[task.task_id] ? "Hide" : "Show"} Details
                                </button>
                              </Show>
                              <Show
                                when={isActive}
                                fallback={
                                  <button
                                    class="btn btn-sm btn-danger"
                                    type="button"
                                    onClick={() => removeTask(task.task_id)}
                                  >
                                    <Trash2 size={13} />
                                    Remove
                                  </button>
                                }
                              >
                                <button
                                  class="btn btn-sm btn-warning"
                                  type="button"
                                  onClick={() => cancelTask(task.task_id)}
                                >
                                  <Square size={13} />
                                  Cancel
                                </button>
                              </Show>
                            </div>
                            <Show when={expanded()[task.task_id] && hasDetails}>
                              <pre class="code-block">{task.result || task.error}</pre>
                            </Show>
                          </div>
                        </article>
                      );
                    }}
                  </For>
                </div>
              </section>
            </div>
          );
        }}
      </DataGate>
    </AppShell>
  );
}
