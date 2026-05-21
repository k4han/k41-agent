import { createMemo, createSignal, For, onMount } from "solid-js";
import { Save, Search } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch } from "@/lib/api";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  ChangesPreview,
  type PendingChange,
  SettingRow,
  SettingsSection,
  useSettingsData,
} from "./shared";

export function ProvidersPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft, saveChanges } =
    useSettingsData("/dashboard-api/providers");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const { showToast } = useToast();

  const searchNeedle = createMemo(() => search().trim().toLowerCase());

  const settingMatches = (key: string, info: SettingInfo, extra = ""): boolean => {
    const needle = searchNeedle();
    if (!needle) {
      return true;
    }
    return [key, info.label ?? "", info.description ?? "", info.category ?? "", extra]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  };

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return Object.entries(payload.by_category)
      .map(([category, settings]) => ({
        category,
        settings: Object.entries(settings).filter(([key, info]) =>
          settingMatches(key, info),
        ),
      }))
      .filter((group) => group.settings.length > 0);
  });

  const filteredProviderRows = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const fieldOrder = payload.provider_field_order || [];
    return (payload.provider_rows || [])
      .map((provider) => {
        const orderedFields = (fieldOrder.length ? fieldOrder : Object.keys(provider.fields))
          .map((field) => provider.fields[field])
          .filter((entry): entry is { key: string; info: SettingInfo } => Boolean(entry));
        const providerMatches = !searchNeedle() || provider.name.toLowerCase().includes(searchNeedle());
        return {
          name: provider.name,
          fields: providerMatches
            ? orderedFields
            : orderedFields.filter((entry) => settingMatches(entry.key, entry.info, provider.name)),
        };
      })
      .filter((provider) => provider.fields.length > 0);
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
      breadcrumbLabel="Providers"
      actions={
        <button
          class="btn btn-primary"
          type="button"
          disabled={pendingChanges().length === 0}
          onClick={() => setConfirmOpen(true)}
        >
          <Save size={14} />
          Save Changes {pendingChanges().length ? `(${pendingChanges().length})` : ""}
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="stack">
              <div class="settings-toolbar">
                <div class="settings-search">
                  <Search size={15} />
                  <input
                    class="input"
                    type="search"
                    placeholder="Search providers or settings..."
                    value={search()}
                    onInput={(event) => setSearch(event.currentTarget.value)}
                  />
                </div>
              </div>

              <For each={filteredCategories()}>
                {(group) => (
                  <SettingsSection
                    title={categoryLabel(group.category)}
                    description={`${group.settings.length} setting${group.settings.length === 1 ? "" : "s"}`}
                  >
                    <div class="settings-list">
                      <For each={group.settings}>
                        {([key, info]) => (
                          <SettingRow
                            settingKey={key}
                            info={info}
                            draft={drafts()[key]}
                            dirty={pendingChanges().some((change) => change.key === key)}
                            onChange={(value) => setDraft(key, value)}
                            onRestore={() => restoreDraft(key)}
                          />
                        )}
                      </For>
                    </div>
                  </SettingsSection>
                )}
              </For>

              <SettingsSection
                title="Providers"
                description={`${filteredProviderRows().length} provider${filteredProviderRows().length === 1 ? "" : "s"}`}
              >
                <div class="provider-list">
                  <For each={filteredProviderRows()} fallback={<div class="empty">No providers found.</div>}>
                    {(provider) => (
                      <section class="provider-config-group">
                        <div class="provider-config-header">
                          <div>
                            <div class="panel-title">{provider.name}</div>
                            <div class="hint">Per-provider configuration</div>
                          </div>
                          <button class="btn btn-sm" type="button" onClick={() => loadProviderModels(provider.name)}>
                            Load models
                          </button>
                        </div>
                        <div class="settings-list">
                          <For each={provider.fields}>
                            {(entry) => (
                              <SettingRow
                                settingKey={entry.key}
                                info={entry.info}
                                draft={drafts()[entry.key]}
                                dirty={pendingChanges().some((change) => change.key === entry.key)}
                                trimProviderPrefix
                                onChange={(value) => setDraft(entry.key, value)}
                                onRestore={() => restoreDraft(entry.key)}
                              />
                            )}
                          </For>
                        </div>
                      </section>
                    )}
                  </For>
                </div>
              </SettingsSection>
            </div>

            <ConfirmDialog
              open={confirmOpen()}
              onClose={() => setConfirmOpen(false)}
              changes={pendingChanges()}
              settings={payload.settings}
              onConfirm={() => saveChanges(() => setConfirmOpen(false))}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function ConfirmDialog(props: {
  open: boolean;
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
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button class="btn btn-primary" type="button" onClick={props.onConfirm}>
            Confirm Save
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
