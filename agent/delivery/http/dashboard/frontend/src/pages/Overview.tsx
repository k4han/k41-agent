import { createSignal, For, onMount } from "solid-js";
import { RefreshCw, Square, Play } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import { statusBadgeClass } from "@/lib/utils";
import type { ServiceStatus } from "@/types";

type OverviewPayload = {
  services: ServiceStatus[];
};

export function OverviewPage() {
  const [data, setData] = createSignal<OverviewPayload>();
  const [error, setError] = createSignal("");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<OverviewPayload>("/dashboard-api/overview"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load overview");
    }
  };

  const serviceAction = async (name: string, action: "start" | "stop") => {
    try {
      await postJson(`/services/${encodeURIComponent(name)}/${action}`);
      showToast(`Service ${action} requested.`);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Service action failed", "error");
    }
  };

  const allAction = async (action: "start-all" | "stop-all") => {
    try {
      const result = await postJson<OverviewPayload>(`/services/${action}`);
      setData(result);
      showToast("Service state updated.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Service action failed", "error");
    }
  };

  onMount(load);

  return (
    <AppShell
      title="Services Overview"
      subtitle="Runtime channels and background services."
      actions={
        <>
          <button class="btn" type="button" onClick={load}>
            <RefreshCw size={14} />
            Refresh
          </button>
          <button class="btn" type="button" onClick={() => allAction("start-all")}>
            <Play size={14} />
            Start All
          </button>
          <button class="btn btn-warning" type="button" onClick={() => allAction("stop-all")}>
            <Square size={14} />
            Stop All
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="grid-3">
              <div class="panel metric">
                <div class="metric-value">{payload.services.length}</div>
                <div class="metric-label">Registered services</div>
              </div>
              <div class="panel metric">
                <div class="metric-value">
                  {payload.services.filter((service) => service.status === "running").length}
                </div>
                <div class="metric-label">Running</div>
              </div>
              <div class="panel metric">
                <div class="metric-value">
                  {payload.services.filter((service) => service.status === "error").length}
                </div>
                <div class="metric-label">Errors</div>
              </div>
            </div>

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Services</div>
              </div>
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Status</th>
                      <th>Error</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={payload.services}
                      fallback={
                        <tr>
                          <td colSpan={4}>
                            <div class="empty">No services registered.</div>
                          </td>
                        </tr>
                      }
                    >
                      {(service) => (
                        <tr>
                          <td class="mono">{service.name}</td>
                          <td>
                            <span class={statusBadgeClass(service.status)}>
                              {service.status}
                            </span>
                          </td>
                          <td class="muted">{service.error || "-"}</td>
                          <td>
                            {service.status === "running" ? (
                              <button
                                class="btn btn-sm btn-warning"
                                type="button"
                                onClick={() => serviceAction(service.name, "stop")}
                              >
                                Stop
                              </button>
                            ) : (
                              <button
                                class="btn btn-sm"
                                type="button"
                                onClick={() => serviceAction(service.name, "start")}
                              >
                                Start
                              </button>
                            )}
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

