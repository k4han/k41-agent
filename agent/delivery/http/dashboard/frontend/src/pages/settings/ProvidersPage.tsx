import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Edit3, RefreshCw, Save } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, putJson } from "@/lib/api";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  ChangesPreview,
  type PendingChange,
  SettingRow,
  SettingsSection,
  settingsFromPayload,
  useSettingsData,
} from "./shared";

const DEFAULT_PROVIDER_KEY = "llm.default_provider";

type ProviderFieldEntry = {
  key: string;
  info: SettingInfo;
};

type ProviderTableRow = {
  name: string;
  fields: ProviderFieldEntry[];
  providerType: string;
  defaultModel: string;
  modelCount: number;
  enabled: boolean;
  isDefault: boolean;
  dirtyCount: number;
  matchesSearch: boolean;
};

function hasDraftValue(drafts: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(drafts, key);
}

function draftValue(drafts: Record<string, unknown>, entry: ProviderFieldEntry | undefined): unknown {
  if (!entry) {
    return null;
  }
  return hasDraftValue(drafts, entry.key) ? drafts[entry.key] : entry.info.value;
}

function textValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function isFalseValue(value: unknown): boolean {
  return value === false || String(value).toLowerCase() === "false";
}

function modelCount(value: unknown): number {
  if (Array.isArray(value)) {
    return value.length;
  }
  if (typeof value === "string") {
    return value
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean).length;
  }
  return 0;
}

export function ProvidersPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft } =
    useSettingsData("/dashboard-api/providers");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [changesToConfirm, setChangesToConfirm] = createSignal<PendingChange[]>([]);
  const [editingProviderName, setEditingProviderName] = createSignal<string | null>(null);
  const [savingDefaultProvider, setSavingDefaultProvider] = createSignal(false);
  const { showToast } = useToast();

  const searchNeedle = createMemo(() => search().trim().toLowerCase());
  const dirtyKeys = createMemo(() => new Set(pendingChanges().map((change) => change.key)));

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
          key !== DEFAULT_PROVIDER_KEY && settingMatches(key, info),
        ),
      }))
      .filter((group) => group.settings.length > 0);
  });

  const defaultProviderSetting = createMemo(() => data()?.settings[DEFAULT_PROVIDER_KEY]);

  const defaultProviderOptions = createMemo(() => {
    const payload = data();
    const values = new Set(payload?.provider_name_options || []);
    const current = textValue(drafts()[DEFAULT_PROVIDER_KEY] ?? defaultProviderSetting()?.value);
    if (current) {
      values.add(current);
    }
    return Array.from(values);
  });

  const defaultProviderVisible = createMemo(() => {
    const info = defaultProviderSetting();
    if (!info) {
      return false;
    }
    return settingMatches(DEFAULT_PROVIDER_KEY, info, defaultProviderOptions().join(" "));
  });

  const visibleDefaultProviderSetting = createMemo(() =>
    defaultProviderVisible() ? defaultProviderSetting() : undefined,
  );

  const providerRows = createMemo<ProviderTableRow[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const fieldOrder = payload.provider_field_order || [];
    const draftSnapshot = drafts();
    const dirtySnapshot = dirtyKeys();
    const defaultProvider = textValue(
      hasDraftValue(draftSnapshot, DEFAULT_PROVIDER_KEY)
        ? draftSnapshot[DEFAULT_PROVIDER_KEY]
        : payload.settings[DEFAULT_PROVIDER_KEY]?.value,
    );

    return (payload.provider_rows || []).map((provider) => {
      const orderedFields = (fieldOrder.length ? fieldOrder : Object.keys(provider.fields))
        .map((field) => provider.fields[field])
        .filter((entry): entry is ProviderFieldEntry => Boolean(entry));
      const fieldMap = Object.fromEntries(
        Object.entries(provider.fields).filter(([, entry]) => Boolean(entry)),
      ) as Record<string, ProviderFieldEntry>;
      const providerTypeField = fieldMap.type || fieldMap.provider;
      const enabledField = fieldMap.enabled;
      const defaultModelField = fieldMap.default_model;
      const modelsField = fieldMap.models;
      const searchable = [
        provider.name,
        ...orderedFields.flatMap((entry) => [
          entry.key,
          entry.info.label,
          entry.info.description,
          textValue(draftValue(draftSnapshot, entry)),
        ]),
      ]
        .join(" ")
        .toLowerCase();

      return {
        name: provider.name,
        fields: orderedFields,
        providerType: textValue(draftValue(draftSnapshot, providerTypeField)),
        defaultModel: textValue(draftValue(draftSnapshot, defaultModelField)),
        modelCount: modelCount(draftValue(draftSnapshot, modelsField)),
        enabled: enabledField ? !isFalseValue(draftValue(draftSnapshot, enabledField)) : true,
        isDefault: defaultProvider === provider.name,
        dirtyCount: orderedFields.filter((entry) => dirtySnapshot.has(entry.key)).length,
        matchesSearch: !searchNeedle() || searchable.includes(searchNeedle()),
      };
    });
  });

  const filteredProviderRows = createMemo(() => providerRows().filter((provider) => provider.matchesSearch));

  const editingProvider = createMemo(() => {
    const name = editingProviderName();
    if (!name) {
      return null;
    }
    return providerRows().find((provider) => provider.name === name) || null;
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
      if (catalog) {
        setDraft(key, catalog.models.map((model) => model.id).join("\n"));
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to load models", "error");
    }
  };

  const saveDefaultProvider = async (nextProvider: string) => {
    const currentValue = textValue(data()?.settings[DEFAULT_PROVIDER_KEY]?.value);
    if (!nextProvider || nextProvider === textValue(drafts()[DEFAULT_PROVIDER_KEY])) {
      return;
    }

    setSavingDefaultProvider(true);
    setDraft(DEFAULT_PROVIDER_KEY, nextProvider);
    try {
      await putJson(`/settings/${DEFAULT_PROVIDER_KEY}`, { value: nextProvider });
      showToast("Default provider updated.");
      await load();
    } catch (err) {
      setDraft(DEFAULT_PROVIDER_KEY, currentValue || null);
      showToast(err instanceof Error ? err.message : "Failed to update default provider", "error");
    } finally {
      setSavingDefaultProvider(false);
    }
  };

  const providerPendingChanges = (providerName: string): PendingChange[] =>
    pendingChanges().filter((change) => change.key.startsWith(`llm.providers.${providerName}.`));

  const openProviderSaveConfirm = (providerName: string) => {
    const changes = providerPendingChanges(providerName);
    if (!changes.length) {
      return;
    }
    setChangesToConfirm(changes);
    setConfirmOpen(true);
  };

  const saveConfirmedChanges = async () => {
    const changes = changesToConfirm();
    if (!changes.length) {
      setConfirmOpen(false);
      return;
    }
    const values = Object.fromEntries(changes.map((change) => [change.key, change.newValue]));
    try {
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} setting(s).`);
      setConfirmOpen(false);
      setChangesToConfirm([]);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save settings", "error");
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Provider Configuration"
      subtitle="Manage default provider and per-provider LLM credentials/models."
      breadcrumbLabel="Providers"
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="stack">
              <SettingsResourceToolbar
                searchValue={search()}
                searchPlaceholder="Search providers or settings..."
                onSearchInput={setSearch}
              />

              <Show when={visibleDefaultProviderSetting()}>
                {(info) => (
                  <SettingsSection title="LLM" description="Default provider used at runtime">
                    <div class="settings-list">
                      <SettingRow
                        settingKey={DEFAULT_PROVIDER_KEY}
                        info={info()}
                        draft={drafts()[DEFAULT_PROVIDER_KEY]}
                        dirty={false}
                        control={
                          <select
                            class="select"
                            value={textValue(drafts()[DEFAULT_PROVIDER_KEY] ?? info().value)}
                            disabled={savingDefaultProvider() || defaultProviderOptions().length === 0}
                            onChange={(event) => saveDefaultProvider(event.currentTarget.value)}
                          >
                            <option value="" disabled>
                              {defaultProviderOptions().length ? "Select provider" : "No providers configured"}
                            </option>
                            <For each={defaultProviderOptions()}>
                              {(providerName) => <option value={providerName}>{providerName}</option>}
                            </For>
                          </select>
                        }
                        actions={
                          <Show when={savingDefaultProvider()}>
                            <span class="badge">Saving</span>
                          </Show>
                        }
                        onChange={() => undefined}
                        onRestore={() => undefined}
                      />
                    </div>
                  </SettingsSection>
                )}
              </Show>

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
                <section class="panel">
                  <div class="table-wrap">
                    <table class="table providers-table">
                      <thead>
                        <tr>
                          <th>Provider</th>
                          <th>Type</th>
                          <th>Default Model</th>
                          <th>Models</th>
                          <th>Status</th>
                          <th>Changes</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        <For
                          each={filteredProviderRows()}
                          fallback={<EmptyTableRow colSpan={7} message="No providers found." />}
                        >
                          {(provider) => (
                            <tr class={provider.dirtyCount ? "provider-row-dirty" : ""}>
                              <td>
                                <div class="provider-name-cell">
                                  <span class="setting-title">{provider.name}</span>
                                  <Show when={provider.isDefault}>
                                    <span class="badge badge-info">Default</span>
                                  </Show>
                                </div>
                              </td>
                              <td>
                                <Show when={provider.providerType} fallback={<span class="hint">Not set</span>}>
                                  <span class="chip">{provider.providerType}</span>
                                </Show>
                              </td>
                              <td>
                                <Show when={provider.defaultModel} fallback={<span class="hint">Not set</span>}>
                                  <div class="provider-table-value mono">{provider.defaultModel}</div>
                                </Show>
                              </td>
                              <td>
                                <span class="hint">
                                  {provider.modelCount
                                    ? `${provider.modelCount} model${provider.modelCount === 1 ? "" : "s"}`
                                    : "No models"}
                                </span>
                              </td>
                              <td>
                                <span class={provider.enabled ? "badge badge-success" : "badge badge-warning"}>
                                  {provider.enabled ? "Enabled" : "Disabled"}
                                </span>
                              </td>
                              <td>
                                <Show when={provider.dirtyCount > 0} fallback={<span class="hint">-</span>}>
                                  <span class="badge badge-warning">
                                    {provider.dirtyCount} unsaved
                                  </span>
                                </Show>
                              </td>
                              <td>
                                <div class="row-wrap provider-table-actions">
                                  <button
                                    class="btn btn-sm"
                                    type="button"
                                    onClick={() => setEditingProviderName(provider.name)}
                                  >
                                    <Edit3 size={13} />
                                    Edit
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </div>
                </section>
              </SettingsSection>
            </div>

            <Show when={editingProvider()}>
              {(provider) => (
                <ProviderEditDialog
                  provider={provider()}
                  drafts={drafts()}
                  dirtyKeys={dirtyKeys()}
                  onClose={() => setEditingProviderName(null)}
                  onLoadModels={() => loadProviderModels(provider().name)}
                  onSave={() => openProviderSaveConfirm(provider().name)}
                  onChange={(key, value) => setDraft(key, value)}
                  onRestore={(key) => restoreDraft(key)}
                />
              )}
            </Show>

            <ConfirmDialog
              open={confirmOpen()}
              onClose={() => setConfirmOpen(false)}
              changes={changesToConfirm()}
              settings={settingsFromPayload(payload)}
              onConfirm={saveConfirmedChanges}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function ProviderEditDialog(props: {
  provider: ProviderTableRow;
  drafts: Record<string, unknown>;
  dirtyKeys: Set<string>;
  onClose: () => void;
  onLoadModels: () => void;
  onSave: () => void;
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
}) {
  return (
    <Dialog
      open
      title={`Edit ${props.provider.name}`}
      wide
      onClose={props.onClose}
      footer={
        <>
          <button class="btn" type="button" onClick={props.onClose}>
            Close
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={props.provider.dirtyCount === 0}
            onClick={props.onSave}
          >
            <Save size={14} />
            Save{props.provider.dirtyCount ? ` (${props.provider.dirtyCount})` : ""}
          </button>
        </>
      }
    >
      <div class="stack">
        <div class="provider-edit-summary">
          <span class={props.provider.enabled ? "badge badge-success" : "badge badge-warning"}>
            {props.provider.enabled ? "Enabled" : "Disabled"}
          </span>
          <Show when={props.provider.isDefault}>
            <span class="badge badge-info">Default</span>
          </Show>
          <Show when={props.provider.providerType}>
            <span class="chip">{props.provider.providerType}</span>
          </Show>
          <span class="hint">
            {props.provider.fields.length} setting{props.provider.fields.length === 1 ? "" : "s"}
          </span>
        </div>

        <div class="settings-list">
          <For each={props.provider.fields} fallback={<div class="empty">No settings found.</div>}>
            {(entry) => (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={props.dirtyKeys.has(entry.key)}
                trimProviderPrefix
                actions={
                  entry.key.endsWith(".models") ? (
                    <button class="btn btn-sm" type="button" onClick={props.onLoadModels}>
                      <RefreshCw size={13} />
                      Load models
                    </button>
                  ) : undefined
                }
                onChange={(value) => props.onChange(entry.key, value)}
                onRestore={() => props.onRestore(entry.key)}
              />
            )}
          </For>
        </div>
      </div>
    </Dialog>
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
