import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { RefreshCw, Sparkles } from "lucide-solid";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataGate } from "@/components/State";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { connectionReturnTo } from "@/lib/providerRoutes";
import type { WebConnectionsPayload } from "@/types";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { suggestProviderName } from "@/lib/providerConnections";
import { ProviderConnectionList, ProviderPicker, ProviderFormPanel, type ConnectionTestState } from "./ProviderConnections";
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
  const [deleteTarget, setDeleteTarget] = createSignal<string | null>(null);
  const [clearDefaultTarget, setClearDefaultTarget] = createSignal<{ type: string; label: string } | null>(null);
  const [testStates, setTestStates] = createSignal<Record<string, ConnectionTestState>>({});
  const [verifyingForm, setVerifyingForm] = createSignal(false);
  const [formTestResult, setFormTestResult] = createSignal<{
    ok: boolean;
    latency_ms: number;
    message: string;
    error_code?: string;
  } | null>(null);
  const isEditing = () => Boolean(params.connectionName || createdName());
  const showForm = () => isEditing() || Boolean(query.new);
  const dirty = () => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || changeKey());
  useUnsavedChanges(dirty, () => { setForm(initial()); setChangeKey(false); setFormTestResult(null); });
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => {
    const name = params.connectionName || createdName();
    if (!name) return undefined;
    return data()?.connections.find((item) => item.name.toLowerCase() === name.toLowerCase());
  };
  const returnTo = () => connectionReturnTo(String(query.returnTo || ""));

  const testConnectionByName = async (name: string) => {
    setTestStates((prev) => ({ ...prev, [name]: { loading: true } }));
    try {
      const res = await postJson<{ ok: boolean; message: string; latency_ms: number; error_code?: string }>(
        `${API}/verify`,
        { name }
      );
      setTestStates((prev) => ({ ...prev, [name]: { loading: false, ok: res.ok, latency_ms: res.latency_ms, message: res.message } }));
      if (res.ok) showToast(`${name}: Connected (⚡ ${res.latency_ms}ms)`);
      else showToast(`${name}: ${res.message}`, "error");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Test failed";
      setTestStates((prev) => ({ ...prev, [name]: { loading: false, ok: false, latency_ms: 0, message: msg } }));
      showToast(`${name}: ${msg}`, "error");
    }
  };

  const testCurrentConnection = async () => {
    if (verifyingForm() || busy()) return;
    setVerifyingForm(true);
    setFormTestResult(null);
    try {
      const body: Record<string, unknown> = {
        type: form().type,
      };
      if (isEditing()) {
        body.name = form().name;
      }
      if (form().api_key) {
        body.api_key = form().api_key;
      }
      if (form().cse_id) {
        body.cse_id = form().cse_id;
      }
      if (form().base_url) {
        body.base_url = form().base_url;
      }
      const res = await postJson<{
        ok: boolean;
        message: string;
        latency_ms: number;
        error_code?: string;
      }>(`${API}/verify`, body);
      setFormTestResult(res);
      if (res.ok) {
        showToast(res.message || `Connected (⚡ ${res.latency_ms}ms)`);
      } else {
        showToast(res.message || "Connection verification failed.", "error");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Verification request failed.";
      setFormTestResult({ ok: false, latency_ms: 0, message: msg, error_code: "REQUEST_FAILED" });
      showToast(msg, "error");
    } finally {
      setVerifyingForm(false);
    }
  };

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
      setFormTestResult(null);
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
  const confirmDeleteConnection = async () => {
    const name = deleteTarget();
    if (!name) return;
    setDeleteTarget(null);
    await action(() => deleteJson(`${API}/${encodeURIComponent(name)}`));
  };
  const confirmClearDefault = async () => {
    const target = clearDefaultTarget();
    if (!target) return;
    setClearDefaultTarget(null);
    await action(() => putJson(`${API}/defaults/${target.type}`, { name: null }));
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
    <ProviderSettingsLayout
      title={showForm() ? (isEditing() ? `Edit ${form().name || "Connection"}` : `Add ${service()?.label || "Web Connection"}`) : "Search & Web Providers"}
      breadcrumbSegments={
        showForm()
          ? [
              { label: "Providers", href: "/settings/providers?tab=web" },
              { label: isEditing() ? (form().name || "Edit Connection") : "Add Connection" },
            ]
          : undefined
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <p class="hint">Manage credentials once. Tools and agents can use a shared default or a named connection. Empty fields fall back to the service environment variables.</p>
            <Show when={showForm()} fallback={
              <>
                <SettingsResourceToolbar searchValue={search()} searchPlaceholder="Search configurations and providers..." onSearchInput={setSearch} />
                <ProviderConnectionList
                  items={payload().connections.filter((entry) => `${entry.name} ${payload().services.find((item) => item.type === entry.type)?.label || entry.type}`.toLowerCase().includes(search().trim().toLowerCase())).map((entry) => ({
                    name: entry.name, label: payload().services.find((item) => item.type === entry.type)?.label || entry.type,
                    configured: entry.configured, isDefault: entry.is_default, canSetDefault: entry.configured && !entry.is_default, canDelete: true,
                  }))}
                  busy={busy()}
                  emptyMessage="No saved configurations. Environment credentials remain available."
                  onEdit={(name) => navigate(`/settings/providers/web/${encodeURIComponent(name)}`)}
                  onSetDefault={(name) => { const entry = payload().connections.find((item) => item.name === name); if (entry) void action(() => putJson(`${API}/defaults/${entry.type}`, { name })); }}
                  onDelete={(name) => setDeleteTarget(name)}
                  onTest={testConnectionByName}
                  testStates={testStates()}
                />
                <Show when={Object.values(payload().defaults).some(Boolean)}>
                  <div class="row-wrap"><For each={payload().services.filter((item) => payload().defaults[item.type])}>{(item) => <button class="btn btn-sm" disabled={busy()} onClick={() => setClearDefaultTarget({ type: item.type, label: item.label })}>Clear {item.label} default</button>}</For></div>
                </Show>
                <ProviderPicker items={payload().services.filter((item) => item.label.toLowerCase().includes(search().trim().toLowerCase())).map((item) => ({
                  id: item.type, label: item.label, description: item.capabilities.join(" / "), count: payload().connections.filter((entry) => entry.type === item.type).length,
                }))} onSelect={(id) => navigate(`/settings/providers?tab=web&new=${id}`)} />
              </>
            }>
              <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
              <ProviderFormPanel
                title={isEditing() ? "Edit connection" : `Add ${service()?.label || "connection"}`}
                busy={busy()}
                onSubmit={save}
                onBack={() => navigate(returnTo())}
                actions={
                  <div class="row-wrap items-center">
                    <button
                      class="btn btn-secondary"
                      type="button"
                      disabled={verifyingForm() || busy()}
                      onClick={testCurrentConnection}
                      title="Test web connection"
                    >
                      <Show when={verifyingForm()} fallback={<Sparkles size={14} />}>
                        <RefreshCw size={14} class="spin" />
                      </Show>
                      {verifyingForm() ? "Testing..." : "Test Connection"}
                    </button>
                    <Show when={formTestResult()}>
                      <Show
                        when={formTestResult()!.ok}
                        fallback={
                          <span class="badge badge-danger" title={formTestResult()!.message}>
                            {formTestResult()!.error_code || "Failed"}
                          </span>
                        }
                      >
                        <span class="badge badge-success connection-latency-badge" title={formTestResult()!.message}>
                          ⚡ {formTestResult()!.latency_ms}ms
                        </span>
                      </Show>
                    </Show>
                  </div>
                }
              >
                <label class="stack">Name<input class="input" required pattern="[A-Za-z0-9_-]+" disabled={isEditing() || busy()} value={form().name} onInput={(event) => setForm({ ...form(), name: event.currentTarget.value })} /></label>
                <label class="stack">Service<select class="input" disabled={isEditing() || busy()} value={form().type} onChange={(event) => setForm({ ...emptyForm(event.currentTarget.value), name: suggestProviderName(event.currentTarget.value, payload().connections.map((item) => item.name)) })}>
                  <For each={payload().services}>{(item) => <option value={item.type}>{item.label}</option>}</For>
                </select></label>
                <Show when={isEditing()}>
                  <p class="hint">API key: {current()?.has_api_key ? "Stored" : "Not stored"}. Effective source: {current()?.sources.api_key || "default"}.</p>
                  <label><input type="checkbox" checked={changeKey()} onChange={(event) => setChangeKey(event.currentTarget.checked)} /> Replace or clear the API key</label>
                </Show>
                <Show when={!isEditing() || changeKey()}><label class="stack">API key<input class="input" type="password" autocomplete="new-password" value={form().api_key} onInput={(event) => setForm({ ...form(), api_key: event.currentTarget.value })} /><span class="hint">Leave empty to use the environment credential.</span></label></Show>
                <For each={(service()?.fields || []).filter((field) => field !== "api_key")}>{(field) => (
                  <label class="stack">{field === "cse_id" ? "Google CSE ID" : "Base URL"}<input class="input" type={field === "base_url" ? "url" : "text"} value={form()[field as "cse_id" | "base_url"]} onInput={(event) => setForm({ ...form(), [field]: event.currentTarget.value })} /></label>
                )}</For>
                <Show when={formTestResult() && !formTestResult()!.ok}>
                  <div class="connection-error-alert" role="alert">
                    <div class="row-wrap items-center gap-2">
                      <span class="badge badge-danger">
                        {formTestResult()!.error_code || "FAILED"}
                      </span>
                      <span class="connection-error-msg">{formTestResult()!.message}</span>
                    </div>
                  </div>
                </Show>
              </ProviderFormPanel>
              </Show>
            </Show>
          </div>
        )}
      </DataGate>
      <ConfirmDialog
        open={deleteTarget() !== null}
        title="Delete Connection"
        message={<p>Are you sure you want to delete connection <span class="mono">{deleteTarget()}</span>?</p>}
        confirmLabel="Delete"
        confirmVariant="danger"
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDeleteConnection()}
      />
      <ConfirmDialog
        open={clearDefaultTarget() !== null}
        title="Clear Default Connection"
        message={
          <p>
            Are you sure you want to clear the default connection for <strong>{clearDefaultTarget()?.label}</strong>? Tools and agents will fall back to environment credentials.
          </p>
        }
        confirmLabel="Clear Default"
        confirmVariant="warning"
        onClose={() => setClearDefaultTarget(null)}
        onConfirm={() => void confirmClearDefault()}
      />
    </ProviderSettingsLayout>
  );
}
