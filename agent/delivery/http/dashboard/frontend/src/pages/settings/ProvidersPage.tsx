import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw, Save } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch } from "@/lib/api";
import { formatValue } from "@/lib/utils";

import { SettingsLayout } from "./SettingsLayout";
import {
  SettingRow,
  SourcesTab,
  useSettingsData,
} from "./shared";

export function ProvidersPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, resetDraft, saveChanges } =
    useSettingsData("/dashboard-api/providers");

  const [tab, setTab] = createSignal<"effective" | "sources">("effective");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const { showToast } = useToast();

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return Object.entries(payload.by_category)
      .map(([category, settings]) => ({
        category,
        settings: Object.entries(settings),
      }))
      .filter((group) => group.settings.length > 0);
  });

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
    <SettingsLayout
      title="Provider Configuration"
      subtitle="Manage default provider and per-provider LLM credentials/models."
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
              </div>
            </Show>

            <Show when={tab() === "sources"}>
              <SourcesTab sources={payload.settings_sources} />
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
                  <button class="btn btn-primary" type="button" onClick={() => saveChanges(() => setConfirmOpen(false))}>
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
    </SettingsLayout>
  );
}
