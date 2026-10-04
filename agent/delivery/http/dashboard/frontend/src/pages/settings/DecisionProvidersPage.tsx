import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { suggestProviderName } from "@/lib/providerConnections";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { DataGate } from "@/components/State";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { useToast } from "@/components/Toast";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";
import { ProviderConnectionList, ProviderFormPanel, ProviderPicker } from "./ProviderConnections";
import type { DecisionProvidersPayload } from "@/types";

const ROOT = "/settings/providers?tab=decision";
const API = "/dashboard-api/decision-providers";
type Form = { name: string; type: string; fields: Record<string, string | number> };

export function DecisionProvidersPage() {
  const params = useParams<{ decisionProviderName?: string }>();
  const [query] = useSearchParams<{ new?: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [data, setData] = createSignal<DecisionProvidersPayload>();
  const [error, setError] = createSignal("");
  const [search, setSearch] = createSignal("");
  const [form, setForm] = createSignal<Form>({ name: "", type: "", fields: {} });
  const [initial, setInitial] = createSignal(form());
  const [replacedSecrets, setReplacedSecrets] = createSignal<string[]>([]);
  const [busy, setBusy] = createSignal(false);
  const editing = () => Boolean(params.decisionProviderName);
  const showForm = () => editing() || Boolean(query.new);
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => data()?.providers.find((item) => item.name.toLowerCase() === String(params.decisionProviderName || "").toLowerCase());
  useUnsavedChanges(() => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || replacedSecrets().length > 0), () => { setForm(initial()); setReplacedSecrets([]); });
  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<DecisionProvidersPayload>(API);
      setData(payload);
      const entry = payload.providers.find((item) => item.name.toLowerCase() === String(params.decisionProviderName || "").toLowerCase());
      if (editing() && !entry) throw new Error("Decision provider not found.");
      const kind = entry?.type || query.new || "";
      const definition = payload.services.find((item) => item.type === kind);
      const fields: Form["fields"] = {};
      for (const field of definition?.fields || []) fields[field.name] = field.input_type === "password" ? "" : entry?.fields[field.name] ?? field.default ?? "";
      const next = { name: entry?.name || suggestProviderName(kind, payload.providers.map((item) => item.name)), type: kind, fields };
      setForm(next); setInitial(next); setReplacedSecrets([]);
    } catch (err) { setData(undefined); setError(err instanceof Error ? err.message : "Failed to load decision providers."); }
  };
  createEffect(() => { params.decisionProviderName; query.new; void load(); });
  const action = async (work: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true);
    try { await work(); await load(); }
    catch (err) { showToast(err instanceof Error ? err.message : "Request failed.", "error"); }
    finally { setBusy(false); }
  };
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy() || !service()) return;
    setBusy(true);
    try {
      const fields = Object.fromEntries(service()!.fields
        .filter((field) => field.input_type !== "password" || !editing() || replacedSecrets().includes(field.name))
        .map((field) => [field.name, form().fields[field.name]]));
      if (editing()) await putJson(`${API}/${encodeURIComponent(form().name)}`, { fields });
      else await postJson(API, { name: form().name, type: form().type, fields });
      setInitial(form()); setReplacedSecrets([]);
      showToast("Decision provider configuration saved.");
      navigate(ROOT);
    } catch (err) { showToast(err instanceof Error ? err.message : "Failed to save decision provider.", "error"); }
    finally { setBusy(false); }
  };
  return (
    <ProviderSettingsLayout title="Decision Model Providers">
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <Show when={showForm()} fallback={
              <>
                <p class="hint">Save separate decision model configurations and choose one default. Without a named default, legacy settings and environment credentials remain available.</p>
                <SettingsResourceToolbar searchValue={search()} searchPlaceholder="Search configurations and providers..." onSearchInput={setSearch} />
                <ProviderConnectionList busy={busy()} items={payload.providers
                  .filter((item) => `${item.name} ${item.type} ${item.fields.model || ""}`.toLowerCase().includes(search().trim().toLowerCase()))
                  .map((item) => ({ name: item.name, label: payload.services.find((service) => service.type === item.type)?.label || item.type,
                    model: String(item.fields.model || ""), configured: item.configured, isDefault: item.is_default,
                    canSetDefault: item.configured && !item.is_default, canDelete: !item.is_default,
                    deleteReason: item.is_default ? "Choose another default or clear it before deleting." : "",
                  }))}
                  onEdit={(name) => navigate(`/settings/providers/decision/${encodeURIComponent(name)}`)}
                  onSetDefault={(name) => action(() => putJson(`${API}/default`, { name }))}
                  onDelete={(name) => { if (window.confirm(`Delete decision provider ${name}?`)) void action(() => deleteJson(`${API}/${encodeURIComponent(name)}`)); }} />
                <Show when={payload.default_provider}><button class="btn btn-sm" disabled={busy()} onClick={() => action(() => putJson(`${API}/default`, { name: null }))}>Clear default</button></Show>
                <ProviderPicker items={payload.services.filter((item) => item.label.toLowerCase().includes(search().trim().toLowerCase())).map((item) => ({
                  id: item.type, label: item.label, count: payload.providers.filter((provider) => provider.type === item.type).length,
                }))} onSelect={(id) => navigate(`/settings/providers?tab=decision&new=${id}`)} />
              </>
            }>
              <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
                <ProviderFormPanel title={editing() ? "Edit decision provider" : `Add ${service()?.label}`} busy={busy()} onSubmit={save} onBack={() => navigate(ROOT)}>
                  <label class="stack">Configuration name<input class="input" required pattern="[A-Za-z0-9_-]+" disabled={editing()} value={form().name} onInput={(event) => setForm({ ...form(), name: event.currentTarget.value })} /></label>
                  <Show when={editing()}>
                    <For each={(service()?.fields || []).filter((field) => field.input_type === "password")}>{(field) => (
                      <div class="stack">
                        <p class="hint">{field.label}: {(current()?.stored_secrets?.[field.name] ?? current()?.has_api_token) ? "Stored" : "Not stored"}.</p>
                        <label><input type="checkbox" checked={replacedSecrets().includes(field.name)} onChange={(event) => setReplacedSecrets((current) => event.currentTarget.checked ? [...current, field.name] : current.filter((name) => name !== field.name))} /> Replace or clear the {field.label.toLowerCase()}</label>
                      </div>
                    )}</For>
                  </Show>
                  <For each={service()?.fields || []}>{(field) => (
                    <Show when={field.input_type !== "password" || !editing() || replacedSecrets().includes(field.name)}>
                      <label class="stack">{field.label}<input class="input" type={field.input_type} min={field.min} step={field.step ?? (field.input_type === "number" ? "any" : undefined)}
                        required={field.input_type !== "password" && field.required} autocomplete={field.input_type === "password" ? "new-password" : undefined}
                        value={form().fields[field.name] ?? ""} onInput={(event) => setForm({ ...form(), fields: { ...form().fields, [field.name]: event.currentTarget.value } })} />
                        <Show when={field.input_type === "password"}><span class="hint">Leave empty to use the environment credential.</span></Show>
                      </label>
                    </Show>
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
