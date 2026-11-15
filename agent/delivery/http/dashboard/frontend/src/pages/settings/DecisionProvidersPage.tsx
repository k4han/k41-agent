import { A, useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { ArrowRight, BrainCircuit, RefreshCw, Sparkles } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onCleanup, Show } from "solid-js";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { decisionProviderFields, suggestProviderName } from "@/lib/providerConnections";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";
import { ProviderFormPanel, ProviderEditorTabs } from "./ProviderConnections";
import type { DecisionProvidersPayload } from "@/types";

const ROOT = "/settings/providers?tab=decision";
const API = "/dashboard-api/decision-providers";
type Form = { name: string; type: string; fields: Record<string, string | number> };

export function DecisionProvidersPage(props: {
  embedded?: boolean;
  name?: string;
  providerType?: string;
  onSaved?: (name: string) => void;
  onCancel?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
} = {}) {
  const params = useParams<{ decisionProviderName?: string }>();
  const [query] = useSearchParams<{ new?: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [data, setData] = createSignal<DecisionProvidersPayload>();
  const [error, setError] = createSignal("");
  const [form, setForm] = createSignal<Form>({ name: "", type: "", fields: {} });
  const [initial, setInitial] = createSignal(form());
  const [replacedSecrets, setReplacedSecrets] = createSignal<string[]>([]);
  const [busy, setBusy] = createSignal(false);
  const [verifyingForm, setVerifyingForm] = createSignal(false);
  const [formTestResult, setFormTestResult] = createSignal<{
    ok: boolean;
    latency_ms: number;
    message: string;
    error_code?: string;
  } | null>(null);
  const providerName = () => props.embedded ? props.name : params.decisionProviderName;
  const newType = () => props.embedded ? props.providerType : query.new;
  const editing = () => Boolean(providerName());
  const showForm = () => editing() || Boolean(newType());
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => data()?.providers.find((item) => item.name.toLowerCase() === String(providerName() || "").toLowerCase());
  const dirty = () => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || replacedSecrets().length > 0);
  const discard = () => { setForm(initial()); setReplacedSecrets([]); setFormTestResult(null); };
  const [editorTab, setEditorTab] = createSignal<"decision" | "connection">("decision");
  const editorTabs = [{ value: "decision", label: "Decision" }, { value: "connection", label: "Connection" }] as const;
  const fieldTab = (name: string) => ["model", "base_url", "timeout", "max_retries"].includes(name) ? "decision" : "connection";
  useUnsavedChanges(dirty, discard);
  createEffect(() => props.onDirtyChange?.(dirty()));
  createEffect(() => props.onBusyChange?.(busy() || verifyingForm()));
  onCleanup(() => { props.onDirtyChange?.(false); props.onBusyChange?.(false); });
  const back = () => props.embedded ? props.onCancel?.() : navigate(editing() ? ROOT : "/settings/providers?tab=catalog&kind=decision");

  const testCurrentConnection = async () => {
    if (verifyingForm() || busy()) return;
    setVerifyingForm(true);
    setFormTestResult(null);
    try {
      const body: Record<string, unknown> = {
        type: form().type,
        fields: decisionProviderFields(form().fields, service()?.fields || [], editing(), replacedSecrets()),
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
      const entry = payload.providers.find((item) => item.name.toLowerCase() === String(providerName() || "").toLowerCase());
      if (editing() && !entry) throw new Error("Decision provider not found.");
      const kind = entry?.type || newType() || "";
      const definition = payload.services.find((item) => item.type === kind);
      const fields: Form["fields"] = {};
      for (const field of definition?.fields || []) fields[field.name] = field.input_type === "password" ? "" : entry?.fields[field.name] ?? field.default ?? "";
      const next = { name: entry?.name || suggestProviderName(kind, payload.providers.map((item) => item.name)), type: kind, fields };
      setForm(next); setInitial(next); setReplacedSecrets([]);
      setFormTestResult(null);
    } catch (err) { setData(undefined); setError(err instanceof Error ? err.message : "Failed to load decision providers."); }
  };
  createEffect(() => { providerName(); newType(); void load(); });
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy() || !service()) return;
    setBusy(true);
    try {
      const fields = decisionProviderFields(form().fields, service()!.fields, editing(), replacedSecrets());
      if (editing()) await putJson(`${API}/${encodeURIComponent(form().name)}`, { fields });
      else await postJson(API, { name: form().name, type: form().type, fields });
      setInitial(form()); setReplacedSecrets([]);
      showToast("Decision provider configuration saved.");
      if (editing()) await load();
      if (props.embedded) props.onSaved?.(form().name);
      else if (!editing()) navigate(`/settings/providers/decision/${encodeURIComponent(form().name)}`);
    } catch (err) { showToast(err instanceof Error ? err.message : "Failed to save decision provider.", "error"); }
    finally { setBusy(false); }
  };
  const content = (
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <Show when={!props.embedded}><div class="decision-signpost-banner"><span class="row-wrap items-center"><BrainCircuit size={16} />Decision credentials and models</span><A href="/settings/decisions" class="btn btn-sm">Open Decisions & Routing <ArrowRight size={13} /></A></div></Show>
            <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
              <ProviderFormPanel
                title={editing() ? "Edit decision provider" : `Add ${service()?.label}`}
                busy={busy()}
                onSubmit={save}
                editing={editing()} dirty={dirty()} onDiscard={discard}
                hideBack={props.embedded}
                onBack={back}
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
                <Show when={editing() && !props.embedded}><ProviderEditorTabs id="decision-editor" items={editorTabs} value={editorTab()} onChange={setEditorTab} /></Show>
                <For each={editorTabs}>{(tab) => <div class="provider-field-section" hidden={editing() && !props.embedded && editorTab() !== tab.value} role={editing() && !props.embedded ? "tabpanel" : undefined} id={`decision-editor-panel-${tab.value}`} aria-labelledby={editing() && !props.embedded ? `decision-editor-tab-${tab.value}` : undefined}>
                <Show when={editing() && tab.value === "connection"}>
                  <For each={(service()?.fields || []).filter((field) => field.input_type === "password")}>{(field) => (
                    <div class="stack">
                      <p class="hint">{field.label}: {(current()?.stored_secrets?.[field.name] ?? current()?.has_api_token) ? "Stored" : "Not stored"}.</p>
                      <label><input type="checkbox" checked={replacedSecrets().includes(field.name)} onChange={(event) => setReplacedSecrets((current) => event.currentTarget.checked ? [...current, field.name] : current.filter((name) => name !== field.name))} /> Replace or clear the {field.label.toLowerCase()}</label>
                    </div>
                  )}</For>
                </Show>
                <For each={(service()?.fields || []).filter((field) => fieldTab(field.name) === tab.value)}>{(field) => (
                  <Show when={field.input_type !== "password" || !editing() || replacedSecrets().includes(field.name)}>
                    <label class="stack">{field.label}<input class="input" type={field.input_type} min={field.min} step={field.step ?? (field.input_type === "number" ? "any" : undefined)}
                      required={field.input_type !== "password" && field.required} onInvalid={() => setEditorTab(tab.value)} autocomplete={field.input_type === "password" ? "new-password" : undefined}
                      value={form().fields[field.name] ?? ""} onInput={(event) => setForm({ ...form(), fields: { ...form().fields, [field.name]: event.currentTarget.value } })} />
                      <Show when={field.input_type === "password"}><span class="hint">Leave empty to use the environment credential.</span></Show>
                    </label>
                  </Show>
                )}</For>

                </div>}</For>
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
          </div>
        )}
      </DataGate>
  );
  return (
    <Show when={!props.embedded} fallback={content}>
      <ProviderSettingsLayout
        title={showForm() ? (editing() ? `Edit ${form().name || "Decision Provider"}` : `Add ${service()?.label || "Decision Provider"}`) : "Decision Model Providers"}
        breadcrumbSegments={showForm() ? [
          { label: "Providers", href: "/settings/providers?tab=decision" },
          { label: editing() ? (form().name || "Edit Decision Provider") : "Add Decision Provider" },
        ] : undefined}
      >{content}</ProviderSettingsLayout>
    </Show>
  );
}
