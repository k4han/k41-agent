import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw, RotateCcw, Save } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, putJson } from "@/lib/api";
import { formatValue, parseModelList } from "@/lib/utils";
import type { SettingInfo, SettingsPayload } from "@/types";

type SettingsMode = "config" | "providers";

type PendingChange = {
  key: string;
  oldValue: unknown;
  newValue: unknown;
};

function sameValue(a: unknown, b: unknown): boolean {
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

function typedValue(info: SettingInfo, raw: unknown): unknown {
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

function SettingControl(props: {
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

function SettingRow(props: {
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

export function SettingsPage(props: { mode: SettingsMode }) {
  const [data, setData] = createSignal<SettingsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [tab, setTab] = createSignal<"effective" | "sources">("effective");
  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const { showToast } = useToast();

  const endpoint = () => (props.mode === "providers" ? "/dashboard-api/providers" : "/dashboard-api/config");

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<SettingsPayload>(endpoint());
      setData(payload);
      setDrafts(Object.fromEntries(Object.entries(payload.settings).map(([key, info]) => [key, info.value])));
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

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = search().trim().toLowerCase();
    return Object.entries(payload.by_category)
      .map(([category, settings]) => ({
        category,
        settings: Object.entries(settings).filter(([key, info]) =>
          [key, info.label, info.description, info.category]
            .join(" ")
            .toLowerCase()
            .includes(needle),
        ),
      }))
      .filter((group) => group.settings.length > 0);
  });

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const resetDraft = (key: string) => {
    setDraft(key, null);
    showToast("Reset queued. Save changes to apply.", "warning");
  };

  const saveChanges = async () => {
    const values = Object.fromEntries(pendingChanges().map((change) => [change.key, change.newValue]));
    try {
      await putJson("/settings", { values });
      showToast(`Updated ${pendingChanges().length} setting(s).`);
      setConfirmOpen(false);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save settings", "error");
    }
  };

  const loadProviderModels = async (providerName: string) => {
    try {
      const result = await apiFetch<{ providers: Array<{ provider: string; models: Array<{ id: string }> }> }>(
        "/providers/models?refresh=true",
      );
      const catalog = result.providers.find((provider) => provider.provider === providerName);
      const count = catalog?.models.length || 0;
      showToast(count ? `Loaded ${count} model(s).` : "No models returned.", count ? "success" : "warning");
      const key = `llm.providers.${providerName}.models`;
      if (catalog && data()?.settings[key]) {
        setDraft(key, catalog.models.map((model) => model.id).join("\n"));
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to load models", "error");
    }
  };

  onMount(load);

  return (
    <AppShell
      title={props.mode === "providers" ? "Provider Configuration" : "Runtime Configuration"}
      subtitle={
        props.mode === "providers"
          ? "Manage default provider and per-provider LLM settings."
          : "Manage channels, database, and security runtime settings."
      }
      actions={
        <>
          <button class="btn" type="button" onClick={load}>
            <RefreshCw size={14} />
            Reload
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={pendingChanges().length === 0}
            onClick={() => setConfirmOpen(true)}
          >
            <Save size={14} />
            Save Changes {pendingChanges().length ? `(${pendingChanges().length})` : ""}
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="tabs">
              <button class={`tab ${tab() === "effective" ? "active" : ""}`} type="button" onClick={() => setTab("effective")}>
                Effective Values
              </button>
              <button class={`tab ${tab() === "sources" ? "active" : ""}`} type="button" onClick={() => setTab("sources")}>
                By Source
              </button>
            </div>

            <Show when={tab() === "effective"}>
              <div class="stack">
                <Show when={props.mode === "config"}>
                  <input
                    class="input"
                    type="search"
                    placeholder="Search settings..."
                    value={search()}
                    onInput={(event) => setSearch(event.currentTarget.value)}
                  />
                </Show>

                <For each={filteredCategories()}>
                  {(group) => (
                    <section class="stack">
                      <div class="row-wrap">
                        <strong>{group.category}</strong>
                        <span class="badge">{group.settings.length}</span>
                      </div>
                      <For each={group.settings}>
                        {([key, info]) => (
                          <SettingRow
                            settingKey={key}
                            info={info}
                            draft={drafts()[key]}
                            dirty={pendingChanges().some((change) => change.key === key)}
                            onChange={(value) => setDraft(key, value)}
                            onReset={() => resetDraft(key)}
                          />
                        )}
                      </For>
                    </section>
                  )}
                </For>

                <Show when={props.mode === "providers"}>
                  <section class="panel">
                    <div class="panel-header">
                      <div class="panel-title">Providers</div>
                    </div>
                    <div class="panel-body stack">
                      <For each={payload.provider_rows || []} fallback={<div class="empty">No providers found in config.</div>}>
                        {(provider) => (
                          <section class="panel">
                            <div class="panel-header">
                              <div>
                                <div class="panel-title">{provider.name}</div>
                                <div class="hint">Per-provider configuration</div>
                              </div>
                              <button class="btn btn-sm" type="button" onClick={() => loadProviderModels(provider.name)}>
                                Load models
                              </button>
                            </div>
                            <div class="panel-body stack">
                              <For each={payload.provider_field_order || Object.keys(provider.fields)}>
                                {(field) => {
                                  const entry = provider.fields[field];
                                  return (
                                    <Show when={entry}>
                                      <SettingRow
                                        settingKey={entry.key}
                                        info={entry.info}
                                        draft={drafts()[entry.key]}
                                        dirty={pendingChanges().some((change) => change.key === entry.key)}
                                        onChange={(value) => setDraft(entry.key, value)}
                                        onReset={() => resetDraft(entry.key)}
                                      />
                                    </Show>
                                  );
                                }}
                              </For>
                            </div>
                          </section>
                        )}
                      </For>
                    </div>
                  </section>
                </Show>
              </div>
            </Show>

            <Show when={tab() === "sources"}>
              <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">Configuration Sources</div>
                </div>
                <div class="panel-body stack">
                  <For each={Object.entries(payload.settings_sources)}>
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
            </Show>

            <Dialog
              open={confirmOpen()}
              title="Confirm Changes"
              onClose={() => setConfirmOpen(false)}
              footer={
                <>
                  <button class="btn" type="button" onClick={() => setConfirmOpen(false)}>
                    Cancel
                  </button>
                  <button class="btn btn-primary" type="button" onClick={saveChanges}>
                    Confirm Save
                  </button>
                </>
              }
            >
              <div class="stack">
                <p>You are about to update {pendingChanges().length} setting(s).</p>
                <For each={pendingChanges()}>
                  {(change) => (
                    <div class="panel">
                      <div class="panel-body">
                        <div class="mono">{change.key}</div>
                        <div class="hint">Current: {formatValue(change.oldValue)}</div>
                        <div>New: {formatValue(change.newValue)}</div>
                      </div>
                    </div>
                  )}
                </For>
              </div>
            </Dialog>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}

