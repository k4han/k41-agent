import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { DataGate } from "@/components/State";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { connectionReturnTo } from "@/lib/providerRoutes";
import type { WebConnectionsPayload } from "@/types";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { suggestProviderName } from "@/lib/providerConnections";
import { ProviderConnectionList, ProviderPicker, ProviderFormPanel } from "./ProviderConnections";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";

const ROOT = "/settings/providers?tab=web";
const API = "/dashboard-api/web-connections";
const emptyForm = (type = "tavily") => ({ name: "", type, api_key: "", cse_id: "", base_url: "" });

export function WebConnectionsPage() {
  const params = useParams<{ connectionName?: string }>();
  const [query] = useSearchParams<{ new?: string; returnTo?: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [data, setData] = createSignal<WebConnectionsPayload>();
  const [error, setError] = createSignal("");
  const [form, setForm] = createSignal(emptyForm(query.new));
  const [initial, setInitial] = createSignal(emptyForm(query.new));
  const [changeKey, setChangeKey] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [search, setSearch] = createSignal("");
  const [createdName, setCreatedName] = createSignal("");
  const isEditing = () => Boolean(params.connectionName || createdName());
  const showForm = () => isEditing() || Boolean(query.new);
  const dirty = () => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || changeKey());
  useUnsavedChanges(dirty, () => { setForm(initial()); setChangeKey(false); });
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => {
    const name = params.connectionName || createdName();
    if (!name) return undefined;
    return data()?.connections.find((item) => item.name.toLowerCase() === name.toLowerCase());
  };
  const returnTo = () => connectionReturnTo(String(query.returnTo || ""));
  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<WebConnectionsPayload>(API);
      setData(payload);
      if (isEditing()) {
        const entry = payload.connections.find((item) => item.name.toLowerCase() === (params.connectionName || createdName()).toLowerCase());
        if (!entry) throw new Error("Web connection not found.");
        const next = { ...emptyForm(entry.type), name: entry.name, ...entry.fields };
        setForm(next); setInitial(next);
      } else {
        const type = query.new || "tavily";
        const next = { ...emptyForm(type), name: query.new ? suggestProviderName(type, payload.connections.map((item) => item.name)) : "" };
        setForm(next); setInitial(next);
      }
      setChangeKey(false);
    } catch (err) { setData(undefined); setError(err instanceof Error ? err.message : "Failed to load connections."); }
  };
  createEffect(() => {
    params.connectionName; query.new;
    setCreatedName("");
    void load();
  });
  const action = async (work: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true);
    try { await work(); await load(); }
    catch (err) { showToast(err instanceof Error ? err.message : "Request failed.", "error"); }
    finally { setBusy(false); }
  };
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy()) return;
    setBusy(true);
    try {
      const values: Record<string, unknown> = {};
      for (const field of service()?.fields || []) {
        if (field === "api_key") {
          if (!isEditing() || changeKey()) values.api_key = form().api_key || null;
        } else values[field] = form()[field as keyof ReturnType<typeof emptyForm>];
      }
      if (isEditing()) await putJson(`${API}/${encodeURIComponent(form().name)}`, values);
      else {
        await postJson(API, { name: form().name, type: form().type, ...values });
        setCreatedName(form().name);
      }
      setInitial(form()); setChangeKey(false);
      showToast("Web connection saved.");
      navigate(returnTo());
    } catch (err) { showToast(err instanceof Error ? err.message : "Failed to save connection.", "error"); }
    finally { setBusy(false); }
  };
  return (
    <ProviderSettingsLayout title="Web Service Connections">
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <p class="hint">Manage credentials once. Tools and agents can use a shared default or a named connection. Empty fields fall back to the service environment variables.</p>
            <Show when={showForm()} fallback={
              <>
                <SettingsResourceToolbar searchValue={search()} searchPlaceholder="Search configurations and providers..." onSearchInput={setSearch} />
                <ProviderConnectionList
                  items={payload.connections.filter((entry) => `${entry.name} ${payload.services.find((item) => item.type === entry.type)?.label || entry.type}`.toLowerCase().includes(search().trim().toLowerCase())).map((entry) => ({
                    name: entry.name, label: payload.services.find((item) => item.type === entry.type)?.label || entry.type,
                    configured: entry.configured, isDefault: entry.is_default, canSetDefault: entry.configured && !entry.is_default, canDelete: true,
                  }))}
                  busy={busy()}
                  emptyMessage="No saved configurations. Environment credentials remain available."
                  onEdit={(name) => navigate(`/settings/providers/web/${encodeURIComponent(name)}`)}
                  onSetDefault={(name) => { const entry = payload.connections.find((item) => item.name === name); if (entry) void action(() => putJson(`${API}/defaults/${entry.type}`, { name })); }}
                  onDelete={(name) => { if (window.confirm(`Delete connection ${name}?`)) void action(() => deleteJson(`${API}/${encodeURIComponent(name)}`)); }}
                />
                <Show when={Object.values(payload.defaults).some(Boolean)}>
                  <div class="row-wrap"><For each={payload.services.filter((item) => payload.defaults[item.type])}>{(item) => <button class="btn btn-sm" disabled={busy()} onClick={() => action(() => putJson(`${API}/defaults/${item.type}`, { name: null }))}>Clear {item.label} default</button>}</For></div>
                </Show>
                <ProviderPicker items={payload.services.filter((item) => item.label.toLowerCase().includes(search().trim().toLowerCase())).map((item) => ({
                  id: item.type, label: item.label, description: item.capabilities.join(" / "), count: payload.connections.filter((entry) => entry.type === item.type).length,
                }))} onSelect={(id) => navigate(`/settings/providers?tab=web&new=${id}`)} />
              </>
            }>
              <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
              <ProviderFormPanel title={isEditing() ? "Edit connection" : `Add ${service()?.label || "connection"}`} busy={busy()} onSubmit={save} onBack={() => navigate(returnTo())}>
                <label class="stack">Name<input class="input" required pattern="[A-Za-z0-9_-]+" disabled={isEditing() || busy()} value={form().name} onInput={(event) => setForm({ ...form(), name: event.currentTarget.value })} /></label>
                <label class="stack">Service<select class="input" disabled={isEditing() || busy()} value={form().type} onChange={(event) => setForm({ ...emptyForm(event.currentTarget.value), name: suggestProviderName(event.currentTarget.value, payload.connections.map((item) => item.name)) })}>
                  <For each={payload.services}>{(item) => <option value={item.type}>{item.label}</option>}</For>
                </select></label>
                <Show when={isEditing()}>
                  <p class="hint">API key: {current()?.has_api_key ? "Stored" : "Not stored"}. Effective source: {current()?.sources.api_key || "default"}.</p>
                  <label><input type="checkbox" checked={changeKey()} onChange={(event) => setChangeKey(event.currentTarget.checked)} /> Replace or clear the API key</label>
                </Show>
                <Show when={!isEditing() || changeKey()}><label class="stack">API key<input class="input" type="password" autocomplete="new-password" value={form().api_key} onInput={(event) => setForm({ ...form(), api_key: event.currentTarget.value })} /><span class="hint">Leave empty to use the environment credential.</span></label></Show>
                <For each={(service()?.fields || []).filter((field) => field !== "api_key")}>{(field) => (
                  <label class="stack">{field === "cse_id" ? "Google CSE ID" : "Base URL"}<input class="input" type={field === "base_url" ? "url" : "text"} value={form()[field as "cse_id" | "base_url"]} onInput={(event) => setForm({ ...form(), [field]: event.currentTarget.value })} /></label>
                )}</For>
              </ProviderFormPanel>
              </Show>
            </Show>
          </div>
        )}
      </DataGate>
    </ProviderSettingsLayout>
  );
}
