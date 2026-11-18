import "@/styles/browser-settings.css";

import { batch, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { ChevronDown, Download, Globe, RefreshCw, RotateCcw, ShieldCheck } from "lucide-solid";

import { DataGate } from "@/components/State";
import { BrowserProfiles } from "@/components/BrowserProfiles";
import { FormInput } from "@/components/FormInput";
import { apiFetch, postJson } from "@/lib/api";
import { SettingsLayout } from "./SettingsLayout";
import { SettingRow, SettingsPendingBar, useSettingsData } from "./shared";

type SetupAction = "install" | "repair" | "check";
const EXECUTABLE_PATH = "browser.executable_path";
const STREAM_QUALITY = "browser.stream_quality";
const STREAM_WIDTH = "browser.stream_max_width";
const STREAM_HEIGHT = "browser.stream_max_height";
const PRIMARY_SETTINGS = new Set([EXECUTABLE_PATH, STREAM_QUALITY, STREAM_WIDTH, STREAM_HEIGHT]);
const STREAM_SIZES = [[800, 600], [1024, 600], [1280, 720], [1600, 900], [1920, 1080]] as const;
type SetupJob = {
  id: string;
  action: SetupAction;
  state: "running" | "succeeded" | "failed";
  configured_executable_path: string;
  checked_executable_path?: string | null;
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
  const executablePathDirty = () => pendingChanges().some(change => change.key === EXECUTABLE_PATH);
  const actionsDisabled = () => busy() || saving() || !setup() || executablePathDirty();
  const previousJob = createMemo(() => {
    const value = setup();
    const job = value?.job;
    if (!job || job.state === "running") return false;
    return job.configured_executable_path !== value.configured_executable_path ||
      (job.checked_executable_path !== undefined && job.checked_executable_path !== value.executable_path);
  });
  const currentJobFailed = () => setup()?.job?.state === "failed" && !previousJob();
  const streamWidth = () => Number(drafts()[STREAM_WIDTH] ?? 1280);
  const streamHeight = () => Number(drafts()[STREAM_HEIGHT] ?? 720);
  const streamSizeIndex = createMemo(() => STREAM_SIZES.findIndex(([width, height]) => width === streamWidth() && height === streamHeight()));
  const streamSizeLabel = () => `${streamSizeIndex() < 0 ? "Custom: " : ""}${streamWidth()} × ${streamHeight()}`;
  const streamSliderIndex = createMemo(() => {
    if (streamSizeIndex() >= 0) return streamSizeIndex();
    return STREAM_SIZES.reduce((nearest, [width, height], index) =>
      Math.hypot(width - streamWidth(), height - streamHeight()) <
      Math.hypot(STREAM_SIZES[nearest][0] - streamWidth(), STREAM_SIZES[nearest][1] - streamHeight()) ? index : nearest, 0);
  });
  const selectStreamSize = (index: number) => batch(() => {
    const [width, height] = STREAM_SIZES[index];
    setDraft(STREAM_WIDTH, width);
    setDraft(STREAM_HEIGHT, height);
  });
  const streamSizeDirty = () => pendingChanges().some(change => change.key === STREAM_WIDTH || change.key === STREAM_HEIGHT);
  let disposed = false;
  let revision = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let configurationCard: HTMLDetailsElement | undefined;
  let configurationFields: HTMLFieldSetElement | undefined;

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
    if (saving() || busy()) return;
    const changedKeys = new Set(pendingChanges().map(change => change.key));
    if (!changedKeys.size) return;
    const invalid = Array.from(configurationFields?.querySelectorAll<HTMLInputElement>('input[type="number"]') ?? [])
      .find(input => changedKeys.has(input.name) && !input.validity.valid);
    if (invalid) {
      if (configurationCard) configurationCard.open = true;
      invalid.reportValidity();
      return;
    }
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
    if (currentJobFailed()) return "Last operation failed";
    if (!value.installed) return "Browser not installed";
    if (previousJob()) return "Check required";
    if (value.job?.state === "succeeded" && value.executable_path && value.job.checked_executable_path === value.executable_path) {
      return "Browser ready";
    }
    return "Browser detected";
  });

  onMount(() => { void load(); void refreshSetup(); });
  onCleanup(() => { disposed = true; ++revision; clearTimeout(timer); });

  return (
    <SettingsLayout title="Browser" contentWidth="medium">
      <div class="browser-settings-layout">
        <div class="browser-settings-grid">
          <BrowserProfiles />

          <div class="stack">
            <section class="panel browser-setup-card" aria-busy={busy()}>
              <div class="panel-header">
                <h2 class="panel-title"><Globe size={17} /> Browser setup</h2>
                <button class="btn btn-secondary btn-sm" type="button" disabled={refreshing()} onClick={() => void refresh()}>
                  <RefreshCw size={13} class={refreshing() ? "spinner-animate" : ""} /> Refresh
                </button>
              </div>
              <div class="panel-body stack">
                <div class="browser-setup-status" role="status" aria-live="polite">
                  <span class={`badge ${currentJobFailed() ? "badge-danger" : ""}`}>{statusLabel()}</span>
                  <Show when={setup()?.executable_path}>
                    {path => <code class="browser-setup-path">{path()}</code>}
                  </Show>
                </div>
                <Show when={setupError() || actionError()}>
                  <div class="browser-setup-error" role="alert">{setupError() || actionError()}</div>
                </Show>
                <Show when={setup()}>{(value) => <>
                  <div class="row-wrap browser-setup-actions">
                    <button class="btn btn-secondary btn-sm" type="button" disabled={actionsDisabled()} onClick={() => void start("check")}>
                      <ShieldCheck size={14} /> Check browser
                    </button>
                    <Show when={!value().installed} fallback={
                      <button class="btn btn-secondary btn-sm" type="button" disabled={actionsDisabled() || !value().installation_supported} onClick={() => void start("repair")}>
                        <RotateCcw size={14} /> Reinstall browser
                      </button>
                    }>
                      <button class="btn btn-primary btn-sm" type="button" disabled={actionsDisabled() || !value().installation_supported} onClick={() => void start("install")}>
                        <Download size={14} /> Install browser
                      </button>
                    </Show>
                  </div>
                  <Show when={!value().installation_supported && value().installation_unavailable_reason}>
                    {reason => <p class="hint">{reason()}</p>}
                  </Show>
                  <Show when={!value().installed && value().installation_supported && !busy()}>
                    <p class="hint">Automatic installation needs root or passwordless sudo on the server.</p>
                  </Show>
                  <Show when={value().job}>{(job) =>
                    <div class="browser-setup-result" aria-live="polite">
                      <Show when={previousJob()} fallback={<>
                        <Show when={job().error}><p class="browser-setup-error" role="alert">{job().error}</p></Show>
                        <Show when={job().output}>
                          <details open={currentJobFailed()}>
                            <summary>Operation output</summary>
                            <pre class="browser-setup-output">{job().output}</pre>
                          </details>
                        </Show>
                      </>}>
                        <details>
                          <summary>
                            Previous operation for{" "}
                            <Show when={job().checked_executable_path || job().configured_executable_path} fallback="automatic discovery">
                              {path => <code class="browser-setup-path">{path()}</code>}
                            </Show>
                          </summary>
                          <Show when={job().error}><p class="browser-setup-error">{job().error}</p></Show>
                          <Show when={job().output}><pre class="browser-setup-output">{job().output}</pre></Show>
                        </details>
                      </Show>
                    </div>
                  }</Show>
                </>}</Show>

                <Show when={data()?.settings[EXECUTABLE_PATH]}>{info =>
                  <fieldset class="browser-settings-fields browser-executable" disabled={busy() || saving()}>
                    <SettingRow settingKey={EXECUTABLE_PATH} info={info()} draft={drafts()[EXECUTABLE_PATH]}
                      dirty={executablePathDirty()} onChange={value => setDraft(EXECUTABLE_PATH, value)}
                      onRestore={() => restoreDraft(EXECUTABLE_PATH)}
                      control={<FormInput
                        id="browser-executable-path"
                        name={EXECUTABLE_PATH}
                        ariaLabel={info().label || "Chrome executable"}
                        value={String(drafts()[EXECUTABLE_PATH] ?? "")}
                        onChange={value => setDraft(EXECUTABLE_PATH, value)}
                        placeholder="Automatic discovery"
                      />} />
                    <Show when={executablePathDirty()}>
                      <p class="hint">Save or undo the executable change to run browser setup.</p>
                    </Show>
                  </fieldset>
                }</Show>
              </div>
            </section>

            <DataGate data={data()} error={error()} onRetry={load}>{payload =>
              <section class="panel browser-viewer-card">
                <div class="panel-header">
                  <h2 class="panel-title">Live viewer</h2>
                  <button class="btn btn-ghost btn-sm" type="button" disabled={busy() || saving()}
                    onClick={() => batch(() => { setDraft(STREAM_QUALITY, 50); selectStreamSize(2); })}>
                    <RotateCcw size={13} /> Reset viewer defaults
                  </button>
                </div>
                <fieldset class="browser-settings-fields" disabled={busy() || saving()}>
                  <Show when={payload().settings[STREAM_QUALITY]}>{info =>
                    <SettingRow settingKey={STREAM_QUALITY} info={info()} draft={drafts()[STREAM_QUALITY]}
                      dirty={pendingChanges().some(change => change.key === STREAM_QUALITY)}
                      onChange={value => setDraft(STREAM_QUALITY, value)} onRestore={() => restoreDraft(STREAM_QUALITY)}
                      actions={<output for="browser-stream-quality">{Number(drafts()[STREAM_QUALITY] ?? 50)}%</output>}
                      control={<input id="browser-stream-quality" class="browser-stream-slider" type="range" min="1" max="100" step="1"
                        aria-label="Live image quality" value={Number(drafts()[STREAM_QUALITY] ?? 50)}
                        onInput={event => setDraft(STREAM_QUALITY, Number(event.currentTarget.value))} />} />
                  }</Show>
                  <Show when={payload().settings[STREAM_WIDTH]}>{info =>
                    <SettingRow settingKey={STREAM_WIDTH} info={{ ...info(), label: "Maximum live image size" }} draft={drafts()[STREAM_WIDTH]}
                      dirty={streamSizeDirty()} onChange={value => setDraft(STREAM_WIDTH, value)}
                      onRestore={() => batch(() => { restoreDraft(STREAM_WIDTH); restoreDraft(STREAM_HEIGHT); })}
                      actions={<output for="browser-stream-size">{streamSizeLabel()}</output>}
                      control={<>
                        <input id="browser-stream-size" class="browser-stream-slider" type="range" min="0" max="4" step="1" list="browser-stream-size-marks"
                          aria-label="Maximum live image size" aria-valuetext={streamSizeLabel()}
                          value={streamSliderIndex()} onInput={event => selectStreamSize(Number(event.currentTarget.value))} />
                        <datalist id="browser-stream-size-marks">
                          <For each={STREAM_SIZES}>{([width, height], index) => <option value={index()} label={`${width} × ${height}`} />}</For>
                        </datalist>
                      </>} />
                  }</Show>
                </fieldset>
              </section>
            }</DataGate>
          </div>

          <Show when={data()}>{payload =>
            <details ref={configurationCard} class="panel browser-configuration-card">
              <summary class="panel-header">
                <span class="panel-title">Browser configuration</span>
                <ChevronDown size={15} aria-hidden="true" />
              </summary>
              <fieldset ref={configurationFields} class="browser-settings-fields" disabled={busy() || saving()}>
                <For each={Object.entries(payload().settings).filter(([key]) => !PRIMARY_SETTINGS.has(key))}>{([key, info]) =>
                  <SettingRow settingKey={key} info={info} draft={drafts()[key]}
                    dirty={pendingChanges().some(change => change.key === key)}
                    onChange={value => setDraft(key, info.input_type === "number" && value === "" ? null : value)}
                    onRestore={() => restoreDraft(key)} />
                }</For>
              </fieldset>
            </details>
          }</Show>
        </div>
      </div>
      <Show when={data()}>{payload =>
        <SettingsPendingBar count={pendingChanges().length} saving={saving()} disabled={busy()}
          restartRequired={pendingChanges().some(change => payload().settings[change.key]?.restart_required)}
          onDiscard={discardAll} onSave={() => void save()} />
      }</Show>
    </SettingsLayout>
  );
}
