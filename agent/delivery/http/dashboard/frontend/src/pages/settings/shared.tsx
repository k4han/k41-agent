import { createMemo, createSignal, For, Show } from "solid-js";
import { RotateCcw } from "lucide-solid";

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
      setDrafts(
        Object.fromEntries(
          Object.entries(payload.settings).map(([key, info]) => [key, info.value]),
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
    return Object.entries(payload.settings)
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  });

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const resetDraft = (key: string) => {
    setDraft(key, null);
    showToast("Reset queued. Save changes to apply.", "warning");
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

  return { data, error, drafts, load, pendingChanges, setDraft, resetDraft, saveChanges };
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
              type={props.info.input_type === "password" ? "password" : props.info.input_type === "number" ? "number" : "text"}
              min={props.info.min}
              max={props.info.max}
              step={props.info.step}
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
      <label class="checkbox-row">
        <input
          type="checkbox"
          checked={Boolean(props.value)}
          onChange={(event) => props.onChange(event.currentTarget.checked)}
        />
        <span>{Boolean(props.value) ? "enabled" : "disabled"}</span>
      </label>
    </Show>
  );
}

export function SettingRow(props: {
  settingKey: string;
  info: SettingInfo;
  draft: unknown;
  dirty: boolean;
  onChange: (value: unknown) => void;
  onReset: () => void;
}) {
  return (
    <div class={`panel ${props.dirty ? "setting-dirty" : ""}`}>
      <div class="panel-body">
        <div class="grid-2">
          <div>
            <div>{props.info.label || props.settingKey}</div>
            <div class="mono hint">{props.settingKey}</div>
            <Show when={props.info.description}>
              <div class="hint">{props.info.description}</div>
            </Show>
            <div class="row-wrap" style={{ "margin-top": "8px" }}>
              <span class="badge">{props.info.source}</span>
              <span class="chip">{props.info.input_type}</span>
            </div>
          </div>
          <div class="stack">
            <SettingControl info={{ ...props.info, key: props.settingKey }} value={props.draft} onChange={props.onChange} />
            <div class="row-wrap">
              <button class="btn btn-sm" type="button" onClick={props.onReset}>
                <RotateCcw size={13} />
                Reset
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SourcesTab(props: { sources: Record<string, Array<{ source: string; value: unknown }>> }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Configuration Sources</div>
      </div>
      <div class="panel-body stack">
        <For each={Object.entries(props.sources)}>
          {([key, sources]) => (
            <div class="panel">
              <div class="panel-body">
                <div class="mono">{key}</div>
                <div class="stack" style={{ "margin-top": "8px" }}>
                  <For each={sources}>
                    {(source) => (
                      <div class="row-wrap">
                        <span class="badge">{source.source}</span>
                        <span class="mono">{formatValue(source.value)}</span>
                      </div>
                    )}
                  </For>
                </div>
              </div>
            </div>
          )}
        </For>
      </div>
    </section>
  );
}
