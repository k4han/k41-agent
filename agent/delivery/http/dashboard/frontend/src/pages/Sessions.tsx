import { createSignal, For, onCleanup, onMount } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { MetricCard, MetricsRow } from "@/components/Metrics";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { apiFetch } from "@/lib/api";
import type { ActiveSession } from "@/types";

type SessionsPayload = {
  sessions: ActiveSession[];
  count: number;
};

const ACTIVE_SESSION_POLL_INTERVAL_MS = 5000;
const IDLE_SESSION_POLL_INTERVAL_MS = 30000;

export function SessionsPage() {
  const [data, setData] = createSignal<SessionsPayload>();
  const [error, setError] = createSignal("");
  let timer: number | undefined;
  let loading = false;
  let disposed = false;

  const clearRefreshTimer = () => {
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timer = undefined;
    }
  };

  const scheduleRefresh = (payload = data()) => {
    clearRefreshTimer();
    if (disposed || document.hidden) {
      return;
    }

    const delay = payload && payload.count > 0
      ? ACTIVE_SESSION_POLL_INTERVAL_MS
      : IDLE_SESSION_POLL_INTERVAL_MS;

    timer = window.setTimeout(() => {
      timer = undefined;
      void load();
    }, delay);
  };

  const load = async () => {
    if (disposed || loading) {
      return;
    }

    loading = true;
    clearRefreshTimer();
    setError("");
    try {
      const payload = await apiFetch<SessionsPayload>("/dashboard-api/sessions");
      if (disposed) {
        return;
      }

      setData(payload);
      scheduleRefresh(payload);
    } catch (err) {
      if (disposed) {
        return;
      }

      setError(err instanceof Error ? err.message : "Failed to load sessions");
      scheduleRefresh();
    } finally {
      loading = false;
    }
  };

  const handleVisibilityChange = () => {
    if (disposed) {
      return;
    }

    if (document.hidden) {
      clearRefreshTimer();
      return;
    }

    void load();
  };

  onMount(() => {
    document.addEventListener("visibilitychange", handleVisibilityChange);
    void load();
  });
  onCleanup(() => {
    disposed = true;
    clearRefreshTimer();
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  });

  return (
    <AppShell
      title="Active Sessions"
      subtitle="Currently running agent sessions."
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <MetricsRow>
              <MetricCard value={payload.count} label="Active sessions" />
              <MetricCard
                value={payload.sessions.reduce((total, session) => total + session.tools_called.length, 0)}
                label="Recorded tool calls"
              />
              <MetricCard
                value={payload.sessions.filter((session) => session.current_step.startsWith("tool:")).length}
                label="Using tools"
              />
            </MetricsRow>
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
                      fallback={<EmptyTableRow colSpan={6} message="No active sessions." />}
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
