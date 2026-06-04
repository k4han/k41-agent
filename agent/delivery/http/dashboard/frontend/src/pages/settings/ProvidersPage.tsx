import { createMemo, createSignal, createEffect, For, Show } from "solid-js";
import { Check, Copy, Edit3, Plus, RefreshCw, Save, Search, ShieldAlert, Star, Trash2, Globe, Shield, Coins, Sparkles, Sliders } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyButton } from "@/components/CopyButton";
import { Dialog } from "@/components/Dialog";
import { ModelPicker } from "@/components/ModelPicker";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getProviderTypes } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import type { ModelCatalog, ProviderRow, ProviderTypeOption, SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  ChangesPreview,
  type PendingChange,
  SettingRow,
  SettingsConfirmDialog,
  settingsFromPayload,
  useSettingsData,
} from "./shared";

const DEFAULT_PROVIDER_KEY = "llm.default_provider";

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
  isCustom?: boolean;
};

// Grouped by connection state instead of static categories

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
      if (name === "provider" || name === "type") {
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
  const [updatingCatalog, setUpdatingCatalog] = createSignal(false);
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [changesToConfirm, setChangesToConfirm] = createSignal<PendingChange[]>([]);
  const [deleteTarget, setDeleteTarget] = createSignal<ProviderView | null>(null);
  const [logoErrors, setLogoErrors] = createSignal<Record<string, boolean>>({});
  const [fallbackProvider, setFallbackProvider] = createSignal("");
  const [fallbackModel, setFallbackModel] = createSignal("");
  const [fallbackInitialized, setFallbackInitialized] = createSignal(false);
  const [savingFallback, setSavingFallback] = createSignal(false);
  const { showToast } = useToast();

  const searchNeedle = createMemo(() => search().trim().toLowerCase());
  const dirtyKeys = createMemo(() => new Set(pendingChanges().map((change) => change.key)));

  const typeOptions = createMemo(() => providerTypeOptions(data()?.provider_type_options));

  // Catalog items loaded from backend api.json
  const providersCatalog = createMemo(() => data()?.providers_catalog || {});

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

  // Derived catalog cards that represent all possible cards in the grid
  const providerCards = createMemo(() => {
    const cat = providersCatalog();
    const rows = providerRows();
    const needle = searchNeedle();

    // Map each catalog entry to a card representation
    const cards = Object.keys(cat).map((id) => {
      const entry = cat[id];
      const configured = rows.find((r) => r.name.toLowerCase() === id.toLowerCase());

      const searchable = [entry.id, entry.name].join(" ").toLowerCase();

      return {
        id: entry.id,
        name: entry.name,
        providerType: entry.provider_type,
        baseUrl: entry.base_url,
        envVars: entry.env_vars || [],
        docUrl: entry.doc_url,
        defaultModel: configured?.defaultModel || entry.default_model,
        modelCount: configured?.modelCount || entry.models?.length || 0,
        configured: !!configured,
        enabled: configured ? configured.enabled : false,
        isDefault: configured ? configured.isDefault : false,
        dirtyCount: configured ? configured.dirtyCount : 0,
        matchesSearch: !needle || searchable.includes(needle),
        configuredRow: configured || null,
        catalogEntry: entry,
      };
    });

    // If there are configured rows not in catalog, append them as custom providers
    rows.forEach((row) => {
      const alreadyInCatalog = Object.keys(cat).some((id) => id.toLowerCase() === row.name.toLowerCase());
      if (!alreadyInCatalog) {
        const searchable = row.name.toLowerCase();

        cards.push({
          id: row.name.toLowerCase(),
          name: row.name,
          providerType: row.providerType,
          baseUrl: "",
          envVars: [],
          docUrl: "",
          defaultModel: row.defaultModel,
          modelCount: row.modelCount,
          configured: true,
          enabled: row.enabled,
          isDefault: row.isDefault,
          dirtyCount: row.dirtyCount,
          matchesSearch: !needle || searchable.includes(needle),
          configuredRow: row,
          catalogEntry: null,
        });
      }
    });

    // Sort cards: configured/enabled first, then alphabetically
    cards.sort((a, b) => {
      if (a.configured !== b.configured) {
        return a.configured ? -1 : 1;
      }
      if (a.enabled !== b.enabled) {
        return a.enabled ? -1 : 1;
      }
      return a.name.localeCompare(b.name);
    });

    return cards;
  });

  const connectedProviderCards = createMemo(() =>
    providerCards().filter((card) => card.matchesSearch && card.configured),
  );

  const unconnectedProviderCards = createMemo(() =>
    providerCards().filter((card) => card.matchesSearch && !card.configured),
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
    setAddForm({ name: "", type: "google", api_key: "", base_url: "", isCustom: true });
    setAddOpen(true);
  };

  const handleCardClick = (card: any) => {
    if (card.configured) {
      setSelectedProviderName(card.configuredRow?.name ?? card.id);
    } else {
      // Setup helper prefilled from catalog
      setAddForm({
        name: card.id,
        type: card.providerType,
        api_key: "",
        base_url: card.baseUrl || "",
        isCustom: false,
      });
      setAddOpen(true);
    }
  };

  // Sync fallback values from server-side settings on the first data load.
  createEffect(() => {
    const payload = data();
    if (!payload || fallbackInitialized()) {
      return;
    }
    const settings = settingsFromPayload(payload);
    const serverProvider = (settings["llm.fallback.provider"]?.value as string) ?? "";
    const serverModel = (settings["llm.fallback.model"]?.value as string) ?? "";
    setFallbackProvider(String(serverProvider));
    setFallbackModel(String(serverModel));
    setFallbackInitialized(true);
  });

  const fallbackDirty = createMemo(() => {
    const payload = data();
    if (!payload) {
      return false;
    }
    const settings = settingsFromPayload(payload);
    const serverProvider = String(settings["llm.fallback.provider"]?.value ?? "").trim();
    const serverModel = String(settings["llm.fallback.model"]?.value ?? "").trim();
    return (
      fallbackProvider().trim() !== serverProvider || fallbackModel().trim() !== serverModel
    );
  });

  const saveFallback = async () => {
    setSavingFallback(true);
    try {
      await putJson("/settings", {
        values: {
          "llm.fallback.provider": fallbackProvider().trim(),
          "llm.fallback.model": fallbackModel().trim(),
        },
      });
      showToast("Fallback model updated.");
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save fallback",
        "error",
      );
    } finally {
      setSavingFallback(false);
    }
  };

  const clearFallback = () => {
    setFallbackProvider("");
    setFallbackModel("");
  };

  useCatalogAndLoad(load);

  return (
    <SettingsLayout
      title="Provider Configuration"
      subtitle="Manage default provider and per-provider LLM credentials/models."
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
      <style>{`
        .providers-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 20px;
          margin-top: 15px;
          margin-bottom: 30px;
        }
        @media (max-width: 1200px) {
          .providers-grid {
            grid-template-columns: repeat(3, 1fr);
          }
        }
        @media (max-width: 900px) {
          .providers-grid {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 600px) {
          .providers-grid {
            grid-template-columns: repeat(1, 1fr);
          }
        }
        .provider-section-title {
          font-size: 13px;
          font-weight: 700;
          color: var(--muted);
          text-transform: uppercase;
          letter-spacing: 0.05em;
          margin: 25px 0 15px 0;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .provider-section-title::before {
          content: "";
          display: inline-block;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent, #6366f1);
        }
        .provider-card {
          background: rgba(30, 41, 59, 0.45);
          backdrop-filter: blur(10px);
          -webkit-backdrop-filter: blur(10px);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 12px;
          padding: 12px 16px;
          display: flex;
          flex-direction: row;
          align-items: center;
          gap: 12px;
          min-height: 64px;
          transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
          cursor: pointer;
          position: relative;
          overflow: hidden;
        }
        .provider-card:hover {
          transform: translateY(-2px);
          border-color: var(--accent, #6366f1);
          background: rgba(30, 41, 59, 0.65);
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2), 0 0 10px rgba(99, 102, 241, 0.1);
        }
        .provider-card.card-configured {
          border-color: rgba(99, 102, 241, 0.35);
          background: rgba(99, 102, 241, 0.03);
        }
        .provider-card.card-configured:hover {
          border-color: var(--accent, #6366f1);
          background: rgba(99, 102, 241, 0.06);
        }
        .provider-card.card-enabled {
          border-color: rgba(16, 185, 129, 0.35);
          background: rgba(16, 185, 129, 0.03);
        }
        .provider-card.card-enabled:hover {
          border-color: #10b981;
          background: rgba(16, 185, 129, 0.06);
        }
        .logo-wrap {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 40px;
          height: 40px;
          border-radius: 8px;
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.06);
          overflow: hidden;
          padding: 3px;
          flex-shrink: 0;
        }
        .dark .logo-wrap {
          background: rgba(250, 250, 250, 0.9);
          border-color: rgba(255, 255, 255, 0.22);
        }
        .logo-img {
          width: 100%;
          height: 100%;
          object-fit: contain;
        }
        .logo-fallback {
          font-weight: 700;
          font-size: 16px;
          color: var(--accent, #6366f1);
          background: rgba(99, 102, 241, 0.1);
          width: 100%;
          height: 100%;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 6px;
        }
        .dark .logo-fallback {
          color: #111827;
          background: rgba(99, 102, 241, 0.14);
        }
        .card-right {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
          flex: 1;
        }
        .card-title {
          font-size: 13.5px;
          font-weight: 600;
          color: var(--fg);
          line-height: 1.2;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .card-status-row {
          display: flex;
          align-items: center;
        }
        .status-no-connection {
          font-size: 11px;
          font-weight: 500;
          color: #8a8a8a;
        }
        .status-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 2px 8px;
          border-radius: 9999px;
          font-size: 10.5px;
          font-weight: 600;
        }
        .status-dot {
          display: inline-block;
          width: 5px;
          height: 5px;
          border-radius: 50%;
        }
        .status-active {
          background: rgba(16, 185, 129, 0.15);
          color: #10b981;
        }
        .status-active .status-dot {
          background: #10b981;
        }
        .model-spec-section {
          margin-top: 24px;
          border-top: 1px solid rgba(255, 255, 255, 0.08);
          padding-top: 18px;
        }
        .model-spec-toolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 12px;
        }
        .model-spec-title {
          font-size: 13px;
          font-weight: 700;
          color: var(--fg);
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .model-spec-search {
          position: relative;
          display: flex;
          align-items: center;
          width: min(100%, 280px);
        }
        .model-spec-search svg {
          position: absolute;
          left: 10px;
          color: var(--muted);
          pointer-events: none;
        }
        .model-spec-search .input {
          width: 100%;
          padding-left: 32px;
        }
        .model-spec-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
          gap: 12px;
          max-height: 260px;
          overflow-y: auto;
          padding-right: 4px;
        }
        .model-spec-card {
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.04);
          border-radius: 8px;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .model-spec-button {
          color: inherit;
          cursor: pointer;
          font: inherit;
          text-align: left;
        }
        .model-spec-button:hover {
          border-color: rgba(99, 102, 241, 0.35);
          background: rgba(99, 102, 241, 0.05);
        }
        .model-spec-button:focus-visible {
          outline: 2px solid var(--border-strong);
          outline-offset: 2px;
        }
        .model-spec-header {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 2px 8px;
        }
        .model-spec-copy {
          grid-row: 1 / span 2;
          grid-column: 2;
          align-self: center;
          color: var(--muted);
        }
        .model-spec-name {
          font-size: 13px;
          font-weight: 600;
          color: var(--fg);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .model-spec-id {
          font-size: 10px;
          color: var(--muted);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .model-spec-badges {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
        }
        .spec-badge {
          font-size: 9px;
          font-weight: 600;
          padding: 1px 6px;
          border-radius: 4px;
          text-transform: capitalize;
        }
        .spec-badge-context {
          background: rgba(59, 130, 246, 0.1);
          color: #60a5fa;
          border: 1px solid rgba(59, 130, 246, 0.15);
        }
        .spec-badge-reasoning {
          background: rgba(168, 85, 247, 0.1);
          color: #c084fc;
          border: 1px solid rgba(168, 85, 247, 0.15);
        }
        .spec-badge-tools {
          background: rgba(234, 179, 8, 0.1);
          color: #facc15;
          border: 1px solid rgba(234, 179, 8, 0.15);
        }
        .spec-badge-modality {
          background: rgba(255, 255, 255, 0.04);
          color: var(--muted);
          border: 1px solid rgba(255, 255, 255, 0.06);
        }
        .model-spec-cost {
          font-size: 10.5px;
          color: var(--muted);
          margin-top: auto;
          border-top: 1px dashed rgba(255, 255, 255, 0.04);
          padding-top: 6px;
        }
        .cost-number {
          font-weight: 600;
          color: var(--fg);
        }
        .model-spec-empty {
          grid-column: 1 / -1;
          padding: 24px 12px;
          color: var(--muted);
          font-size: 12px;
          text-align: center;
        }
        @media (max-width: 640px) {
          .model-spec-toolbar {
            align-items: stretch;
            flex-direction: column;
          }
          .model-spec-search {
            width: 100%;
          }
        }
      `}</style>

      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={search()}
              searchPlaceholder="Search providers and models..."
              onSearchInput={setSearch}
              actions={
                <button class="btn btn-primary" type="button" onClick={openAddDialog}>
                  <Plus size={15} />
                  Custom Provider
                </button>
              }
            />

            <FallbackModelSection
              catalogs={payload.model_catalogs || []}
              providerNames={payload.provider_name_options || payload.provider_names || []}
              defaultProvider={payload.default_provider || ""}
              defaultModel={payload.default_model || ""}
              provider={fallbackProvider()}
              model={fallbackModel()}
              dirty={fallbackDirty()}
              saving={savingFallback()}
              onProviderModelChange={(nextProvider, nextModel) => {
                setFallbackProvider(nextProvider);
                setFallbackModel(nextModel);
              }}
              onSave={saveFallback}
              onClear={clearFallback}
            />

            {/* Connected Providers Grid */}
            <Show when={connectedProviderCards().length > 0}>
              <div>
                <div class="provider-section-title">Connected Providers</div>
                <div class="providers-grid">
                  <For each={connectedProviderCards()}>
                    {(card) => (
                      <div
                        class={`provider-card ${card.configured ? "card-configured" : ""} ${
                          card.enabled ? "card-enabled" : ""
                        }`}
                        onClick={() => handleCardClick(card)}
                      >
                        <div class="logo-wrap">
                          <Show
                            when={!logoErrors()[card.id]}
                            fallback={
                              <div class="logo-fallback">
                                {card.name.charAt(0).toUpperCase()}
                              </div>
                            }
                          >
                            <img
                              src={providerLogoUrl(card)}
                              alt={card.name}
                              class="logo-img"
                              onError={() => setLogoErrors((curr) => ({ ...curr, [card.id]: true }))}
                            />
                          </Show>
                        </div>
                        <div class="card-right">
                          <div class="card-title">{card.name}</div>
                          <div class="card-status-row">
                            <Show
                              when={card.configured && card.enabled}
                              fallback={<span class="status-no-connection">No connections</span>}
                            >
                              <div class="status-pill status-active">
                                <span class="status-dot"></span>
                                1 Connected
                              </div>
                            </Show>
                          </div>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </div>
            </Show>

            {/* Available Providers Grid */}
            <Show when={unconnectedProviderCards().length > 0}>
              <div>
                <div class="provider-section-title">Available Providers</div>
                <div class="providers-grid">
                  <For each={unconnectedProviderCards()}>
                    {(card) => (
                      <div
                        class={`provider-card ${card.configured ? "card-configured" : ""} ${
                          card.enabled ? "card-enabled" : ""
                        }`}
                        onClick={() => handleCardClick(card)}
                      >
                        <div class="logo-wrap">
                          <Show
                            when={!logoErrors()[card.id]}
                            fallback={
                              <div class="logo-fallback">
                                {card.name.charAt(0).toUpperCase()}
                              </div>
                            }
                          >
                            <img
                              src={providerLogoUrl(card)}
                              alt={card.name}
                              class="logo-img"
                              onError={() => setLogoErrors((curr) => ({ ...curr, [card.id]: true }))}
                            />
                          </Show>
                        </div>
                        <div class="card-right">
                          <div class="card-title">{card.name}</div>
                          <div class="card-status-row">
                            <Show
                              when={card.configured && card.enabled}
                              fallback={<span class="status-no-connection">No connections</span>}
                            >
                              <div class="status-pill status-active">
                                <span class="status-dot"></span>
                                1 Connected
                              </div>
                            </Show>
                          </div>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </div>
            </Show>

            {/* Empty view */}
            <Show when={providerCards().filter((card) => card.matchesSearch).length === 0}>
              <div class="panel empty" style={{ padding: "40px 20px", "text-align": "center" }}>
                <Globe size={32} class="muted" style={{ "margin-bottom": "10px" }} />
                <h3>No providers match your search</h3>
                <p class="hint">Try searching for other brand names or sync the catalog.</p>
              </div>
            </Show>

            <Show when={selectedProvider()}>
              {(provider) => (
                <ProviderEditDialog
                  provider={provider()}
                  drafts={drafts()}
                  dirtyKeys={dirtyKeys()}
                  savingDefault={savingDefaultProvider()}
                  savingProvider={savingProvider()}
                  catalog={providersCatalog()}
                  onClose={() => setSelectedProviderName(null)}
                  onSetDefault={() => setDefaultProvider(provider())}
                  onLoadModels={() => loadProviderModels(provider().name)}
                  onSave={() => saveProviderChanges(provider().name)}
                  onChange={(key, value) => setDraft(key, value)}
                  onRestore={(key) => restoreDraft(key)}
                  onDelete={() => setDeleteTarget(provider())}
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
  catalog: any;
  onClose: () => void;
  onSetDefault: () => void;
  onLoadModels: () => void;
  onSave: () => void;
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
  onDelete: () => void;
}) {
  // Try to find matching catalog item for models metadata
  const catalogEntry = createMemo(() => {
    const nameLower = props.provider.name.toLowerCase();
    const typeLower = props.provider.providerType.toLowerCase();
    return props.catalog[nameLower] || props.catalog[typeLower] || null;
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

  return (
    <Dialog
      open
      title={`Configure ${props.provider.name}`}
      wide
      onClose={props.onClose}
      footer={
        <>
          <Show when={props.provider.canDelete}>
            <button class="btn btn-danger" type="button" onClick={props.onDelete} style={{ "margin-right": "auto" }}>
              <Trash2 size={13} />
              Delete Provider
            </button>
          </Show>
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

        {/* Detailed Model Metadata Section from models.dev */}
        <Show when={catalogEntry() && catalogEntry().models?.length > 0}>
          <div class="model-spec-section">
            <div class="model-spec-toolbar">
              <div class="model-spec-title">
                <Sparkles size={14} style={{ color: "var(--accent, #6366f1)" }} />
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
                {(model) => (
                  <CopyButton
                    value={String(model.id || model.name || "")}
                    class="model-spec-card model-spec-button"
                    ariaLabel={`Copy model ID ${model.id || model.name}`}
                    title={`Copy ${model.id || model.name}`}
                    successMessage="Model ID copied."
                    failureMessage="Could not copy model ID."
                  >
                    {(state) => (
                      <>
                        <div class="model-spec-header">
                          <span class="model-spec-name">{model.name}</span>
                          <span class="model-spec-id mono">{model.id}</span>
                          <Show when={state.copied()} fallback={<Copy size={13} class="model-spec-copy" aria-hidden="true" />}>
                            <Check size={13} class="model-spec-copy" aria-hidden="true" />
                          </Show>
                        </div>
                        <div class="model-spec-badges">
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
                      </>
                    )}
                  </CopyButton>
                )}
              </For>
            </div>
          </div>
        </Show>
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
      title={`Configure ${props.form.name || "New Provider"}`}
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
            Save & Enable
          </button>
        </>
      }
    >
      <div class="stack">
        <Show when={props.form.isCustom}>
          <div class="provider-type-grid" style={{ "margin-bottom": "20px" }}>
            <For each={props.typeOptions}>
              {(option) => (
                <button
                  class={`provider-type-option ${props.form.type === option.value ? "active" : ""}`}
                  type="button"
                  onClick={() => props.onChange({ type: option.value, base_url: "" })}
                  title={option.description}
                >
                  <span class="setting-title">{option.label}</span>
                </button>
              )}
            </For>
          </div>
        </Show>

        <div class="grid-2">
          <label class="field">
            <span>Provider ID</span>
            <input
              class="input mono"
              value={props.form.name}
              placeholder="e.g. deepseek, groq"
              disabled={!props.form.isCustom}
              onInput={(event) => props.onChange({ name: event.currentTarget.value })}
            />
          </label>
          <label class="field">
            <span>API Key</span>
            <input
              class="input"
              type="password"
              value={props.form.api_key}
              placeholder="Paste your credentials here"
              onInput={(event) => props.onChange({ api_key: event.currentTarget.value })}
            />
          </label>
        </div>

        <Show when={selectedType()?.requires_base_url || props.form.base_url}>
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


function FallbackModelSection(props: {
  catalogs: ModelCatalog[];
  providerNames: string[];
  defaultProvider: string;
  defaultModel: string;
  provider: string;
  model: string;
  dirty: boolean;
  saving: boolean;
  onProviderModelChange: (provider: string, model: string) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  return (
    <section class="settings-group">
      <div class="settings-section-header">
        <div>
          <div class="settings-section-title">
            <ShieldAlert size={15} style={{ "vertical-align": "middle", "margin-right": "6px" }} />
            Fallback Model
          </div>
          <div class="hint">
            Used automatically when an agent's configured provider or model is missing or invalid
            (e.g. the provider was deleted or the agent card no longer references a valid model).
            Leave empty to disable the fallback and surface the original error.
          </div>
        </div>
        <div class="row-wrap">
          <Show when={props.dirty}>
            <button class="btn btn-sm" type="button" onClick={props.onClear} disabled={props.saving}>
              Reset
            </button>
            <button class="btn btn-primary btn-sm" type="button" onClick={props.onSave} disabled={props.saving}>
              <Save size={13} />
              {props.saving ? "Saving..." : "Save Fallback"}
            </button>
          </Show>
        </div>
      </div>
      <div class="field" style={{ "max-width": "520px" }}>
        <label>Provider / Model</label>
        <ModelPicker
          catalogs={props.catalogs}
          providerNames={props.providerNames}
          defaultProvider={props.defaultProvider}
          defaultModel={props.defaultModel}
          provider={props.provider}
          model={props.model}
          disabled={props.saving}
          dropdownPlacement="bottom"
          resolveDefault={true}
          onChange={(nextProvider, nextModel) => {
            props.onProviderModelChange(nextProvider, nextModel);
          }}
        />
      </div>
    </section>
  );
}
