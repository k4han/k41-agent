import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { DataGate } from "@/components/State";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { connectionReturnTo } from "@/lib/providerRoutes";
import type { WebConnectionsPayload } from "@/types";
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
  const [makeDefault, setMakeDefault] = createSignal(false);
  const [createdName, setCreatedName] = createSignal("");
  const isEditing = () => Boolean(params.connectionName || createdName());
  const showForm = () => isEditing() || Boolean(query.new);
  const dirty = () => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || changeKey() || makeDefault());
  useUnsavedChanges(dirty, () => { setForm(initial()); setChangeKey(false); setMakeDefault(false); });
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
        const type = payload.services.some((item) => item.type === query.new) ? query.new! : "tavily";
        const next = emptyForm(type);
        setForm(next); setInitial(next);
      }
      setChangeKey(false); setMakeDefault(false);
    } catch (err) { setData(undefined); setError(err instanceof Error ? err.message : "Failed to load connections."); }
  };
  createEffect(() => {
    params.connectionName; query.new;
    setCreatedName("");
    void load();
  });
  const action = async (work: () => Promise<unknown>) => {
    setBusy(true);
    try { await work(); await load(); }
    catch (err) { showToast(err instanceof Error ? err.message : "Request failed.", "error"); }
    finally { setBusy(false); }
  };
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
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
      if (makeDefault()) {
        await putJson(`${API}/defaults/${form().type}`, { name: form().name });
        setMakeDefault(false);
      }
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
              <For each={payload.services}>{(definition) => (
                <section class="panel settings-section-card">
                  <div class="panel-header split">
                    <div><div class="panel-title">{definition.label}</div><div class="hint">{definition.capabilities.join(" / ")}</div></div>
                    <button class="btn btn-primary btn-sm" type="button" onClick={() => navigate(`/settings/providers?tab=web&new=${definition.type}`)}>Add connection</button>
                  </div>
                  <div class="panel-body stack">
                    <Show when={payload.defaults[definition.type]}>
                      <button class="btn btn-sm" disabled={busy()} onClick={() => action(() => putJson(`${API}/defaults/${definition.type}`, { name: null }))}>Clear shared default</button>
                    </Show>
                    <For each={payload.connections.filter((entry) => entry.type === definition.type)}>{(entry) => (
                      <div class="row-wrap">
                        <strong style={{ "overflow-wrap": "anywhere", "max-width": "100%", "min-width": "0" }}>{entry.name}</strong>
                        <span class="badge">{entry.configured ? "Configuration complete" : "Configuration incomplete"}</span>
                        <Show when={entry.is_default}><span class="badge badge-info">Shared default</span></Show>
                        <button class="btn btn-sm" onClick={() => navigate(`/settings/providers/web/${encodeURIComponent(entry.name)}`)}>Edit</button>
                        <button class="btn btn-sm" disabled={busy() || entry.is_default} onClick={() => action(() => putJson(`${API}/defaults/${entry.type}`, { name: entry.name }))}>Set default</button>
                        <button class="btn btn-sm btn-danger" disabled={busy()} onClick={() => {
                          if (window.confirm(`Delete connection ${entry.name}?`)) void action(() => deleteJson(`${API}/${encodeURIComponent(entry.name)}`));
                        }}>Delete</button>
                      </div>
                    )}</For>
                    <Show when={!payload.connections.some((entry) => entry.type === definition.type)}><p class="hint">No saved connections. Environment credentials remain available.</p></Show>
                  </div>
                </section>
              )}</For>
            }>
              <form class="panel panel-body stack" onSubmit={save}>
                <h3>{isEditing() ? "Edit connection" : "Create connection"}</h3>
                <label class="stack">Name<input class="input" required pattern="[A-Za-z0-9_-]+" disabled={isEditing() || busy()} value={form().name} onInput={(event) => setForm({ ...form(), name: event.currentTarget.value })} /></label>
                <label class="stack">Service<select class="input" disabled={isEditing() || busy()} value={form().type} onChange={(event) => setForm({ ...emptyForm(event.currentTarget.value), name: form().name })}>
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
                <label><input type="checkbox" checked={makeDefault()} onChange={(event) => setMakeDefault(event.currentTarget.checked)} /> Use as the shared default for this service</label>
                <div class="row-wrap"><button type="submit" class="btn btn-primary" disabled={busy()}>Save connection</button><button class="btn" type="button" disabled={busy()} onClick={() => navigate(returnTo())}>Cancel</button></div>
              </form>
            </Show>
          </div>
        )}
      </DataGate>
    </ProviderSettingsLayout>
  );
}
