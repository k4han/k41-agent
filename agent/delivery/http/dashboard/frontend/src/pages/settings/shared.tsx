import { createMemo, createSignal, For, JSX, Show } from "solid-js";
import { RotateCcw } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { apiFetch, putJson } from "@/lib/api";
import { formatValue, parseModelList } from "@/lib/utils";
import type { SettingInfo, SettingsPayload } from "@/types";

// --- Types ---------------------------------------------------------------

export type PendingChange = {
  key: string;
  oldValue: unknown;
  newValue: unknown;
};

// --- Helpers -------------------------------------------------------------

export function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function displayDraft(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join("\n");
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function controlInputType(info: SettingInfo): string {
  if (info.input_type === "password") {
    return "password";
  }
  if (info.input_type === "number") {
    return "number";
  }
  if (info.input_type === "url") {
    return "url";
  }
  return "text";
}

function providerNameFromKey(settingKey: string): string | null {
  const match = /^llm\.providers\.([^.]+)\./.exec(settingKey);
  return match?.[1] ?? null;
}

export function categoryLabel(category: string): string {
  return category
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function settingLabel(
  settingKey: string,
  info?: Pick<SettingInfo, "label">,
  options: { trimProviderPrefix?: boolean } = {},
): string {
  const label = info?.label || settingKey;
  if (!options.trimProviderPrefix) {
    return label;
  }

  const providerName = providerNameFromKey(settingKey);
  const providerPrefix = providerName ? `${providerName}: ` : "";
  return providerPrefix && label.startsWith(providerPrefix)
    ? label.slice(providerPrefix.length)
    : label;
}

export function formatSettingValue(info: SettingInfo | undefined, value: unknown): string {
  if (info?.input_type === "password" && value) {
    return "********";
  }
  return formatValue(value);
}

export function typedValue(info: SettingInfo, raw: unknown): unknown {
  if (raw === null) {
    return null;
  }
  if (info.input_type === "boolean") {
    return Boolean(raw);
  }
  const text = String(raw ?? "");
  if (!text.trim()) {
    return null;
  }
  if (info.key.endsWith(".models")) {
    const models = parseModelList(text);
    return models.length ? models : null;
  }
  if (info.input_type === "number") {
    const value = Number(text);
    return Number.isNaN(value) ? null : value;
  }
  return text;
}

export function settingsFromPayload(payload: SettingsPayload): Record<string, SettingInfo> {
  const settings = { ...payload.settings };
  for (const provider of payload.provider_rows || []) {
    for (const entry of Object.values(provider.fields)) {
      settings[entry.key] = entry.info;
    }
  }
  return settings;
}

// --- Hooks ---------------------------------------------------------------

export function useSettingsData(endpoint: string) {
  const [data, setData] = createSignal<SettingsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<SettingsPayload>(endpoint);
      setData(payload);
      const settings = settingsFromPayload(payload);
      setDrafts(
        Object.fromEntries(
          Object.entries(settings).map(([key, info]) => [key, info.value]),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    }
  };

  const pendingChanges = createMemo<PendingChange[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return Object.entries(settingsFromPayload(payload))
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  });

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const restoreDraft = (key: string) => {
    const payload = data();
    const settings = payload ? settingsFromPayload(payload) : {};
    if (!settings[key]) {
      showToast("No server value to restore.", "warning");
      return;
    }
    const current = settings[key].value ?? null;
    setDraft(key, current);
    showToast("Change reverted.", "warning");
  };

  const saveChanges = async (onSuccess?: () => void) => {
    const values = Object.fromEntries(
      pendingChanges().map((change) => [change.key, change.newValue]),
    );
    try {
      await putJson("/settings", { values });
      showToast(`Updated ${pendingChanges().length} setting(s).`);
      onSuccess?.();
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save settings",
        "error",
      );
    }
  };

  return {
    data,
    error,
    drafts,
    load,
    pendingChanges,
    setDraft,
    restoreDraft,
    saveChanges,
  };
}

// --- Components ----------------------------------------------------------

export function SettingControl(props: {
  info: SettingInfo;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  return (
    <Show
      when={props.info.input_type === "boolean"}
      fallback={
        <Show
          when={props.info.key.endsWith(".models")}
          fallback={
            <input
              class="input"
              type={controlInputType(props.info)}
              min={props.info.min}
              max={props.info.max}
              step={props.info.step}
              placeholder="Not set"
              value={displayDraft(props.value)}
              onInput={(event) => props.onChange(event.currentTarget.value)}
            />
          }
        >
          <textarea
            class="textarea mono"
            rows={4}
            value={displayDraft(props.value)}
            onInput={(event) => props.onChange(event.currentTarget.value)}
          />
        </Show>
      }
    >
      <button
        class={`toggle-control ${Boolean(props.value) ? "active" : ""}`}
        type="button"
        role="switch"
        aria-checked={Boolean(props.value)}
        aria-label={props.info.label || props.info.key}
        onClick={() => props.onChange(!Boolean(props.value))}
      >
        <span class="toggle-track">
          <span class="toggle-thumb" />
        </span>
        <span class="toggle-label">{Boolean(props.value) ? "Enabled" : "Disabled"}</span>
      </button>
    </Show>
  );
}

export function SettingsSection(props: {
  title: string;
  description?: JSX.Element;
  actions?: JSX.Element;
  class?: string;
  children: JSX.Element;
}) {
  return (
    <section class={`settings-group ${props.class || ""}`}>
      <div class="settings-section-header">
        <div>
          <div class="settings-section-title">{props.title}</div>
          <Show when={props.description}>
            <div class="hint">{props.description}</div>
          </Show>
        </div>
        <Show when={props.actions}>
          <div class="row-wrap">{props.actions}</div>
        </Show>
      </div>
      {props.children}
    </section>
  );
}

export function SettingRow(props: {
  settingKey: string;
  info: SettingInfo;
  draft: unknown;
  dirty: boolean;
  trimProviderPrefix?: boolean;
  actions?: JSX.Element;
  control?: JSX.Element;
  onChange: (value: unknown) => void;
  onRestore: () => void;
}) {
  return (
    <div class={`setting-card ${props.dirty ? "setting-dirty" : ""}`}>
      <div class="setting-card-main">
        <div class="setting-copy">
          <div class="setting-title-row">
            <div class="setting-title">
              {settingLabel(props.settingKey, props.info, {
                trimProviderPrefix: props.trimProviderPrefix,
              })}
            </div>
            <Show when={props.dirty}>
              <span class="badge badge-warning">Unsaved</span>
            </Show>
            <Show when={props.actions}>
              <div class="setting-inline-actions">{props.actions}</div>
            </Show>
          </div>
          <Show when={props.info.description}>
            <div class="setting-description">{props.info.description}</div>
          </Show>
        </div>
        <div class="setting-control-panel">
          {props.control || (
            <SettingControl info={{ ...props.info, key: props.settingKey }} value={props.draft} onChange={props.onChange} />
          )}
          <Show when={props.dirty}>
            <div class="setting-actions">
              <button class="btn btn-sm" type="button" onClick={props.onRestore}>
                <RotateCcw size={13} />
                Undo
              </button>
            </div>
          </Show>
        </div>
      </div>
    </div>
  );
}

export function ChangesPreview(props: {
  changes: PendingChange[];
  settings: Record<string, SettingInfo>;
}) {
  return (
    <div class="change-list">
      <For each={props.changes}>
        {(change) => {
          const info = () => props.settings[change.key];
          return (
            <div class="change-card">
              <div>
                <div class="setting-title">{settingLabel(change.key, info())}</div>
                <div class="mono hint">{change.key}</div>
              </div>
              <div class="change-values">
                <div>
                  <span class="setting-detail-label">Current</span>
                  <span>{formatSettingValue(info(), change.oldValue)}</span>
                </div>
                <div>
                  <span class="setting-detail-label">New</span>
                  <span>{formatSettingValue(info(), change.newValue)}</span>
                </div>
              </div>
            </div>
          );
        }}
      </For>
    </div>
  );
}

export function SettingsConfirmDialog(props: {
  open: boolean;
  saving?: boolean;
  changes: PendingChange[];
  settings: Record<string, SettingInfo>;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={props.open}
      title="Confirm Changes"
      onClose={props.onClose}
      footer={
        <>
          <button
            class="btn"
            type="button"
            disabled={props.saving}
            onClick={props.onClose}
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={props.saving}
            onClick={props.onConfirm}
          >
            {props.saving ? "Saving..." : "Confirm Save"}
          </button>
        </>
      }
    >
      <div class="stack">
        <p>You are about to update {props.changes.length} setting{props.changes.length === 1 ? "" : "s"}.</p>
        <ChangesPreview changes={props.changes} settings={props.settings} />
      </div>
    </Dialog>
  );
}
