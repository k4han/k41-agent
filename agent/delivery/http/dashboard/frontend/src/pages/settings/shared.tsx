import { createComputed, createMemo, createSignal, For, type JSX, Show, untrack } from "solid-js";
import { createStore, reconcile, unwrap } from "solid-js/store";
import { ArrowRight, Check, RotateCcw, TriangleAlert } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { putJson } from "@/lib/api";
import { useDashboardData } from "@/lib/dashboardData";
import { cloneValue, formatValue, parseModelList, sameJsonValue } from "@/lib/utils";
import type { SettingInfo, SettingsPayload } from "@/types";

// Import new form components
import {
  FormInput,
  FormSelect,
  FormTextarea,
  type ValidationRule,
} from "@/components/forms";

// --- Types ---------------------------------------------------------------

export type PendingChange = {
  key: string;
  oldValue: unknown;
  newValue: unknown;
};

export const RESTART_REQUIRED_NOTICE = "Restart required to apply bootstrap changes.";

// --- Helpers -------------------------------------------------------------

export function sameValue(a: unknown, b: unknown): boolean {
  return sameJsonValue(a, b);
}

function booleanValue(value: unknown): boolean {
  if (typeof value === "string") {
    return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  }
  return Boolean(value);
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

function toolNameFromKey(settingKey: string): string | null {
  const match = /^tools\.([^.]+)\./.exec(settingKey);
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
  options: { trimProviderPrefix?: boolean; trimToolPrefix?: boolean } = {},
): string {
  let label = info?.label || settingKey;
  if (options.trimProviderPrefix) {
    const providerName = providerNameFromKey(settingKey);
    const providerPrefix = providerName ? `${providerName}: ` : "";
    if (providerPrefix && label.startsWith(providerPrefix)) {
      label = label.slice(providerPrefix.length);
    }
  }
  if (options.trimToolPrefix) {
    const toolName = toolNameFromKey(settingKey);
    const toolPrefix = toolName ? `${toolName}: ` : "";
    if (toolPrefix && label.startsWith(toolPrefix)) {
      label = label.slice(toolPrefix.length);
    }
  }
  return label;
}

export function formatSettingValue(info: SettingInfo | undefined, value: unknown): string {
  if (info?.input_type === "password" && value) {
    return "••••••••";
  }
  return formatValue(value);
}

export function typedValue(info: SettingInfo, raw: unknown): unknown {
  if (info.key.startsWith("llm.providers.") && info.key.endsWith(".model_profiles")) {
    if (raw === null || raw === undefined || raw === "") return {};
    if (typeof raw === "object") return raw;
    try { return JSON.parse(String(raw)); } catch { return raw; }
  }
  if (raw === null || raw === undefined) {
    return null;
  }
  if (info.input_type === "boolean") {
    return booleanValue(raw);
  }
  const text = String(raw ?? "");
  if (info.key.endsWith(".models")) {
    const models = parseModelList(text);
    return models.length ? models : Array.isArray(info.value) ? [] : null;
  }
  if (!text.trim()) {
    return typeof info.value === "string" ? "" : null;
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
  const { data, error, load } = useDashboardData<SettingsPayload>(endpoint, { defer: true });
  const [draftState, setDraftState] = createStore<{ values: Record<string, unknown> }>({ values: {} });
  const drafts = () => draftState.values;
  const setDrafts = (values: Record<string, unknown>) =>
    setDraftState("values", reconcile(cloneValue(unwrap(values))));
  const { showToast } = useToast();
  let lastSynced: Record<string, unknown> | undefined;

  // Synchronize drafts before downstream computations and form controls read the payload.
  createComputed(() => {
    const payload = data();
    if (!payload) {
      return;
    }
    const settings = settingsFromPayload(payload);
    const serverValues = Object.fromEntries(
      Object.entries(settings).map(([key, info]) => [key, info.value]),
    );
    if (!lastSynced) {
      setDrafts(serverValues);
      lastSynced = cloneValue(unwrap(serverValues));
      return;
    }
    // Merge server refresh without dropping unsaved edits: only clean keys
    // (draft matches the previous server baseline) follow the new payload.
    const currentDrafts = untrack(() => unwrap(draftState.values));
    const next: Record<string, unknown> = { ...currentDrafts };
    let changed = false;
    for (const [key, serverValue] of Object.entries(serverValues)) {
      if (!(key in next) || sameJsonValue(currentDrafts[key], lastSynced[key])) {
        if (!sameJsonValue(next[key], serverValue)) {
          next[key] = serverValue;
          changed = true;
        }
      }
    }
    for (const key of Object.keys(next)) {
      if (!(key in serverValues)) {
        delete next[key];
        changed = true;
      }
    }
    if (changed) {
      setDrafts(next);
    }
    lastSynced = cloneValue(unwrap(serverValues));
  });

  const serverSettings = createMemo(() => {
    const payload = data();
    return payload ? settingsFromPayload(payload) : {};
  });

  const pendingChanges = createMemo<PendingChange[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return Object.entries(serverSettings())
      .map(([key, info]) => {
        const settingInfo = { ...info, key };
        const oldValue = typedValue(settingInfo, info.value);
        const newValue = typedValue(settingInfo, drafts()[key]);
        return {
          change: { key, oldValue: info.value, newValue },
          changed: !sameValue(oldValue, newValue),
        };
      })
      .filter(({ changed }) => changed)
      .map(({ change }) => change);
  });

  const setDraft = (key: string, value: unknown) => {
    setDraftState("values", key, reconcile(cloneValue(unwrap(value))));
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

  const discardAll = () => {
    const payload = data();
    if (!payload) return;
    const settings = settingsFromPayload(payload);
    setDrafts(Object.fromEntries(Object.entries(settings).map(([k, v]) => [k, v.value])));
    showToast("All changes discarded.", "warning");
  };

  const saveChanges = async (onSuccess?: () => void) => {
    const changes = pendingChanges();
    const payload = data();
    const settings = payload ? settingsFromPayload(payload) : {};
    const restartRequired = changes.some(
      (change: PendingChange) => settings[change.key]?.restart_required === true,
    );
    const values = Object.fromEntries(
      changes.map((change: PendingChange) => [change.key, change.newValue]),
    );
    try {
      await putJson("/settings", { values });
      showToast(
        restartRequired
          ? `Updated ${changes.length} setting(s). ${RESTART_REQUIRED_NOTICE}`
          : `Updated ${changes.length} setting(s).`,
        restartRequired ? "warning" : "success",
      );
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
    discardAll,
    saveChanges,
  };
}

// --- Components ----------------------------------------------------------

export function SettingControl(props: {
  info: SettingInfo;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (props.info.key.startsWith("llm.providers.") && props.info.key.endsWith(".model_profiles")) {
    return <FormTextarea
      value={typeof props.value === "string" ? props.value : JSON.stringify(props.value ?? {}, null, 2)}
      onChange={(value) => props.onChange(value)}
      rows={8}
      placeholder='{"custom-model":{"reasoning_effort_levels":["low","medium","high"],"reasoning_effort_default":"high"}}'
      validation={[{
        validate: (value) => {
          try {
            const parsed = JSON.parse(String(value || "{}"));
            return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed);
          } catch { return false; }
        },
        message: "Enter a JSON object keyed by model ID.",
      }]}
      showValidationStatus={true}
    />;
  }
  if (props.info.key === "tools.permissions") {
    return <FormTextarea
      value={typeof props.value === "string" ? props.value : JSON.stringify(props.value ?? [], null, 2)}
      onChange={(value) => props.onChange(value)}
      rows={6}
      placeholder='[{"action":"shell","resource":"*","effect":"ask"}]'
      validation={[{
        validate: (value) => {
          try { return Array.isArray(JSON.parse(String(value || "[]"))); }
          catch { return false; }
        },
        message: "Enter a JSON array of permission rules.",
      }]}
      showValidationStatus={true}
    />;
  }
  const validationRules = createMemo<ValidationRule[]>(() => {
    const rules: ValidationRule[] = [];

    if (props.info.required) {
      rules.push({
        validate: (value) => {
          if (props.info.input_type === "boolean") return true;
          return value !== null && value !== undefined && String(value).trim().length > 0;
        },
        message: "This field is required",
      });
    }

    return rules;
  });

  const selectOptions = createMemo(() => {
    const opts = props.info.options;
    if (!Array.isArray(opts) || opts.length === 0) {
      return null;
    }
    return opts.map((opt) => ({
      value: String(opt),
      label: opt === "__default__" ? "Use shared default" : String(opt),
    }));
  });

  return (
    <Show
      when={props.info.input_type === "boolean"}
      fallback={
        <Show
          when={props.info.input_type === "select" && Boolean(selectOptions())}
          fallback={
            <Show
              when={props.info.key.endsWith(".models")}
              fallback={
                <FormInput
                  value={displayDraft(props.value)}
                  onChange={(value) => props.onChange(value)}
                  type={controlInputType(props.info) as any}
                  placeholder={props.info.required ? "Required" : "Not set"}
                  min={props.info.min}
                  max={props.info.max}
                  step={props.info.step}
                  validation={validationRules()}
                  showValidationStatus={props.info.required}
                  showTogglePassword={props.info.input_type === "password"}
                />
              }
            >
              <FormTextarea
                value={displayDraft(props.value)}
                onChange={(value) => props.onChange(value)}
                rows={4}
                placeholder={props.info.required ? "Required — one per line" : "Not set — one per line"}
                validation={validationRules()}
                showValidationStatus={props.info.required}
              />
            </Show>
          }
        >
          <FormSelect
            value={displayDraft(props.value)}
            options={selectOptions()!}
            onChange={(value) => props.onChange(value)}
            placeholder={props.info.required ? "Required" : "Not set"}
            required={props.info.required}
            showValidationStatus={props.info.required}
            ariaLabel={props.info.label || props.info.key}
          />
        </Show>
      }
    >
      <button
        class={`toggle-control ${booleanValue(props.value) ? "active" : ""}`}
        type="button"
        role="switch"
        aria-checked={booleanValue(props.value)}
        aria-label={props.info.label || props.info.key}
        onClick={() => props.onChange(!booleanValue(props.value))}
      >
        <span class="toggle-track">
          <span class="toggle-thumb" />
        </span>
        <span class="toggle-text">{booleanValue(props.value) ? "Enabled" : "Disabled"}</span>
      </button>
    </Show>
  );
}

export function SettingsSection(props: {
  title: string;
  count?: number;
  actions?: JSX.Element;
  class?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  isOpen?: boolean;
  onToggle?: () => void;
  children: JSX.Element;
}) {
  return (
    <section class={`settings-group ${props.class || ""}`}>
      <div class="settings-section-header">
        <div class="settings-section-header-text">
          <div class="settings-section-title">
            {props.title}
          </div>
        </div>
        <Show when={props.actions}>
          <div class="row-wrap settings-section-actions">{props.actions}</div>
        </Show>
      </div>
      <div class="settings-section-body">{props.children}</div>
    </section>
  );
}

export function SettingRow(props: {
  settingKey: string;
  info: SettingInfo;
  draft: unknown;
  dirty: boolean;
  trimProviderPrefix?: boolean;
  trimToolPrefix?: boolean;
  actions?: JSX.Element;
  control?: JSX.Element;
  showKey?: boolean;
  onChange: (value: unknown) => void;
  onRestore: () => void;
}) {
  const label = createMemo(() =>
    settingLabel(props.settingKey, props.info, {
      trimProviderPrefix: props.trimProviderPrefix,
      trimToolPrefix: props.trimToolPrefix,
    }),
  );

  const tooltip = createMemo(() => {
    if (props.info.description) {
      return props.info.description;
    }
    return props.settingKey;
  });

  return (
    <div
      class={`setting-card setting-row ${props.dirty ? "setting-dirty" : ""} setting-row--compact`}
    >
      <div class="setting-card-accent" aria-hidden="true" />
      <div class="setting-card-main setting-row-main">
        <div class="setting-copy setting-row-copy">
          <div class="setting-title-row">
            <span class="setting-title" title={tooltip()}>
              {label()}
            </span>
            <Show when={props.info.required}>
              <span class="setting-required" title="Required">*</span>
            </Show>
            <Show when={props.showKey}>
              <span class="setting-key-tag mono" title={props.settingKey}>
                {props.settingKey}
              </span>
            </Show>
            <Show when={props.info.restart_required}>
              <span
                class="setting-restart-indicator"
                title={RESTART_REQUIRED_NOTICE}
                aria-label={RESTART_REQUIRED_NOTICE}
              >
                <TriangleAlert size={12} />
              </span>
            </Show>
            <Show when={props.dirty}>
              <span class="setting-dirty-dot" title="Unsaved changes" />
            </Show>
            <Show when={props.actions}>
              <div class="setting-inline-actions">{props.actions}</div>
            </Show>
          </div>
        </div>
        <div class="setting-control-panel setting-row-controls">
          <div class="setting-control-wrapper">
            {props.control || (
              <SettingControl
                info={{ ...props.info, key: props.settingKey }}
                value={props.draft}
                onChange={props.onChange}
              />
            )}
          </div>
          <Show when={props.dirty}>
            <button
              class="btn btn-ghost btn-sm setting-undo-btn"
              type="button"
              title="Undo change"
              aria-label="Undo change"
              onClick={props.onRestore}
            >
              <RotateCcw size={13} />
            </button>
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
        {(change: PendingChange) => {
          const info = () => props.settings[change.key];
          const isRestart = () => info()?.restart_required === true;
          return (
            <div class={`change-card ${isRestart() ? "change-card--restart" : ""}`}>
              <div class="change-card-head">
                <div class="change-card-title">
                  <span class="setting-title">{settingLabel(change.key, info())}</span>
                </div>
                <div class="mono hint change-card-key">{change.key}</div>
              </div>
              <div class="change-values">
                <div class="change-value change-value--old">
                  <span class="change-value-label">Current</span>
                  <span class="change-value-text">{formatSettingValue(info(), change.oldValue) || "—"}</span>
                </div>
                <div class="change-value-arrow" aria-hidden="true">
                  <ArrowRight size={14} />
                </div>
                <div class="change-value change-value--new">
                  <span class="change-value-label">New</span>
                  <span class="change-value-text">{formatSettingValue(info(), change.newValue) || "—"}</span>
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
  restartRequired?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={props.open}
      title="Confirm Changes"
      subtitle={`Review and apply ${props.changes.length} pending change${props.changes.length === 1 ? "" : "s"}`}
      icon={<Check size={17} />}
      iconVariant="primary"
      size="md"
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
            {props.saving ? "Saving…" : `Confirm Save (${props.changes.length})`}
          </button>
        </>
      }
    >
      <div class="stack">
        <p class="hint" style={{ "font-size": "13px" }}>
          You are about to update <strong>{props.changes.length} setting{props.changes.length === 1 ? "" : "s"}</strong>.
        </p>
        <Show when={props.restartRequired}>
          <div class="settings-restart-notice" role="status">
            <TriangleAlert size={14} />
            <span>{RESTART_REQUIRED_NOTICE} Some changes need a restart to take effect.</span>
          </div>
        </Show>
        <div class="change-list-scroll">
          <ChangesPreview changes={props.changes} settings={props.settings} />
        </div>
      </div>
    </Dialog>
  );
}

export function SettingsPendingBar(props: {
  count: number;
  saving?: boolean;
  restartRequired?: boolean;
  onDiscard: () => void;
  onSave: () => void;
}) {
  return (
    <Show when={props.count > 0}>
      <div class="settings-pending-bar" role="status" aria-live="polite">
        <div class="settings-pending-bar-left">
          <span class="settings-pending-text">
            {props.count} unsaved change{props.count === 1 ? "" : "s"}
          </span>
          <Show when={props.restartRequired}>
            <span class="settings-pending-restart" title={RESTART_REQUIRED_NOTICE}>
              <TriangleAlert size={13} />
              Restart required
            </span>
          </Show>
        </div>
        <div class="settings-pending-actions">
          <button class="btn btn-sm" type="button" disabled={props.saving} onClick={props.onDiscard}>
            Discard
          </button>
          <button class="btn btn-primary btn-sm" type="button" disabled={props.saving} onClick={props.onSave}>
            <Check size={14} />
            {props.saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>
    </Show>
  );
}

// --- Segmented tab bar ---------------------------------------------------

export type SettingsTabItem<T extends string> = {
  value: T;
  label: string;
  icon: () => JSX.Element;
};

export function SettingsTabBar<T extends string>(props: {
  items: ReadonlyArray<SettingsTabItem<T>>;
  value: T;
  ariaLabel: string;
  onChange: (value: T) => void;
}) {
  return (
    <div class="settings-tabs" role="tablist" aria-label={props.ariaLabel}>
      <For each={props.items as SettingsTabItem<T>[]}>
        {(item: SettingsTabItem<T>) => (
          <button
            class={`settings-tab ${props.value === item.value ? "active" : ""}`}
            type="button"
            role="tab"
            aria-selected={props.value === item.value}
            onClick={() => props.onChange(item.value)}
          >
            <span class="settings-tab-icon" aria-hidden="true">
              {item.icon()}
            </span>
            <span>{item.label}</span>
          </button>
        )}
      </For>
    </div>
  );
}
