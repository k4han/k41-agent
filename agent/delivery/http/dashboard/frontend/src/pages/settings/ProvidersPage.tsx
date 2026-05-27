import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Edit3, Plus, RefreshCw, Save, Star, Trash2 } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import type { ProviderRow, ProviderTypeOption, SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  ChangesPreview,
  type PendingChange,
  SettingRow,
  settingsFromPayload,
  useSettingsData,
} from "./shared";

const DEFAULT_PROVIDER_KEY = "llm.default_provider";
const DETAIL_FIELD_ORDER = ["enabled", "api_key", "base_url", "default_model", "models", "temperature"];

type ProviderFieldEntry = {
  key: string;
  info: SettingInfo;
};

type ProviderView = {
  name: string;
  fields: ProviderFieldEntry[];
  detailFields: ProviderFieldEntry[];
  fieldMap: Record<string, ProviderFieldEntry>;
  providerType: string;
  typeLabel: string;
  defaultModel: string;
  modelCount: number;
  enabled: boolean;
  isDefault: boolean;
  ready: boolean;
  requiresBaseUrl: boolean;
  canDelete: boolean;
  deleteBlockReason: string;
  canSetDefault: boolean;
  defaultBlockReason: string;
  dirtyCount: number;
  matchesSearch: boolean;
};

type ProviderCreateForm = {
  name: string;
  type: string;
  api_key: string;
  base_url: string;
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

function fieldName(entry: ProviderFieldEntry): string {
  return entry.key.split(".").at(-1) || entry.key;
}

function providerTypeOptions(payloadOptions: ProviderTypeOption[] | undefined): ProviderTypeOption[] {
  if (payloadOptions?.length) {
    return payloadOptions;
  }
  return [
    {
      value: "google",
      label: "Google",
      description: "Google Gemini provider",
      requires_base_url: false,
    },
    {
      value: "anthropic",
      label: "Anthropic",
      description: "Anthropic Claude provider",
      requires_base_url: false,
    },
    {
      value: "openai_compatible",
      label: "OpenAI-compatible",
      description: "Custom endpoint that implements the OpenAI chat API",
      requires_base_url: true,
    },
  ];
}

function buildProviderView(
  provider: ProviderRow,
  fieldOrder: string[],
  drafts: Record<string, unknown>,
  dirtyKeys: Set<string>,
  searchNeedle: string,
): ProviderView {
  const fieldMap = Object.fromEntries(
    Object.entries(provider.fields).filter(([, entry]) => Boolean(entry)),
  ) as Record<string, ProviderFieldEntry>;
  const orderedFields = (fieldOrder.length ? fieldOrder : Object.keys(provider.fields))
    .map((field) => fieldMap[field])
    .filter((entry): entry is ProviderFieldEntry => Boolean(entry));
  const detailFields = DETAIL_FIELD_ORDER
    .map((field) => fieldMap[field])
    .filter((entry): entry is ProviderFieldEntry => {
      if (!entry) {
        return false;
      }
      return fieldName(entry) !== "base_url" || provider.requires_base_url;
    });
  const providerTypeField = fieldMap.type || fieldMap.provider;
  const enabledField = fieldMap.enabled;
  const defaultModelField = fieldMap.default_model;
  const modelsField = fieldMap.models;
  const searchable = [
    provider.name,
    provider.type,
    provider.type_label,
    ...orderedFields.flatMap((entry) => [
      entry.key,
      entry.info.label,
      entry.info.description,
      textValue(draftValue(drafts, entry)),
    ]),
  ]
    .join(" ")
    .toLowerCase();

  return {
    name: provider.name,
    fields: orderedFields,
    detailFields,
    fieldMap,
    providerType: textValue(draftValue(drafts, providerTypeField)) || provider.type,
    typeLabel: provider.type_label || textValue(draftValue(drafts, providerTypeField)) || provider.type,
    defaultModel: textValue(draftValue(drafts, defaultModelField)),
    modelCount: modelCount(draftValue(drafts, modelsField)),
    enabled: enabledField ? !isFalseValue(draftValue(drafts, enabledField)) : provider.enabled,
    isDefault: provider.is_default,
    ready: provider.ready,
    requiresBaseUrl: provider.requires_base_url,
    canDelete: provider.can_delete,
    deleteBlockReason: provider.delete_block_reason,
    canSetDefault: provider.can_set_default,
    defaultBlockReason: provider.default_block_reason,
    dirtyCount: detailFields.filter((entry) => dirtyKeys.has(entry.key)).length,
    matchesSearch: !searchNeedle || searchable.includes(searchNeedle),
  };
}

export function ProvidersPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft } =
    useSettingsData("/dashboard-api/providers");

  const [search, setSearch] = createSignal("");
  const [selectedProviderName, setSelectedProviderName] = createSignal<string | null>(null);
  const [addOpen, setAddOpen] = createSignal(false);
  const [addForm, setAddForm] = createSignal<ProviderCreateForm>({
    name: "",
    type: "google",
    api_key: "",
    base_url: "",
  });
  const [savingDefaultProvider, setSavingDefaultProvider] = createSignal(false);
  const [savingProvider, setSavingProvider] = createSignal(false);
  const [creatingProvider, setCreatingProvider] = createSignal(false);
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [changesToConfirm, setChangesToConfirm] = createSignal<PendingChange[]>([]);
  const [deleteTarget, setDeleteTarget] = createSignal<ProviderView | null>(null);
  const { showToast } = useToast();

  const searchNeedle = createMemo(() => search().trim().toLowerCase());
  const dirtyKeys = createMemo(() => new Set(pendingChanges().map((change) => change.key)));

  const typeOptions = createMemo(() => providerTypeOptions(data()?.provider_type_options));

  const providerRows = createMemo<ProviderView[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return (payload.provider_rows || []).map((provider) =>
      buildProviderView(
        provider,
        payload.provider_field_order || [],
        drafts(),
        dirtyKeys(),
        searchNeedle(),
      ),
    );
  });

  const filteredProviderRows = createMemo(() =>
    providerRows().filter((provider) => provider.matchesSearch),
  );

  const selectedProvider = createMemo(() => {
    const selectedName = selectedProviderName();
    const rows = providerRows();
    return rows.find((provider) => provider.name === selectedName) || null;
  });

  const providerPendingChanges = (providerName: string): PendingChange[] =>
    pendingChanges().filter((change) => change.key.startsWith(`llm.providers.${providerName}.`));

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

  const saveProviderChanges = (providerName: string) => {
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
    setSavingProvider(true);
    const values = Object.fromEntries(changes.map((change) => [change.key, change.newValue]));
    try {
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} setting(s).`);
      setConfirmOpen(false);
      setChangesToConfirm([]);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save provider", "error");
    } finally {
      setSavingProvider(false);
    }
  };

  const setDefaultProvider = async (provider: ProviderView) => {
    if (!provider.canSetDefault) {
      showToast(provider.defaultBlockReason || "Provider is not ready.", "warning");
      return;
    }
    setSavingDefaultProvider(true);
    try {
      await putJson(`/settings/${DEFAULT_PROVIDER_KEY}`, { value: provider.name });
      showToast("Default provider updated.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to update default provider", "error");
    } finally {
      setSavingDefaultProvider(false);
    }
  };

  const createProvider = async () => {
    const form = addForm();
    setCreatingProvider(true);
    try {
      await postJson("/dashboard-api/providers", form);
      showToast("Provider created.");
      setAddOpen(false);
      await load();
      setSelectedProviderName(form.name.trim());
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create provider", "error");
    } finally {
      setCreatingProvider(false);
    }
  };

  const deleteProvider = async () => {
    const provider = deleteTarget();
    if (!provider) {
      return;
    }
    try {
      await deleteJson(`/dashboard-api/providers/${encodeURIComponent(provider.name)}`);
      showToast("Provider deleted.");
      setDeleteTarget(null);
      setSelectedProviderName(null);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete provider", "error");
    }
  };

  const openAddDialog = () => {
    setAddForm({ name: "", type: "google", api_key: "", base_url: "" });
    setAddOpen(true);
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
            <SettingsResourceToolbar
              searchValue={search()}
              searchPlaceholder="Search providers..."
              onSearchInput={setSearch}
              actions={
                <button class="btn btn-primary" type="button" onClick={openAddDialog}>
                  <Plus size={15} />
                  Add Provider
                </button>
              }
            />

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Providers</div>
                <span class="badge">{filteredProviderRows().length}</span>
              </div>
              <div class="table-wrap">
                <table class="table providers-table">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Type</th>
                      <th>Default Model</th>
                      <th>Models</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={filteredProviderRows()}
                      fallback={
                        <tr>
                          <td colSpan={6}>
                            <div class="empty">No providers found.</div>
                          </td>
                        </tr>
                      }
                    >
                      {(provider) => (
                        <tr
                          class={`provider-table-row ${provider.dirtyCount ? "provider-row-dirty" : ""}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelectedProviderName(provider.name)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedProviderName(provider.name);
                            }
                          }}
                        >
                          <td>
                            <div class="provider-name-cell">
                              <span class="setting-title">{provider.name}</span>
                              <Show when={provider.isDefault}>
                                <span class="badge badge-info">Default</span>
                              </Show>
                              <Show when={provider.dirtyCount > 0}>
                                <span class="badge badge-warning">{provider.dirtyCount} unsaved</span>
                              </Show>
                            </div>
                          </td>
                          <td>
                            <span class="chip">{provider.typeLabel}</span>
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
                            <div class="row-wrap provider-table-actions">
                              <button
                                class="btn btn-sm"
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setSelectedProviderName(provider.name);
                                }}
                              >
                                <Edit3 size={13} />
                                Edit
                              </button>
                              <button
                                class="btn btn-sm btn-danger"
                                type="button"
                                disabled={!provider.canDelete}
                                title={provider.deleteBlockReason}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setDeleteTarget(provider);
                                }}
                              >
                                <Trash2 size={13} />
                                Delete
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

            <Show when={selectedProvider()}>
              {(provider) => (
                <ProviderEditDialog
                  provider={provider()}
                  drafts={drafts()}
                  dirtyKeys={dirtyKeys()}
                  savingDefault={savingDefaultProvider()}
                  savingProvider={savingProvider()}
                  onClose={() => setSelectedProviderName(null)}
                  onSetDefault={() => setDefaultProvider(provider())}
                  onLoadModels={() => loadProviderModels(provider().name)}
                  onSave={() => saveProviderChanges(provider().name)}
                  onChange={(key, value) => setDraft(key, value)}
                  onRestore={(key) => restoreDraft(key)}
                />
              )}
            </Show>

            <AddProviderDialog
              open={addOpen()}
              form={addForm()}
              typeOptions={typeOptions()}
              creating={creatingProvider()}
              onClose={() => setAddOpen(false)}
              onChange={(patch) => setAddForm((current) => ({ ...current, ...patch }))}
              onSubmit={createProvider}
            />

            <ConfirmDialog
              open={confirmOpen()}
              saving={savingProvider()}
              changes={changesToConfirm()}
              settings={settingsFromPayload(payload)}
              onClose={() => setConfirmOpen(false)}
              onConfirm={saveConfirmedChanges}
            />

            <DeleteProviderDialog
              provider={deleteTarget()}
              onClose={() => setDeleteTarget(null)}
              onConfirm={deleteProvider}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function ProviderEditDialog(props: {
  provider: ProviderView;
  drafts: Record<string, unknown>;
  dirtyKeys: Set<string>;
  savingDefault: boolean;
  savingProvider: boolean;
  onClose: () => void;
  onSetDefault: () => void;
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
            class="btn"
            type="button"
            disabled={!props.provider.canSetDefault || props.savingDefault}
            title={props.provider.defaultBlockReason}
            onClick={props.onSetDefault}
          >
            <Star size={13} />
            Set Default
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={props.provider.dirtyCount === 0 || props.savingProvider}
            onClick={props.onSave}
          >
            <Save size={13} />
            Save{props.provider.dirtyCount ? ` (${props.provider.dirtyCount})` : ""}
          </button>
        </>
      }
    >
      <div class="stack">
        <div class="provider-edit-summary">
          <span class="chip">{props.provider.providerType}</span>
          <Show when={props.provider.isDefault}>
            <span class="badge badge-info">Default</span>
          </Show>
          <span class={props.provider.enabled ? "badge badge-success" : "badge badge-warning"}>
            {props.provider.enabled ? "Enabled" : "Disabled"}
          </span>
          <Show when={props.provider.dirtyCount > 0}>
            <span class="badge badge-warning">{props.provider.dirtyCount} unsaved</span>
          </Show>
        </div>
        <div class="provider-summary-grid">
          <div>
            <span class="setting-detail-label">Type</span>
            <span class="chip">{props.provider.providerType}</span>
          </div>
          <div>
            <span class="setting-detail-label">Default Model</span>
            <span class="provider-summary-value">{props.provider.defaultModel || "Not set"}</span>
          </div>
          <div>
            <span class="setting-detail-label">Models</span>
            <span class="provider-summary-value">{props.provider.modelCount}</span>
          </div>
          <div>
            <span class="setting-detail-label">Status</span>
            <span class={props.provider.enabled ? "badge badge-success" : "badge badge-warning"}>
              {props.provider.enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
        </div>

        <div class="settings-list">
          <For each={props.provider.detailFields} fallback={<div class="empty">No settings found.</div>}>
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
                      Load Models
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

function AddProviderDialog(props: {
  open: boolean;
  form: ProviderCreateForm;
  typeOptions: ProviderTypeOption[];
  creating: boolean;
  onClose: () => void;
  onChange: (patch: Partial<ProviderCreateForm>) => void;
  onSubmit: () => void;
}) {
  const selectedType = createMemo(() =>
    props.typeOptions.find((option) => option.value === props.form.type) || props.typeOptions[0],
  );
  const canSubmit = createMemo(() => {
    const form = props.form;
    if (!form.name.trim() || !form.api_key.trim()) {
      return false;
    }
    if (selectedType()?.requires_base_url && !form.base_url.trim()) {
      return false;
    }
    return true;
  });

  return (
    <Dialog
      open={props.open}
      title="Add Provider"
      wide
      onClose={props.onClose}
      footer={
        <>
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={!canSubmit() || props.creating}
            onClick={props.onSubmit}
          >
            <Plus size={14} />
            Add Provider
          </button>
        </>
      }
    >
      <div class="stack">
        <div class="provider-type-grid">
          <For each={props.typeOptions}>
            {(option) => (
              <button
                class={`provider-type-option ${props.form.type === option.value ? "active" : ""}`}
                type="button"
                onClick={() => props.onChange({ type: option.value, base_url: "" })}
              >
                <span class="setting-title">{option.label}</span>
                <span class="hint">{option.description}</span>
              </button>
            )}
          </For>
        </div>

        <div class="grid-2">
          <label class="field">
            <span>Provider Name</span>
            <input
              class="input"
              value={props.form.name}
              placeholder="my-provider"
              onInput={(event) => props.onChange({ name: event.currentTarget.value })}
            />
          </label>
          <label class="field">
            <span>API Key</span>
            <input
              class="input"
              type="password"
              value={props.form.api_key}
              placeholder="Required"
              onInput={(event) => props.onChange({ api_key: event.currentTarget.value })}
            />
          </label>
        </div>

        <Show when={selectedType()?.requires_base_url}>
          <label class="field">
            <span>Base URL</span>
            <input
              class="input"
              type="url"
              value={props.form.base_url}
              placeholder="https://api.example.com/v1"
              onInput={(event) => props.onChange({ base_url: event.currentTarget.value })}
            />
          </label>
        </Show>
      </div>
    </Dialog>
  );
}

function ConfirmDialog(props: {
  open: boolean;
  saving: boolean;
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
          <button class="btn" type="button" disabled={props.saving} onClick={props.onClose}>
            Cancel
          </button>
          <button class="btn btn-primary" type="button" disabled={props.saving} onClick={props.onConfirm}>
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

function DeleteProviderDialog(props: {
  provider: ProviderView | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={props.provider !== null}
      title="Delete Provider"
      onClose={props.onClose}
      footer={
        <>
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button class="btn btn-danger" type="button" onClick={props.onConfirm}>
            <Trash2 size={14} />
            Delete
          </button>
        </>
      }
    >
      <p>
        Are you sure you want to delete <span class="mono">{props.provider?.name}</span>?
      </p>
    </Dialog>
  );
}
