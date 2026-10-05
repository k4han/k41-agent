import { createMemo, createSignal, createEffect, For, Show } from "solid-js";
import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { ArrowLeft, Check, Copy, Plus, RefreshCw, Save, Search, Star, Trash2, Sparkles } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyButton } from "@/components/CopyButton";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getProviderTypes } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { parseModelList } from "@/lib/utils";
import type { ProviderRow, ProviderTypeOption, SettingInfo } from "@/types";

import { ProviderSettingsLayout as SettingsLayout } from "./ProviderSettingsLayout";
import { ProviderConnectionList, ProviderPicker, ProviderFormPanel } from "./ProviderConnections";
import { suggestProviderName } from "@/lib/providerConnections";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import {
  type PendingChange,
  SettingRow,
  SettingsConfirmDialog,
  settingsFromPayload,
  useSettingsData,
} from "./shared";

const DEFAULT_PROVIDER_KEY = "llm.default_model";

type ProviderFieldEntry = {
  key: string;
  info: SettingInfo;
};

type ProviderView = {
  name: string;
  catalogId: string;
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
  catalog_id?: string;
  isCustom?: boolean;
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
  const catalogOptions = getProviderTypes();
  if (catalogOptions.length) {
    return catalogOptions;
  }
  return [];
}

function providerLogoUrl(card: { id: string; catalogEntry?: { logo_url?: string | null } | null }): string {
  return card.catalogEntry?.logo_url || `https://models.dev/logos/${card.id}.svg`;
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
  const detailFields = fieldOrder
    .map((field) => fieldMap[field])
    .filter((entry): entry is ProviderFieldEntry => {
      if (!entry) {
        return false;
      }
      const name = fieldName(entry);
      if (name === "provider" || name === "type" || name === "catalog_id") {
        return false;
      }
      return name !== "base_url" || provider.requires_base_url;
    });
  const providerTypeField = fieldMap.type || fieldMap.provider;
  const enabledField = fieldMap.enabled;
  const defaultModelField = fieldMap.default_model;
  const modelsField = fieldMap.models;
  const searchable = provider.name.toLowerCase();

  return {
    name: provider.name,
    catalogId: provider.catalog_id || "",
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
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft, discardAll } =
    useSettingsData("/dashboard-api/providers");

  const params = useParams<{ providerName?: string }>();
  const navigate = useNavigate();
  const [query] = useSearchParams<{ new?: string }>();

  const [search, setSearch] = createSignal("");
  const [savingDefaultProvider, setSavingDefaultProvider] = createSignal(false);
  const [savingProvider, setSavingProvider] = createSignal(false);
  const [updatingCatalog, setUpdatingCatalog] = createSignal(false);
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [changesToConfirm, setChangesToConfirm] = createSignal<PendingChange[]>([]);
  const [deleteTarget, setDeleteTarget] = createSignal<ProviderView | null>(null);
  const [logoErrors, setLogoErrors] = createSignal<Record<string, boolean>>({});
  const { showToast } = useToast();

  const searchNeedle = createMemo(() => search().trim().toLowerCase());
  const dirtyKeys = createMemo<Set<string>>(() => new Set(pendingChanges().map((change: PendingChange) => change.key)));

  const typeOptions = createMemo(() => providerTypeOptions(data()?.provider_type_options));

  // Catalog items loaded from backend api.json
  const providersCatalog = createMemo(() => data()?.providers_catalog || {});

  const providerRows = createMemo<ProviderView[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return (payload.provider_rows || []).map((provider: ProviderRow) =>
      buildProviderView(
        provider,
        payload.provider_field_order || [],
        drafts(),
        dirtyKeys(),
        searchNeedle(),
      ),
    );
  });

  const catalogEntryFor = (row: ProviderView) =>
    Object.values(providersCatalog()).find((entry: any) =>
      entry.id.toLowerCase() === (row.catalogId || row.name).toLowerCase()) as any;
  const pickerItems = createMemo(() => Object.values(providersCatalog())
    .filter((entry: any) => !searchNeedle() || `${entry.id} ${entry.name}`.toLowerCase().includes(searchNeedle()))
    .map((entry: any) => ({ id: entry.id, label: entry.name, logoUrl: entry.logo_url,
      count: providerRows().filter((row) => catalogEntryFor(row)?.id === entry.id).length }))
    .sort((a, b) => a.label.localeCompare(b.label)));
  const connectionItems = createMemo(() => providerRows()
    .filter((row) => !searchNeedle() || `${row.name} ${catalogEntryFor(row)?.name || row.typeLabel} ${row.defaultModel}`.toLowerCase().includes(searchNeedle()))
    .map((row) => ({ name: row.name, label: catalogEntryFor(row)?.name || row.typeLabel,
      model: row.defaultModel, configured: row.ready, enabled: row.enabled, isDefault: row.isDefault,
      canSetDefault: row.canSetDefault, canDelete: row.canDelete,
      defaultReason: row.defaultBlockReason, deleteReason: row.deleteBlockReason })));

  const currentProviderName = () =>
    params.providerName ? decodeURIComponent(params.providerName) : null;

  const currentProvider = createMemo(() => {
    const selectedName = currentProviderName();
    if (!selectedName) return null;
    const rows = providerRows();
    return rows.find((provider) => provider.name.toLowerCase() === selectedName.toLowerCase()) || null;
  });

  const providerPendingChanges = (providerName: string): PendingChange[] =>
    pendingChanges().filter((change: PendingChange) => change.key.startsWith(`llm.providers.${providerName}.`));

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

  const syncCatalog = async () => {
    setUpdatingCatalog(true);
    try {
      const res = await postJson<{ status: string; message: string }>("/dashboard-api/providers/update-catalog", {});
      showToast(res.message, "success");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to sync catalog", "error");
    } finally {
      setUpdatingCatalog(false);
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
      await putJson(`/settings/${DEFAULT_PROVIDER_KEY}`, { value: `${provider.name}/${provider.defaultModel}` });
      showToast("Default provider updated.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to update default provider", "error");
    } finally {
      setSavingDefaultProvider(false);
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
      await load();
      if (params.providerName) {
        navigate("/settings/providers");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete provider", "error");
    }
  };

  useUnsavedChanges(() => pendingChanges().length > 0, discardAll);

  useCatalogAndLoad(load);

  return (
    <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <>
            <Show when={query.new} fallback={
            <Show
              when={params.providerName}
              fallback={
                <SettingsLayout
                  title="AI Model Providers"
                  breadcrumbLabel="Providers"
                  contentWidth="wide"
                  actions={
                    <button
                      class="btn"
                      type="button"
                      disabled={updatingCatalog()}
                      onClick={syncCatalog}
                      title="Sync provider catalog and models list from models.dev"
                    >
                      <RefreshCw size={14} class={updatingCatalog() ? "animate-spin" : ""} />
                      {updatingCatalog() ? "Syncing..." : "Sync Catalog"}
                    </button>
                  }
                >
                  <div class="stack">
                    <SettingsResourceToolbar
                      searchValue={search()}
                      searchPlaceholder="Search providers and models..."
                      onSearchInput={setSearch}
                      actions={
                        <button class="btn btn-primary" type="button" onClick={() => navigate("/settings/providers?tab=llm&new=custom")}>
                          <Plus size={15} />
                          Custom Provider
                        </button>
                      }
                    />

                    <ProviderConnectionList
                      items={connectionItems()} busy={savingDefaultProvider()}
                      onEdit={(name) => navigate(`/settings/providers/llm/${encodeURIComponent(name)}`)}
                      onSetDefault={(name) => { const row = providerRows().find((item) => item.name === name); if (row) void setDefaultProvider(row); }}
                      onDelete={(name) => setDeleteTarget(providerRows().find((item) => item.name === name) || null)}
                    />
                    <ProviderPicker items={pickerItems()} onSelect={(id) => navigate(`/settings/providers?tab=llm&new=${encodeURIComponent(id)}`)} />

                  </div>
                </SettingsLayout>
              }
            >
              <Show
                when={currentProvider()}
                fallback={
                  <SettingsLayout
                    title="Provider Not Found"
                    breadcrumbSegments={[
                      { label: "Providers", href: "/settings/providers?tab=llm" },
                      { label: currentProviderName() || "Unknown" },
                    ]}
                    actions={
                      <button class="btn" type="button" onClick={() => navigate("/settings/providers?tab=llm")}>
                        <ArrowLeft size={14} />
                        Back to Providers
                      </button>
                    }
                  >
                    <div class="panel empty provider-empty-panel">
                      <h3>Provider not found</h3>
                      <p class="hint">The provider "{currentProviderName()}" could not be found or has been removed.</p>
                    </div>
                  </SettingsLayout>
                }
              >
                {(provider) => (
                  <ProviderDetailPage
                    provider={provider()}
                    drafts={drafts()}
                    dirtyKeys={dirtyKeys()}
                    savingDefault={savingDefaultProvider()}
                    savingProvider={savingProvider()}
                    catalog={providersCatalog()}
                    logoErrors={logoErrors()}
                    onLogoError={(id) => setLogoErrors((curr) => ({ ...curr, [id]: true }))}
                    onBack={() => navigate("/settings/providers?tab=llm")}
                    onSetDefault={() => setDefaultProvider(provider())}
                    onLoadModels={() => loadProviderModels(provider().name)}
                    onSave={() => saveProviderChanges(provider().name)}
                    onChange={(key, value) => setDraft(key, value)}
                    onRestore={(key) => restoreDraft(key)}
                    onDelete={() => setDeleteTarget(provider())}
                  />
                )}
              </Show>
            </Show>

            }>
              <AIProviderCreatePage providerId={String(query.new || "")} catalog={providersCatalog()}
                names={providerRows().map((row) => row.name)} typeOptions={typeOptions()} onCreated={load} />
            </Show>

            <SettingsConfirmDialog
              open={confirmOpen()}
              saving={savingProvider()}
              changes={changesToConfirm()}
              settings={settingsFromPayload(payload)}
              onClose={() => setConfirmOpen(false)}
              onConfirm={saveConfirmedChanges}
            />

            <ConfirmDialog
              open={deleteTarget() !== null}
              title="Delete Provider"
              message={<p>Are you sure you want to delete provider <span class="mono">{deleteTarget()?.name}</span>?</p>}
              confirmLabel="Delete"
              confirmVariant="danger"
              onClose={() => setDeleteTarget(null)}
              onConfirm={deleteProvider}
            />
          </>
        )}
      </DataGate>
  );
}

function ProviderDetailPage(props: {
  provider: ProviderView;
  drafts: Record<string, unknown>;
  dirtyKeys: Set<string>;
  savingDefault: boolean;
  savingProvider: boolean;
  catalog: any;
  logoErrors: Record<string, boolean>;
  onLogoError: (id: string) => void;
  onBack: () => void;
  onSetDefault: () => void;
  onLoadModels: () => void;
  onSave: () => void;
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
  onDelete: () => void;
}) {
  // Try to find matching catalog item for models metadata
  const catalogEntry = createMemo<any>(() => {
    const id = (props.provider.catalogId || props.provider.name).toLowerCase();
    return Object.values(props.catalog).find((entry: any) => entry.id.toLowerCase() === id) || null;
  });
  const [modelSearch, setModelSearch] = createSignal("");
  const filteredModels = createMemo(() => {
    const models = catalogEntry()?.models || [];
    const needle = modelSearch().trim().toLowerCase();
    if (!needle) {
      return models;
    }
    return models.filter((model: any) => {
      const searchable = [
        model.name,
        model.id,
        ...(model.input_types || []),
        ...(model.output_types || []),
        model.reasoning ? "reasoning" : "",
        model.tool_call ? "tools tool calling" : "",
      ]
        .join(" ")
        .toLowerCase();
      return searchable.includes(needle);
    });
  });

  const { showToast } = useToast();

  const defaultModelKey = () =>
    props.provider.fieldMap.default_model?.key || `llm.providers.${props.provider.name}.default_model`;

  const modelsKey = () =>
    props.provider.fieldMap.models?.key || `llm.providers.${props.provider.name}.models`;

  const currentDefaultModel = createMemo(() => {
    const key = defaultModelKey();
    if (hasDraftValue(props.drafts, key)) {
      return textValue(props.drafts[key]);
    }
    return textValue(props.provider.fieldMap.default_model?.info.value) || props.provider.defaultModel || "";
  });

  const currentConfiguredModels = createMemo<string[]>(() => {
    const key = modelsKey();
    const raw = hasDraftValue(props.drafts, key)
      ? props.drafts[key]
      : props.provider.fieldMap.models?.info.value;
    if (Array.isArray(raw)) {
      return raw.map((item) => String(item).trim()).filter(Boolean);
    }
    if (typeof raw === "string") {
      return parseModelList(raw);
    }
    return [];
  });

  const handleSetDefaultModel = (modelId: string, modelName?: string) => {
    props.onChange(defaultModelKey(), modelId);
    const models = currentConfiguredModels();
    if (!models.includes(modelId)) {
      props.onChange(modelsKey(), [...models, modelId].join("\n"));
    }
    showToast(`Set "${modelName || modelId}" as default model.`);
  };

  const handleAddModel = (modelId: string, modelName?: string) => {
    const models = currentConfiguredModels();
    if (!models.includes(modelId)) {
      props.onChange(modelsKey(), [...models, modelId].join("\n"));
      showToast(`Added "${modelName || modelId}" to configured models.`);
    }
  };

  const [testingConnection, setTestingConnection] = createSignal(false);
  const [testResult, setTestResult] = createSignal<{
    ok: boolean;
    latency_ms?: number | null;
    message: string;
    error_code?: string | null;
  } | null>(null);

  const handleTestConnection = async () => {
    if (testingConnection()) return;
    setTestingConnection(true);
    setTestResult(null);
    try {
      const typeEntry = props.provider.fieldMap.type || props.provider.fieldMap.provider;
      const apiKeyEntry = props.provider.fieldMap.api_key;
      const baseUrlEntry = props.provider.fieldMap.base_url;
      const catalogEntry = props.provider.fieldMap.catalog_id;
      const payload: Record<string, unknown> = {
        name: props.provider.name,
        type: textValue(draftValue(props.drafts, typeEntry)) || props.provider.providerType,
      };
      if (apiKeyEntry && hasDraftValue(props.drafts, apiKeyEntry.key)) {
        payload.api_key = textValue(props.drafts[apiKeyEntry.key]);
      }
      if (baseUrlEntry && hasDraftValue(props.drafts, baseUrlEntry.key)) {
        payload.base_url = textValue(props.drafts[baseUrlEntry.key]);
      }
      if (catalogEntry && hasDraftValue(props.drafts, catalogEntry.key)) {
        payload.catalog_id = textValue(props.drafts[catalogEntry.key]);
      }
      const result = await postJson<{
        ok: boolean;
        message: string;
        latency_ms?: number | null;
        models?: string[];
        error_code?: string | null;
      }>("/dashboard-api/providers/verify", payload);
      setTestResult(result);
      if (result.ok) {
        showToast(result.message || `Connection OK (⚡ ${result.latency_ms ?? 0}ms)`);
      } else {
        showToast(result.message || "Connection test failed", "error");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Connection test failed";
      setTestResult({ ok: false, message: msg, error_code: "REQUEST_FAILED" });
      showToast(msg, "error");
    } finally {
      setTestingConnection(false);
    }
  };

  return (
    <SettingsLayout
      title={`Configure ${props.provider.name}`}
      description={`Manage credentials, models, and settings for ${props.provider.name}`}
      breadcrumbSegments={[
        { label: "Providers", href: "/settings/providers?tab=llm" },
        { label: props.provider.name },
      ]}
      contentWidth="wide"
      actions={
        <div class="row-wrap items-center">
          <button class="btn" type="button" onClick={props.onBack}>
            <ArrowLeft size={14} />
            Back to Providers
          </button>
          <button
            class="btn"
            type="button"
            disabled={testingConnection()}
            onClick={handleTestConnection}
            title="Test connection to provider"
          >
            <Show when={testingConnection()} fallback={<Sparkles size={13} />}>
              <RefreshCw size={13} class="spin" />
            </Show>
            {testingConnection() ? "Testing..." : "Test Connection"}
          </button>
          <Show when={testResult()}>
            <Show
              when={testResult()!.ok}
              fallback={
                <span
                  class="badge badge-danger test-result-badge"
                  title={testResult()!.message}
                >
                  {testResult()!.error_code || "Failed"}
                </span>
              }
            >
              <span
                class="badge badge-success test-result-badge"
                title={testResult()!.message}
              >
                ⚡ {testResult()!.latency_ms ?? 0}ms
              </span>
            </Show>
          </Show>
          <Show when={props.provider.canDelete}>
            <button class="btn btn-danger" type="button" onClick={props.onDelete}>
              <Trash2 size={13} />
              Delete Provider
            </button>
          </Show>
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
            Save changes{props.provider.dirtyCount ? ` (${props.provider.dirtyCount})` : ""}
          </button>
        </div>
      }
    >
      <div class="stack provider-detail-stack">
        <div class="panel provider-detail-panel">
          <div class="row-wrap provider-header-row">
            <div class="row-wrap provider-identity-row">
              <div class="logo-wrap">
                <Show
                  when={catalogEntry() && !props.logoErrors[props.provider.name.toLowerCase()]}
                  fallback={
                    <div class="logo-fallback">
                      {props.provider.name.charAt(0).toUpperCase()}
                    </div>
                  }
                >
                  <img
                    src={providerLogoUrl({
                      id: props.provider.name.toLowerCase(),
                      catalogEntry: catalogEntry(),
                    })}
                    alt={props.provider.name}
                    class="logo-img"
                    onError={() => props.onLogoError(props.provider.name.toLowerCase())}
                  />
                </Show>
              </div>
              <div>
                <h2 class="provider-title">{props.provider.name}</h2>
                <span class="chip">{props.provider.providerType}</span>
              </div>
            </div>
            <div class="row-wrap provider-badge-row">
              <Show when={props.provider.isDefault}>
                <span class="badge badge-info">Default Provider</span>
              </Show>
              <span class={props.provider.enabled ? "badge badge-success" : "badge badge-warning"}>
                {props.provider.enabled ? "Enabled" : "Disabled"}
              </span>
              <Show when={testResult()?.ok}>
                <span class="badge badge-success connection-latency-badge" title={testResult()!.message}>
                  ⚡ {testResult()!.latency_ms ?? 0}ms
                </span>
              </Show>
              <Show when={testResult() && !testResult()!.ok}>
                <span class="badge badge-danger" title={testResult()!.message}>
                  {testResult()!.error_code || "Connection Error"}
                </span>
              </Show>
              <Show when={props.provider.dirtyCount > 0}>
                <span class="badge badge-warning">{props.provider.dirtyCount} unsaved</span>
              </Show>
            </div>
          </div>

          <div class="provider-summary-grid">
            <div>
              <span class="setting-detail-label">Type</span>
              <span class="chip">{props.provider.providerType}</span>
            </div>
            <div>
              <span class="setting-detail-label">Default Model</span>
              <span class="provider-summary-value mono">{props.provider.defaultModel || "Not set"}</span>
            </div>
            <div>
              <span class="setting-detail-label">Models List</span>
              <span class="provider-summary-value">{props.provider.modelCount}</span>
            </div>
            <div>
              <span class="setting-detail-label">Status</span>
              <span class={props.provider.enabled ? "badge badge-success" : "badge badge-warning"}>
                {props.provider.enabled ? "Active" : "Disabled"}
              </span>
            </div>
          </div>
        </div>

        <div class="panel provider-detail-panel">
          <h3 class="provider-section-heading">Provider Settings</h3>
          <div class="settings-list">
            <For each={props.provider.detailFields} fallback={<div class="empty">No settings found.</div>}>
              {(entry) => (
                <SettingRow
                  settingKey={entry.key}
                  info={entry.info}
                  draft={props.drafts[entry.key]}
                  dirty={props.dirtyKeys.has(entry.key)}
                  showDescription={false}
                  trimProviderPrefix
                  actions={
                    entry.key.endsWith(".models") ? (
                      <button class="btn btn-sm" type="button" onClick={props.onLoadModels}>
                        <RefreshCw size={13} />
                        Fetch Active Models
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

        {/* Detailed Model Metadata Section from models.dev */}
        <Show when={catalogEntry()?.models?.length > 0}>
          <div class="panel provider-detail-panel">
            <div class="model-spec-toolbar">
              <div class="model-spec-title">
                <Sparkles size={14} class="model-spec-sparkles" />
                Model Specifications & Capabilities
              </div>
              <label class="model-spec-search">
                <Search size={14} />
                <input
                  class="input"
                  type="search"
                  aria-label="Search models"
                  value={modelSearch()}
                  placeholder="Search models..."
                  onInput={(event) => setModelSearch(event.currentTarget.value)}
                />
              </label>
            </div>
            <div class="model-spec-grid">
              <For each={filteredModels()} fallback={<div class="model-spec-empty">No models match your search.</div>}>
                {(model) => {
                  const isDefault = () => currentDefaultModel() === model.id;
                  const isConfigured = () => currentConfiguredModels().includes(model.id);

                  return (
                    <div
                      class="model-spec-card"
                      classList={{
                        "is-default": isDefault(),
                        "is-configured": isConfigured(),
                      }}
                    >
                      <div class="model-spec-header">
                        <div class="model-spec-title-wrap">
                          <span class="model-spec-name" title={model.name}>{model.name}</span>
                          <span class="model-spec-id mono" title={model.id}>{model.id}</span>
                        </div>
                        <CopyButton
                          value={String(model.id || model.name || "")}
                          class="btn btn-icon btn-sm model-spec-copy-btn"
                          ariaLabel={`Copy model ID ${model.id || model.name}`}
                          title={`Copy ${model.id || model.name}`}
                          successMessage="Model ID copied."
                        >
                          {(state) => (
                            <Show when={state.copied()} fallback={<Copy size={13} aria-hidden="true" />}>
                              <Check size={13} aria-hidden="true" />
                            </Show>
                          )}
                        </CopyButton>
                      </div>

                      <div class="model-spec-badges">
                        <Show when={isDefault()}>
                          <span class="badge badge-info spec-badge-status">Default</span>
                        </Show>
                        <Show when={isConfigured() && !isDefault()}>
                          <span class="badge badge-success spec-badge-status">In Config</span>
                        </Show>
                        <Show when={model.context_window}>
                          <span class="spec-badge spec-badge-context">
                            🧠 {model.context_window >= 1048576 ? `${(model.context_window / 1048576).toFixed(0)}M` : `${(model.context_window / 1024).toFixed(0)}k`} context
                          </span>
                        </Show>
                        <Show when={model.reasoning}>
                          <span class="spec-badge spec-badge-reasoning">🧠 Reasoning</span>
                        </Show>
                        <Show when={model.tool_call}>
                          <span class="spec-badge spec-badge-tools">🛠️ Tools</span>
                        </Show>
                        <For each={model.input_types}>
                          {(mod) => <span class="spec-badge spec-badge-modality">{mod}</span>}
                        </For>
                      </div>

                      <Show when={model.cost_input !== null && model.cost_input !== undefined}>
                        <div class="model-spec-cost">
                          Cost/1M tokens: <span class="cost-number">${model.cost_input}</span> In / <span class="cost-number">${model.cost_output}</span> Out
                        </div>
                      </Show>

                      <div class="model-spec-actions">
                        <button
                          class="btn btn-sm"
                          classList={{ "btn-primary": !isDefault() }}
                          type="button"
                          disabled={isDefault()}
                          title={isDefault() ? "Current default model" : "Set as provider default model"}
                          onClick={() => handleSetDefaultModel(model.id, model.name)}
                        >
                          <Star size={12} fill={isDefault() ? "currentColor" : "none"} />
                          {isDefault() ? "Default" : "Set Default"}
                        </button>

                        <button
                          class="btn btn-sm"
                          type="button"
                          disabled={isConfigured()}
                          title={isConfigured() ? "Model already in configured list" : "Add model to configured list"}
                          onClick={() => handleAddModel(model.id, model.name)}
                        >
                          <Show when={isConfigured()} fallback={<Plus size={12} />}>
                            <Check size={12} />
                          </Show>
                          {isConfigured() ? "Added" : "Add to Models"}
                        </button>
                      </div>
                    </div>
                  );
                }}
              </For>
            </div>
          </div>
        </Show>
      </div>
    </SettingsLayout>
  );
}

function AIProviderCreatePage(props: {
  providerId: string; catalog: Record<string, any>; names: string[];
  typeOptions: ProviderTypeOption[]; onCreated: () => Promise<unknown>;
}) {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const entry = () => Object.values(props.catalog).find((item: any) => item.id === props.providerId);
  const isCustom = () => props.providerId === "custom";
  const [form, setForm] = createSignal<ProviderCreateForm>({ name: "", type: "google", api_key: "", base_url: "" });
  const [initial, setInitial] = createSignal(form());
  const [busy, setBusy] = createSignal(false);
  const [verifying, setVerifying] = createSignal(false);
  const [verificationResult, setVerificationResult] = createSignal<{
    ok: boolean;
    message: string;
    latency_ms?: number | null;
    models?: string[];
    suggested_default_model?: string;
    error_code?: string | null;
    details?: any;
  } | null>(null);
  const [discoveredModels, setDiscoveredModels] = createSignal<string[]>([]);
  const [selectedDefaultModel, setSelectedDefaultModel] = createSignal("");

  createEffect(() => {
    const item = entry();
    const next = { name: suggestProviderName(isCustom() ? "custom" : props.providerId, props.names, true),
      type: item?.provider_type || "google", api_key: "", base_url: item?.base_url || "",
      catalog_id: item?.id, isCustom: isCustom() };
    setForm(next); setInitial(next);
    setVerificationResult(null);
    setDiscoveredModels([]);
    setSelectedDefaultModel("");
  });

  const patch = (values: Partial<ProviderCreateForm>) => setForm((current) => ({ ...current, ...values }));
  useUnsavedChanges(
    () => JSON.stringify(form()) !== JSON.stringify(initial()),
    () => { setForm(initial()); setVerificationResult(null); setDiscoveredModels([]); setSelectedDefaultModel(""); }
  );

  const verifyConnection = async () => {
    if (verifying() || busy()) return;
    const { type, api_key, base_url, catalog_id } = form();
    if (!api_key.trim()) {
      showToast("Please enter an API key before verifying connection.", "error");
      return;
    }
    setVerifying(true);
    setVerificationResult(null);
    try {
      const result = await postJson<{
        ok: boolean;
        message: string;
        latency_ms?: number | null;
        models?: string[];
        suggested_default_model?: string;
        error_code?: string | null;
        details?: any;
      }>("/dashboard-api/providers/verify", {
        type,
        api_key: api_key.trim(),
        base_url: base_url.trim(),
        catalog_id,
      });
      setVerificationResult(result);
      if (result.ok) {
        const models = result.models || [];
        setDiscoveredModels(models);
        const suggested = result.suggested_default_model || (models.length > 0 ? models[0] : "");
        setSelectedDefaultModel(suggested);
        showToast(result.message || `Connected successfully (⚡ ${result.latency_ms ?? 0}ms)`);
      } else {
        showToast(result.message || "Connection verification failed.", "error");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Verification request failed.";
      setVerificationResult({
        ok: false,
        message: msg,
        error_code: "REQUEST_FAILED",
      });
      showToast(msg, "error");
    } finally {
      setVerifying(false);
    }
  };

  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy()) return;
    // Verification is optional: allow offline save or custom endpoints
    // that cannot list models. Backend does not require verify.
    if (!selectedDefaultModel() && discoveredModels().length === 0) {
      showToast("Saving without verified models. You can set the default model manually.", "warning");
    }
    setBusy(true);
    try {
      const { name, type, api_key, base_url, catalog_id } = form();
      await postJson("/dashboard-api/providers", {
        name,
        type,
        api_key,
        base_url,
        catalog_id,
        default_model: selectedDefaultModel(),
        models: discoveredModels(),
      });
      setInitial(form());
      await props.onCreated();
      showToast("Provider configuration saved.");
      navigate("/settings/providers?tab=llm");
    } catch (err) { showToast(err instanceof Error ? err.message : "Failed to save provider.", "error"); }
    finally { setBusy(false); }
  };
  return (
    <SettingsLayout
      title={`Add ${entry()?.name || "AI Provider"}`}
      breadcrumbSegments={[
        { label: "Providers", href: "/settings/providers?tab=llm" },
        { label: "Add Provider" },
      ]}
      contentWidth="wide"
    >
      <Show when={isCustom() || entry()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" type="button" onClick={() => navigate("/settings/providers?tab=llm")}>Back to Providers</button></div>}>
        <ProviderFormPanel title={`Add ${entry()?.name || "Custom Provider"}`} busy={busy()} onSubmit={save} onBack={() => navigate("/settings/providers?tab=llm")}>
          <label class="stack">Configuration name<input class="input" required pattern="[A-Za-z0-9_-]+" value={form().name} onInput={(event) => patch({ name: event.currentTarget.value })} /></label>
          <Show when={isCustom()}>
            <label class="stack">Provider type<select class="input" value={form().type} onChange={(event) => patch({ type: event.currentTarget.value, base_url: "" })}><For each={props.typeOptions}>{(option) => <option value={option.value}>{option.label}</option>}</For></select></label>
          </Show>
          <label class="stack">API key<input class="input" type="password" required autocomplete="new-password" value={form().api_key} onInput={(event) => patch({ api_key: event.currentTarget.value })} /></label>
          <Show when={form().type === "openai_compatible" || form().base_url}>
            <label class="stack">Base URL<input class="input" type="url" required={form().type === "openai_compatible"} value={form().base_url} onInput={(event) => patch({ base_url: event.currentTarget.value })} /></label>
          </Show>
          <div class="connection-verify-bar">
            <button
              class="btn btn-secondary"
              type="button"
              disabled={verifying() || !form().api_key.trim()}
              onClick={verifyConnection}
            >
              <Show when={verifying()} fallback={<Sparkles size={14} />}>
                <RefreshCw size={14} class="spin" />
              </Show>
              {verifying() ? "Testing & Discovering..." : "Test Connection & Discover Models"}
            </button>
            <Show when={verificationResult()?.ok}>
              <span class="badge badge-success connection-latency-badge">
                ⚡ {verificationResult()?.latency_ms ?? 0}ms
              </span>
            </Show>
          </div>
          <Show when={verificationResult() && !verificationResult()!.ok}>
            <div class="connection-error-alert" role="alert">
              <div class="row-wrap items-center gap-2">
                <span class="badge badge-danger">
                  {verificationResult()!.error_code || "FAILED"}
                </span>
                <span class="connection-error-msg">{verificationResult()!.message}</span>
              </div>
            </div>
          </Show>
          <Show when={verificationResult()?.ok}>
            <div class="connection-success-panel">
              <div class="row-wrap items-center gap-2">
                <span class="badge badge-success">Connection Established</span>
                <span class="hint">Discovered {discoveredModels().length} model{discoveredModels().length === 1 ? "" : "s"}</span>
              </div>
              <Show when={discoveredModels().length > 0}>
                <label class="stack">
                  <span>Default Model</span>
                  <select
                    class="input model-discovery-dropdown"
                    value={selectedDefaultModel()}
                    onChange={(event) => setSelectedDefaultModel(event.currentTarget.value)}
                  >
                    <For each={discoveredModels()}>
                      {(modelId) => <option value={modelId}>{modelId}</option>}
                    </For>
                  </select>
                  <span class="hint">Recommended default model automatically pre-selected.</span>
                </label>
              </Show>
              <Show when={discoveredModels().length === 0}>
                <label class="stack">
                  <span>Default Model (manual)</span>
                  <input
                    class="input mono"
                    placeholder="e.g. gpt-4o-mini"
                    value={selectedDefaultModel()}
                    onInput={(event) => setSelectedDefaultModel(event.currentTarget.value)}
                  />
                  <span class="hint">No models discovered. Enter the default model manually (optional).</span>
                </label>
              </Show>
            </div>
          </Show>
          <Show when={!verificationResult()?.ok}>
            <p class="hint">Test connection above to verify credentials and discover available models automatically.</p>
            <label class="stack">
              <span>Default Model (manual, optional)</span>
              <input
                class="input mono"
                placeholder="e.g. gpt-4o-mini — leave empty to save without a default model"
                value={selectedDefaultModel()}
                onInput={(event) => setSelectedDefaultModel(event.currentTarget.value)}
              />
              <span class="hint">You can save without verification (offline or custom endpoint). Set the model manually if known.</span>
            </label>
          </Show>
        </ProviderFormPanel>
      </Show>
    </SettingsLayout>
  );
}


