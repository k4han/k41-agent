import { A, useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { ArrowRight, BrainCircuit, RefreshCw, Sparkles } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { suggestProviderName } from "@/lib/providerConnections";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataGate } from "@/components/State";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { useToast } from "@/components/Toast";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";
import { ProviderConnectionList, ProviderFormPanel, ProviderPicker, type ConnectionTestState } from "./ProviderConnections";
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
  const [deleteTarget, setDeleteTarget] = createSignal<string | null>(null);
  const [clearDefaultOpen, setClearDefaultOpen] = createSignal(false);
  const [testStates, setTestStates] = createSignal<Record<string, ConnectionTestState>>({});
  const [verifyingForm, setVerifyingForm] = createSignal(false);
  const [formTestResult, setFormTestResult] = createSignal<{
    ok: boolean;
    latency_ms: number;
    message: string;
    error_code?: string;
  } | null>(null);
  const editing = () => Boolean(params.decisionProviderName);
  const showForm = () => editing() || Boolean(query.new);
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => data()?.providers.find((item) => item.name.toLowerCase() === String(params.decisionProviderName || "").toLowerCase());
  useUnsavedChanges(() => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || replacedSecrets().length > 0), () => { setForm(initial()); setReplacedSecrets([]); setFormTestResult(null); });

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
        fields: { ...form().fields },
      };
      if (editing()) {
        body.name = form().name;
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
      setFormTestResult(null);
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
  const confirmDeleteDecisionProvider = async () => {
    const name = deleteTarget();
    if (!name) return;
    setDeleteTarget(null);
    await action(() => deleteJson(`${API}/${encodeURIComponent(name)}`));
  };
  const confirmClearDefault = async () => {
    setClearDefaultOpen(false);
    await action(() => putJson(`${API}/default`, { name: null }));
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
    <ProviderSettingsLayout
      title={showForm() ? (editing() ? `Edit ${form().name || "Decision Provider"}` : `Add ${service()?.label || "Decision Provider"}`) : "Decision Model Providers"}
      breadcrumbSegments={
        showForm()
          ? [
              { label: "Providers", href: "/settings/providers?tab=decision" },
              { label: editing() ? (form().name || "Edit Decision Provider") : "Add Decision Provider" },
            ]
          : undefined
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <Show when={showForm()} fallback={
              <>
                <div class="decision-signpost-banner">
                  <div class="row-wrap items-center gap-3">
                    <BrainCircuit size={18} class="text-primary" />
                    <span class="text-sm">
                      Decision model providers manage API keys and credentials for fast-path inference. Global routing strategies, cascade thresholds, and domain evaluators are configured in <strong>Decisions & Routing</strong>.
                    </span>
                  </div>
                  <A href="/settings/decisions" class="btn btn-sm">
                    Open Decisions & Routing <ArrowRight size={13} />
                  </A>
                </div>
                <p class="hint">Save separate decision model configurations and choose one default. Without a named default, legacy settings and environment credentials remain available.</p>
                <SettingsResourceToolbar searchValue={search()} searchPlaceholder="Search configurations and providers..." onSearchInput={setSearch} />
                <ProviderConnectionList busy={busy()} items={payload().providers
                  .filter((item) => `${item.name} ${item.type} ${item.fields.model || ""}`.toLowerCase().includes(search().trim().toLowerCase()))
                  .map((item) => ({ name: item.name, label: payload().services.find((service) => service.type === item.type)?.label || item.type,
                    model: String(item.fields.model || ""), configured: item.configured, isDefault: item.is_default,
                    canSetDefault: item.configured && !item.is_default, canDelete: !item.is_default,
                    deleteReason: item.is_default ? "Choose another default or clear it before deleting." : "",
                  }))}
                  onEdit={(name) => navigate(`/settings/providers/decision/${encodeURIComponent(name)}`)}
                  onSetDefault={(name) => action(() => putJson(`${API}/default`, { name }))}
                  onDelete={(name) => setDeleteTarget(name)}
                  onTest={testConnectionByName}
                  testStates={testStates()} />
                <Show when={payload().default_provider}><button class="btn btn-sm" disabled={busy()} onClick={() => setClearDefaultOpen(true)}>Clear default</button></Show>
                <ProviderPicker items={payload().services.filter((item) => item.label.toLowerCase().includes(search().trim().toLowerCase())).map((item) => ({
                  id: item.type, label: item.label, count: payload().providers.filter((provider) => provider.type === item.type).length,
                }))} onSelect={(id) => navigate(`/settings/providers?tab=decision&new=${id}`)} />
              </>
            }>
              <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
                <ProviderFormPanel
                  title={editing() ? "Edit decision provider" : `Add ${service()?.label}`}
                  busy={busy()}
                  onSubmit={save}
                  onBack={() => navigate(ROOT)}
                  actions={
                    <div class="row-wrap items-center">
                      <button
                        class="btn btn-secondary"
                        type="button"
                        disabled={verifyingForm() || busy()}
                        onClick={testCurrentConnection}
                        title="Test decision provider connection"
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
        title="Delete Decision Provider"
        message={<p>Are you sure you want to delete decision provider <span class="mono">{deleteTarget()}</span>?</p>}
        confirmLabel="Delete"
        confirmVariant="danger"
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDeleteDecisionProvider()}
      />
      <ConfirmDialog
        open={clearDefaultOpen()}
        title="Clear Default Decision Provider"
        message={
          <p>
            Are you sure you want to clear the default decision provider? Agent routing will fall back to environment credentials or standard LLM routing.
          </p>
        }
        confirmLabel="Clear Default"
        confirmVariant="warning"
        onClose={() => setClearDefaultOpen(false)}
        onConfirm={() => void confirmClearDefault()}
      />
    </ProviderSettingsLayout>
  );
}
