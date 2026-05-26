import { createSignal, onMount, Show } from "solid-js";
import { GitPullRequest } from "lucide-solid";

import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import type { GitHubPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

export function ConnectionsPage() {
  const [data, setData] = createSignal<GitHubPayload>();
  const [error, setError] = createSignal("");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<GitHubPayload>("/dashboard-api/github"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load connections");
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

  onMount(load);

  return (
    <SettingsLayout
      title="Connections"
      subtitle="Manage external service connections."
      breadcrumbLabel="Connections"
      contentWidth="wide"
      actions={
        <button class="btn btn-primary" type="button" onClick={sync}>
          <GitPullRequest size={14} />
          Sync GitHub
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <section class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">GitHub App</div>
                <div class="hint">Repository automation through GitHub webhooks.</div>
              </div>
              <div class="row-wrap">
                <span class={payload.enabled ? "badge badge-success" : "badge badge-warning"}>
                  {payload.enabled ? "enabled" : "disabled"}
                </span>
                <span class={payload.configured ? "badge badge-success" : "badge badge-warning"}>
                  {payload.configured ? "configured" : "missing credentials"}
                </span>
                <Show when={payload.install_url}>
                  <a class="btn btn-sm" href={payload.install_url} target="_blank" rel="noreferrer">
                    Install App
                  </a>
                </Show>
              </div>
            </div>
            <div class="panel-body stack">
              <div class="grid-3">
                <div class="field">
                  <label>App Slug</label>
                  <input class="input mono" readOnly value={payload.app_slug || "Not set"} />
                </div>
                <div class="field">
                  <label>Default Agent</label>
                  <input class="input mono" readOnly value={payload.default_agent} />
                </div>
                <div class="field">
                  <label>Synced Repositories</label>
                  <input class="input mono" readOnly value={String(payload.repositories.length)} />
                </div>
              </div>
              <div class="field">
                <label>Webhook URL</label>
                <input class="input mono" readOnly value={payload.webhook_url} />
              </div>
            </div>
          </section>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
