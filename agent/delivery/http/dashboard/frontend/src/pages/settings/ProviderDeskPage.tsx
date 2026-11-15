import { useLocation, useNavigate, useSearchParams } from "@solidjs/router";
import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { ArrowRight, Plus, RefreshCw, Search, Sparkles, Star, Trash2 } from "lucide-solid";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { buildDeskCatalog, buildDeskConnections, CONNECTION_KIND_LABELS, connectionEndpoint, filterDeskConnections, type DeskConnection, type DeskSources } from "@/lib/providerDesk";
import { currentConnectionKind, currentProviderSection, type ProviderConnectionKind } from "@/lib/providerRoutes";
import type { DecisionProvidersPayload, SettingsPayload, WebConnectionsPayload } from "@/types";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";
import { ProviderDefaults } from "./ProviderDefaults";
import { ProviderPagination, ProviderLogo, type ConnectionTestState } from "./ProviderConnections";

const SOURCE_PATHS: Record<ProviderConnectionKind, string> = {
  llm: "/dashboard-api/providers", decision: "/dashboard-api/decision-providers", web: "/dashboard-api/web-connections",
};
const KINDS: ProviderConnectionKind[] = ["llm", "decision", "web"];

export function ProviderDeskPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useSearchParams<{ q?: string; status?: string; page?: string }>();
  const section = () => currentProviderSection(location.pathname, location.search);
  const kind = () => currentConnectionKind(location.pathname, location.search);
  const [sources, setSources] = createSignal<DeskSources>({});
  const [errors, setErrors] = createSignal<Partial<Record<ProviderConnectionKind, string>>>({});
  const [loading, setLoading] = createSignal<Partial<Record<ProviderConnectionKind, boolean>>>({ llm: true, decision: true, web: true });
  const [busy, setBusy] = createSignal("");
  const [syncing, setSyncing] = createSignal(false);
  const [tests, setTests] = createSignal<Record<string, ConnectionTestState>>({});
  const [deleteTarget, setDeleteTarget] = createSignal<DeskConnection | null>(null);
  const [clearTarget, setClearTarget] = createSignal<{ kind: "decision" | "web"; label: string; type?: string } | null>(null);
  const { showToast } = useToast();
  let disposed = false;
  const generations = { llm: 0, decision: 0, web: 0 };
  onCleanup(() => { disposed = true; });
  const load = async (source: ProviderConnectionKind) => {
    const generation = ++generations[source];
    setLoading((previous) => ({ ...previous, [source]: true }));
    setErrors((previous) => ({ ...previous, [source]: "" }));
    try {
      const result = await apiFetch<SettingsPayload | DecisionProvidersPayload | WebConnectionsPayload>(SOURCE_PATHS[source]);
      if (!disposed && generation === generations[source]) setSources((previous) => ({ ...previous, [source]: result }));
    } catch (error) {
      if (!disposed && generation === generations[source]) setErrors((previous) => ({ ...previous, [source]: error instanceof Error ? error.message : "Failed to load connections." }));
    } finally {
      if (!disposed && generation === generations[source]) setLoading((previous) => ({ ...previous, [source]: false }));
    }
  };
  onMount(() => { void Promise.allSettled(KINDS.map(load)); });
  const connections = createMemo(() => buildDeskConnections(sources()));
  const catalog = createMemo(() => buildDeskCatalog(sources(), connections()));
  const filteredConnections = createMemo(() => filterDeskConnections(connections(), query.q || "", kind(), query.status));
  const filteredCatalog = createMemo(() => {
    const needle = (query.q || "").trim().toLowerCase();
    return catalog().filter((item) => (!kind() || item.kind === kind()) && `${item.label} ${item.providerId} ${item.adapter}`.toLowerCase().includes(needle));
  });
  const pageSize = () => section() === "catalog" ? 20 : 8;
  const total = () => section() === "catalog" ? filteredCatalog().length : filteredConnections().length;
  const page = () => Math.min(Math.max(1, Number.parseInt(query.page || "1", 10) || 1), Math.max(1, Math.ceil(total() / pageSize())));
  const visibleConnections = createMemo(() => filteredConnections().slice((page() - 1) * 8, page() * 8));
  const visibleCatalog = createMemo(() => filteredCatalog().slice((page() - 1) * 20, page() * 20));
  const unavailable = (item: DeskConnection) => Boolean(busy() || errors()[item.kind] || loading()[item.kind]);

  const saveDefault = async (source: ProviderConnectionKind, path: string, body: unknown): Promise<boolean> => {
    if (busy() || errors()[source] || loading()[source]) return false;
    setBusy(`default:${source}`);
    try {
      await putJson(path, body);
      await load(source);
      if (!disposed) showToast("Default selection updated.");
      return true;
    } catch (error) {
      if (!disposed) showToast(error instanceof Error ? error.message : "Failed to update default.", "error");
      return false;
    } finally { if (!disposed) setBusy(""); }
  };
  const setDefault = (item: DeskConnection) => {
    if (!item.canSetDefault || unavailable(item)) return;
    if (item.kind === "llm") void saveDefault("llm", "/settings/llm.default_model", { value: `${item.name}/${item.model}` });
    else if (item.kind === "decision") void saveDefault("decision", `${SOURCE_PATHS.decision}/default`, { name: item.name });
    else void saveDefault("web", `${SOURCE_PATHS.web}/defaults/${encodeURIComponent(item.type)}`, { name: item.name });
  };
  const remove = async () => {
    const item = deleteTarget();
    if (!item || !item.canDelete || unavailable(item)) return;
    setBusy(item.id);
    try {
      const result = await deleteJson<{ message?: string }>(connectionEndpoint(item));
      if (disposed) return;
      setDeleteTarget(null);
      setTests((previous) => { const next = { ...previous }; delete next[item.id]; return next; });
      showToast(result?.message || "Connection deleted.", result?.message ? "warning" : "success");
      await load(item.kind);
    } catch (error) { if (!disposed) showToast(error instanceof Error ? error.message : "Failed to delete connection.", "error"); }
    finally { if (!disposed) setBusy(""); }
  };
  const test = async (item: DeskConnection) => {
    if (unavailable(item) || tests()[item.id]?.loading) return;
    setTests((previous) => ({ ...previous, [item.id]: { loading: true } }));
    try {
      const result = await postJson<ConnectionTestState>(`${SOURCE_PATHS[item.kind]}/verify`, { name: item.name, ...(item.kind === "llm" ? { type: item.type } : {}) });
      if (!disposed) setTests((previous) => ({ ...previous, [item.id]: { ...result, loading: false } }));
    } catch (error) {
      if (!disposed) setTests((previous) => ({ ...previous, [item.id]: { ok: false, message: error instanceof Error ? error.message : "Connection test failed." } }));
    }
  };
  const sync = async () => {
    if (syncing()) return;
    setSyncing(true);
    try {
      const result = await postJson<{ message: string }>("/dashboard-api/providers/update-catalog", {});
      await load("llm");
      if (!disposed) showToast(result.message || "Catalog updated.");
    } catch (error) { if (!disposed) showToast(error instanceof Error ? error.message : "Failed to sync catalog.", "error"); }
    finally { if (!disposed) setSyncing(false); }
  };
  const edit = (item: DeskConnection) => navigate(`/settings/providers/${item.kind}/${encodeURIComponent(item.name)}`);
  const create = (source: ProviderConnectionKind, providerId: string) => navigate(`/settings/providers?tab=catalog&kind=${source}&new=${encodeURIComponent(providerId)}`);

  return (
    <ProviderSettingsLayout createDialog={false} title={section() === "catalog" ? "Provider catalog" : section() === "defaults" ? "Defaults" : "Connections"}
      actions={<Show when={section() !== "defaults"}><Show when={section() === "catalog"} fallback={<button class="btn btn-primary" type="button" onClick={() => navigate("/settings/providers?tab=catalog")}><Plus size={14} />Add connection</button>}>
        <button class="btn" type="button" disabled={syncing() || !sources().llm} onClick={() => void sync()}><RefreshCw size={14} class={syncing() ? "spin" : ""} />{syncing() ? "Syncing..." : "Sync Catalog"}</button>
        <button class="btn btn-primary" type="button" disabled={!sources().llm} onClick={() => create("llm", "custom")}><Plus size={14} />Custom endpoint</button>
      </Show></Show>}>
      <div class="stack provider-desk" data-provider-desk>
        <For each={KINDS}>{(source) => <Show when={errors()[source]}><div class="provider-source-error" role="alert"><span><strong>{CONNECTION_KIND_LABELS[source]}</strong>: {errors()[source]}</span><button class="btn btn-sm" type="button" disabled={loading()[source]} onClick={() => void load(source)}>Retry {CONNECTION_KIND_LABELS[source]}</button></div></Show>}</For>
        <Show when={KINDS.some((source) => loading()[source])}><p class="hint" role="status">Loading provider connections...</p></Show>
        <Show when={section() === "defaults"} fallback={<>
          <div class="provider-desk-toolbar">
            <label class="provider-desk-search"><Search size={15} /><input class="input" type="search" aria-label={section() === "catalog" ? "Search provider catalog" : "Search connections, providers, models"} placeholder={section() === "catalog" ? "Search provider catalog..." : "Search connections, providers, models..."} value={query.q || ""} onInput={(event) => setQuery({ q: event.currentTarget.value || undefined, page: undefined }, { replace: true })} /></label>
            <div class="provider-desk-filters">
              <label>Type<select class="input" aria-label="Connection type" value={kind() || "all"} onChange={(event) => setQuery({ tab: section(), kind: event.currentTarget.value === "all" ? undefined : event.currentTarget.value, page: undefined })}><option value="all">All types</option><For each={KINDS}>{(source) => <option value={source}>{CONNECTION_KIND_LABELS[source]}</option>}</For></select></label>
              <Show when={section() === "connections"}><label>Status<select class="input" aria-label="Connection status" value={query.status || "all"} onChange={(event) => setQuery({ status: event.currentTarget.value === "all" ? undefined : event.currentTarget.value, page: undefined })}><option value="all">All statuses</option><option value="configured">Configured</option><option value="incomplete">Incomplete</option><option value="disabled">Disabled</option></select></label></Show>
            </div>
          </div>
          <Show when={section() === "catalog"} fallback={
            <section class="panel provider-desk-table-panel" data-provider-connections>
              <div class="provider-desk-table-scroll" role="region" aria-label="Saved provider connections" tabindex="0">
                <table class="provider-desk-table"><thead><tr><th scope="col">Connection / Provider</th><th scope="col">Capabilities</th><th scope="col">Default model</th><th scope="col">Models</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
                  <tbody><For each={visibleConnections()}>{(item) => <tr data-connection-name={item.name} data-connection-kind={item.kind}>
                    <td><button class="provider-desk-name" type="button" onClick={() => edit(item)}><ProviderLogo label={item.label} url={item.logoUrl} /><span><strong>{item.name}</strong><small>{item.label} · {item.type}</small></span></button></td>
                    <td><div class="row-wrap"><For each={item.capabilities}>{(capability) => <span class="badge">{capability}</span>}</For></div></td>
                    <td class="mono provider-desk-model">{item.model || "—"}</td><td>{item.kind === "llm" ? item.models.length : "—"}</td>
                    <td><div class="stack provider-desk-status"><span class={`badge ${!item.enabled ? "" : item.configured ? "badge-success" : "badge-warning"}`}>{!item.enabled ? "Disabled" : item.configured ? "Configured" : "Incomplete"}</span><Show when={item.isDefault}><span class="provider-desk-default"><Star size={11} fill="currentColor" />Default</span></Show><Show when={tests()[item.id] && !tests()[item.id].loading}><span class={tests()[item.id].ok ? "hint" : "text-danger"} role="status" title={tests()[item.id].message}>{tests()[item.id].ok ? `⚡ ${tests()[item.id].latency_ms || 0}ms` : "Test failed"}</span></Show></div></td>
                    <td><div class="provider-desk-row-actions">
                      <button class="btn btn-icon btn-sm" type="button" aria-label={`Test connection for ${item.name}`} title={tests()[item.id]?.message || "Test connection"} disabled={unavailable(item) || tests()[item.id]?.loading} onClick={() => void test(item)}><Show when={tests()[item.id]?.loading} fallback={<Sparkles size={14} />}><RefreshCw size={14} class="spin" /></Show></button>
                      <button class="btn btn-icon btn-sm" type="button" aria-label={`Set ${item.name} as default`} aria-pressed={item.isDefault} title={item.defaultReason || "Set as default"} disabled={unavailable(item) || !item.canSetDefault} onClick={() => setDefault(item)}><Star size={14} fill={item.isDefault ? "currentColor" : "none"} /></button>
                      <button class="btn btn-icon btn-sm btn-danger" type="button" aria-label={`Delete ${item.name}`} title={item.deleteReason || "Delete connection"} disabled={unavailable(item) || !item.canDelete} onClick={() => setDeleteTarget(item)}><Trash2 size={14} /></button>
                      <button class="btn btn-sm" type="button" aria-label={`Edit ${item.name}`} onClick={() => edit(item)}>Configure<ArrowRight size={13} /></button>
                    </div></td>
                  </tr>}</For></tbody>
                </table>
              </div>
              <Show when={!total() && !KINDS.some((source) => loading()[source])}><div class="empty"><h3>No connections found</h3><p class="hint">Try another search or add a provider connection.</p><button class="btn" type="button" onClick={() => navigate("/settings/providers?tab=catalog")}>Browse provider catalog</button></div></Show>
            </section>
          }>
            <div class="provider-desk-catalog" data-provider-picker><For each={visibleCatalog()}>{(item) => <button class="provider-picker-card provider-catalog-tile" type="button" data-provider-id={item.providerId} data-provider-kind={item.kind} onClick={() => create(item.kind, item.providerId)}>
              <div class="provider-catalog-top"><ProviderLogo label={item.label} url={item.logoUrl} /><Show when={item.count} fallback={<Plus size={15} />}><span class="badge">{item.count} saved</span></Show></div>
              <strong>{item.label}</strong><span class="hint">{item.modelCount !== undefined ? `${item.modelCount} catalog models` : item.adapter}</span><span class="badge">{CONNECTION_KIND_LABELS[item.kind]}</span>
            </button>}</For></div>
            <Show when={!total() && !KINDS.some((source) => loading()[source])}><p class="empty hint">No providers match your search.</p></Show>
          </Show>
          <ProviderPagination page={page()} total={total()} size={pageSize()} onChange={(value) => setQuery({ page: String(value) })} />
          <p class="hint provider-desk-note">Each saved configuration keeps its own credentials. Catalog models describe provider offerings; account access is checked separately.</p>
        </>}>
          <ProviderDefaults sources={sources()} connections={connections()} busy={Boolean(busy())} unavailable={errors()} onSave={saveDefault} onClear={(source, label, type) => setClearTarget({ kind: source, label, type })} />
        </Show>
      </div>
      <ConfirmDialog open={Boolean(deleteTarget())} title="Delete Connection" message={<p>Delete <strong>{deleteTarget()?.name}</strong> ({deleteTarget() ? CONNECTION_KIND_LABELS[deleteTarget()!.kind] : ""})?</p>} confirmLabel="Delete" confirmVariant="danger" loading={Boolean(busy())} onClose={() => { if (!busy()) setDeleteTarget(null); }} onConfirm={() => void remove()} />
      <ConfirmDialog open={Boolean(clearTarget())} title="Clear Default Connection" message={<p>Clear the {clearTarget()?.label} default and use environment or legacy fallback?</p>} confirmLabel="Clear Default" confirmVariant="warning" loading={Boolean(busy())} onClose={() => { if (!busy()) setClearTarget(null); }} onConfirm={async () => {
        const target = clearTarget();
        if (!target) return;
        const path = target.kind === "decision" ? `${SOURCE_PATHS.decision}/default` : `${SOURCE_PATHS.web}/defaults/${encodeURIComponent(target.type!)}`;
        if (await saveDefault(target.kind, path, { name: null })) setClearTarget(null);
      }} />
    </ProviderSettingsLayout>
  );
}
