import { batch, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Download, Globe, RefreshCw, RotateCcw, ShieldCheck } from "lucide-solid";

import { DataGate } from "@/components/State";
import { apiFetch, postJson } from "@/lib/api";
import { SettingsLayout } from "./SettingsLayout";
import { SettingRow, SettingsPendingBar, SettingsSection, useSettingsData } from "./shared";

type SetupAction = "install" | "repair" | "check";
const STREAM_QUALITY = "browser.stream_quality";
const STREAM_WIDTH = "browser.stream_max_width";
const STREAM_HEIGHT = "browser.stream_max_height";
const STREAM_KEYS = new Set([STREAM_QUALITY, STREAM_WIDTH, STREAM_HEIGHT]);
const STREAM_SIZES = [[800, 600], [1024, 600], [1280, 720], [1600, 900], [1920, 1080]] as const;
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
          <SettingsSection title="Live viewer" actions={
            <button class="btn btn-ghost btn-sm" type="button" disabled={busy() || saving()}
              onClick={() => batch(() => { setDraft(STREAM_QUALITY, 50); selectStreamSize(2); })}>
              <RotateCcw size={13} /> Reset viewer defaults
            </button>
          }>
            <fieldset class="browser-settings-fields" disabled={busy() || saving()}>
              <div class="settings-list">
                <Show when={payload().settings[STREAM_QUALITY]}>{info =>
                  <SettingRow settingKey={STREAM_QUALITY} info={info()} draft={drafts()[STREAM_QUALITY]}
                    dirty={pendingChanges().some(change => change.key === STREAM_QUALITY)}
                    onChange={value => setDraft(STREAM_QUALITY, value)} onRestore={() => restoreDraft(STREAM_QUALITY)}
                    control={<div class="browser-stream-control">
                      <output for="browser-stream-quality">{Number(drafts()[STREAM_QUALITY] ?? 50)}%</output>
                      <input id="browser-stream-quality" type="range" min="1" max="100" step="1"
                        aria-label="Live image quality" aria-describedby="browser-stream-quality-hint"
                        value={Number(drafts()[STREAM_QUALITY] ?? 50)}
                        onInput={event => setDraft(STREAM_QUALITY, Number(event.currentTarget.value))} />
                      <p class="hint" id="browser-stream-quality-hint">JPEG quality stays at this level, including on slow connections.</p>
                    </div>} />
                }</Show>
                <Show when={payload().settings[STREAM_WIDTH]}>{info =>
                  <SettingRow settingKey={STREAM_WIDTH} info={{ ...info(), label: "Maximum live image size" }} draft={drafts()[STREAM_WIDTH]}
                    dirty={streamSizeDirty()} onChange={value => setDraft(STREAM_WIDTH, value)}
                    onRestore={() => batch(() => { restoreDraft(STREAM_WIDTH); restoreDraft(STREAM_HEIGHT); })}
                    control={<div class="browser-stream-control">
                      <output for="browser-stream-size">{streamSizeLabel()}</output>
                      <input id="browser-stream-size" type="range" min="0" max="4" step="1" list="browser-stream-size-marks"
                        aria-label="Maximum live image size" aria-valuetext={streamSizeLabel()} aria-describedby="browser-stream-size-hint"
                        value={streamSliderIndex()} onInput={event => selectStreamSize(Number(event.currentTarget.value))} />
                      <datalist id="browser-stream-size-marks">
                        <For each={STREAM_SIZES}>{([width, height], index) => <option value={index()} label={`${width} × ${height}`} />}</For>
                      </datalist>
                      <div class="browser-resolution-marks">
                        <For each={STREAM_SIZES}>{([width, height], index) =>
                          <button type="button" aria-label={`Use ${width} × ${height}`} aria-pressed={streamSizeIndex() === index()}
                            onClick={() => selectStreamSize(index())}>{width} × {height}</button>
                        }</For>
                      </div>
                      <p class="hint" id="browser-stream-size-hint">Smaller viewers use smaller images. Larger viewers scale the image locally. Page layout stays unchanged.</p>
                    </div>} />
                }</Show>
              </div>
            </fieldset>
          </SettingsSection>
          <SettingsSection title="Browser configuration">
            <fieldset class="browser-settings-fields" disabled={busy() || saving()}>
              <div class="settings-list">
                <For each={Object.entries(payload().settings).filter(([key]) => !STREAM_KEYS.has(key))}>{([key, info]) =>
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
