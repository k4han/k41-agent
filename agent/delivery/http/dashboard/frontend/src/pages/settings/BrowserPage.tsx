import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Download, Globe, RefreshCw, RotateCcw, ShieldCheck } from "lucide-solid";

import { DataGate } from "@/components/State";
import { apiFetch, postJson } from "@/lib/api";
import { SettingsLayout } from "./SettingsLayout";
import { SettingRow, SettingsPendingBar, SettingsSection, useSettingsData } from "./shared";

type SetupAction = "install" | "repair" | "check";
type SetupJob = {
  id: string;
  action: SetupAction;
  state: "running" | "succeeded" | "failed";
  configured_executable_path: string;
  output: string;
  error: string | null;
};
type BrowserSetup = {
  installed: boolean;
  executable_path: string | null;
  configured_executable_path: string;
  installation_supported: boolean;
  installation_unavailable_reason: string | null;
  job: SetupJob | null;
};

export function BrowserPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft, discardAll, saveChanges } =
    useSettingsData("/dashboard-api/browser/configuration");
  const [setup, setSetup] = createSignal<BrowserSetup>();
  const [setupError, setSetupError] = createSignal("");
  const [actionError, setActionError] = createSignal("");
  const [starting, setStarting] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [refreshing, setRefreshing] = createSignal(false);
  const busy = () => starting() || setup()?.job?.state === "running";
  const actionsDisabled = () => busy() || saving() || !setup() || pendingChanges().length > 0;
  let disposed = false;
  let revision = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const refreshSetup = async () => {
    clearTimeout(timer);
    const current = ++revision;
    try {
      const payload = await apiFetch<BrowserSetup>("/dashboard-api/browser/setup");
      if (disposed || current !== revision) return;
      setSetup(payload);
      setSetupError("");
    } catch (err) {
      if (disposed || current !== revision) return;
      setSetupError(err instanceof Error ? err.message : "Could not load browser status.");
    }
    if (!disposed && current === revision && setup()?.job?.state === "running") {
      timer = setTimeout(() => void refreshSetup(), 1500);
    }
  };

  const refresh = async () => {
    setActionError("");
    setRefreshing(true);
    try { await refreshSetup(); } finally { if (!disposed) setRefreshing(false); }
  };

  const start = async (action: SetupAction) => {
    if (actionsDisabled()) return;
    setStarting(true);
    setActionError("");
    setSetupError("");
    ++revision;
    clearTimeout(timer);
    try {
      const job = await postJson<SetupJob>("/dashboard-api/browser/setup", { action });
      if (disposed) return;
      setSetup(previous => previous ? { ...previous, job } : previous);
    } catch (err) {
      if (!disposed) setActionError(err instanceof Error ? err.message : "Could not start browser setup.");
    } finally {
      if (!disposed) {
        setStarting(false);
        // Also refresh after a conflict so a job started in another tab is shown.
        await refreshSetup();
      }
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await saveChanges();
      await refreshSetup();
    } finally { if (!disposed) setSaving(false); }
  };

  const statusLabel = createMemo(() => {
    const value = setup();
    if (!value) return "Loading browser status…";
    if (value.job?.state === "running") {
      return value.job.action === "check" ? "Checking browser…" : "Installing Chrome…";
    }
    if (value.job?.state === "failed") return "Last operation failed";
    if (value.installed && value.job?.state === "succeeded" && value.job.configured_executable_path === value.configured_executable_path) {
      return "Browser ready";
    }
    return value.installed ? "Browser detected" : "Browser not installed";
  });

  onMount(() => { void load(); void refreshSetup(); });
  onCleanup(() => { disposed = true; ++revision; clearTimeout(timer); });

  return (
    <SettingsLayout title="Browser" contentWidth="medium">
      <div class="stack settings-page-stack">
        <div class="settings-page-heading">
          <p class="settings-page-description">
            Install and configure the browser used by your agents. Browser automation runs on the agent server.
          </p>
        </div>
        <section class="panel settings-section-card browser-setup-card" aria-busy={busy()}>
          <div class="panel-header split">
            <div class="panel-title"><Globe size={17} /> Browser setup</div>
            <button class="btn btn-secondary btn-sm" type="button" disabled={refreshing()} onClick={() => void refresh()}>
              <RefreshCw size={13} class={refreshing() ? "spinner-animate" : ""} /> Refresh
            </button>
          </div>
          <div class="panel-body stack">
            <div class="browser-setup-status" role="status" aria-live="polite">
              <span class={`badge ${setup()?.job?.state === "failed" ? "badge-danger" : ""}`}>{statusLabel()}</span>
              <Show when={setup()?.executable_path}>
                <code class="browser-setup-path">{setup()?.executable_path}</code>
              </Show>
            </div>
            <Show when={setupError() || actionError()}>
              <div class="browser-setup-error" role="alert">{setupError() || actionError()}</div>
            </Show>
            <Show when={setup()}>{(value) => <>
              <Show when={!value().installation_supported} fallback={
                <p class="hint">Install official Google Chrome and its required system libraries. The server needs root or passwordless sudo access.</p>
              }>
                <p class="hint">{value().installation_unavailable_reason}</p>
              </Show>
              <div class="row-wrap browser-setup-actions">
                <button class="btn btn-secondary btn-sm" type="button" disabled={actionsDisabled()} onClick={() => void start("check")}>
                  <ShieldCheck size={14} /> Check browser
                </button>
                <Show when={!value().installed} fallback={
                  <button class="btn btn-secondary btn-sm" type="button" disabled={actionsDisabled() || !value().installation_supported} onClick={() => void start("repair")}>
                    <RotateCcw size={14} /> Reinstall Chrome
                  </button>
                }>
                  <button class="btn btn-primary btn-sm" type="button" disabled={actionsDisabled() || !value().installation_supported} onClick={() => void start("install")}>
                    <Download size={14} /> Install Chrome
                  </button>
                </Show>
              </div>
              <Show when={pendingChanges().length > 0}>
                <p class="hint">Save or discard your configuration changes before checking or installing the browser.</p>
              </Show>
              <Show when={value().job}>{(job) =>
                <div class="browser-setup-result" aria-live="polite">
                  <Show when={job().state === "running"}>
                    <p class="hint">This may take a few minutes. You can leave this page and return to view the result.</p>
                  </Show>
                  <Show when={job().error}><p class="browser-setup-error" role="alert">{job().error}</p></Show>
                  <Show when={job().output}>
                    <details open={job().state === "failed"}>
                      <summary>Operation output</summary>
                      <pre class="browser-setup-output">{job().output}</pre>
                    </details>
                  </Show>
                </div>
              }</Show>
            </>}</Show>
          </div>
        </section>

        <DataGate data={data()} error={error()} onRetry={load}>{(payload) => <>
          <SettingsSection title="Browser configuration">
            <fieldset class="browser-settings-fields" disabled={busy() || saving()}>
              <div class="settings-list">
                <For each={Object.entries(payload().settings)}>{([key, info]) =>
                  <SettingRow settingKey={key} info={info} draft={drafts()[key]}
                    dirty={pendingChanges().some(change => change.key === key)}
                    onChange={value => setDraft(key, value)} onRestore={() => restoreDraft(key)} />
                }</For>
              </div>
            </fieldset>
          </SettingsSection>
          <p class="hint">Leave the executable path empty to discover Chrome automatically. Path changes apply to new sessions; changing browser storage requires a server restart.</p>
          <SettingsPendingBar count={pendingChanges().length} saving={saving() || busy()}
            restartRequired={pendingChanges().some(change => payload().settings[change.key]?.restart_required)}
            onDiscard={discardAll} onSave={() => void save()} />
        </>}</DataGate>
      </div>
    </SettingsLayout>
  );
}
