import { createSignal, For, onCleanup, onMount } from "solid-js";
import { RefreshCw } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { apiFetch } from "@/lib/api";
import type { ActiveSession } from "@/types";

type SessionsPayload = {
  sessions: ActiveSession[];
  count: number;
};

export function SessionsPage() {
  const [data, setData] = createSignal<SessionsPayload>();
  const [error, setError] = createSignal("");
  let timer: number | undefined;

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<SessionsPayload>("/dashboard-api/sessions"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sessions");
    }
  };

  onMount(() => {
    void load();
    timer = window.setInterval(load, 5000);
  });
  onCleanup(() => {
    if (timer) {
      window.clearInterval(timer);
    }
  });

  return (
    <AppShell
      title="Active Sessions"
      subtitle="Currently running agent sessions."
      actions={
        <button class="btn" type="button" onClick={load}>
          <RefreshCw size={14} />
          Refresh
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="grid-3">
              <div class="panel metric">
                <div class="metric-value">{payload.count}</div>
                <div class="metric-label">Active sessions</div>
              </div>
              <div class="panel metric">
                <div class="metric-value">
                  {payload.sessions.reduce((total, session) => total + session.tools_called.length, 0)}
                </div>
                <div class="metric-label">Recorded tool calls</div>
              </div>
              <div class="panel metric">
                <div class="metric-value">
                  {payload.sessions.filter((session) => session.current_step.startsWith("tool:")).length}
                </div>
                <div class="metric-label">Using tools</div>
              </div>
            </div>
            <section class="panel">
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Thread</th>
                      <th>Agent</th>
                      <th>Platform</th>
                      <th>Step</th>
                      <th>Elapsed</th>
                      <th>Tools</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={payload.sessions}
                      fallback={
                        <tr>
                          <td colSpan={6}>
                            <div class="empty">No active sessions.</div>
                          </td>
                        </tr>
                      }
                    >
                      {(session) => (
                        <tr>
                          <td>
                            <div class="mono">{session.thread_id}</div>
                            <div class="hint">{session.session_id}</div>
                          </td>
                          <td>{session.agent_name}</td>
                          <td>
                            <span class="badge">{session.platform}</span>
                            <div class="hint">{session.user_id}</div>
                          </td>
                          <td class="mono">{session.current_step}</td>
                          <td>{session.elapsed_display}</td>
                          <td>
                            <div class="chips">
                              <For each={session.tools_called} fallback={<span class="chip">none</span>}>
                                {(tool) => <span class="chip">{tool}</span>}
                              </For>
                            </div>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}

