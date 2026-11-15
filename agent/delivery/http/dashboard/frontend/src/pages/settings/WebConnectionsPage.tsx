import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";
import { RefreshCw, Sparkles } from "lucide-solid";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { DataGate } from "@/components/State";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { connectionReturnTo } from "@/lib/providerRoutes";
import type { WebConnectionsPayload } from "@/types";
import { suggestProviderName } from "@/lib/providerConnections";
import { ProviderFormPanel } from "./ProviderConnections";
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
  const [verifyingForm, setVerifyingForm] = createSignal(false);
  const [formTestResult, setFormTestResult] = createSignal<{
    ok: boolean;
    latency_ms: number;
    message: string;
    error_code?: string;
  } | null>(null);
  const isEditing = () => Boolean(params.connectionName);
  const showForm = () => isEditing() || Boolean(query.new);
  const dirty = () => showForm() && (JSON.stringify(form()) !== JSON.stringify(initial()) || changeKey());
  const discard = () => { setForm(initial()); setChangeKey(false); setFormTestResult(null); };
  useUnsavedChanges(dirty, discard);
  const service = createMemo(() => data()?.services.find((item) => item.type === form().type));
  const current = () => {
    const name = params.connectionName || "";
    if (!name) return undefined;
    return data()?.connections.find((item) => item.name.toLowerCase() === name.toLowerCase());
  };
  const returnTo = () => connectionReturnTo(String(query.returnTo || ""));

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
        const entry = payload.connections.find((item) => item.name.toLowerCase() === (params.connectionName || "").toLowerCase());
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
    void load();
  });
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
      }
      setInitial(form()); setChangeKey(false);
      if (isEditing()) await load();
      showToast("Web connection saved.");
      const destination = query.returnTo ? returnTo() : `/settings/providers/web/${encodeURIComponent(form().name)}`;
      navigate(destination);
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
            <Show when={service()} fallback={<div class="panel panel-body stack"><h3>Provider not found</h3><button class="btn" onClick={() => navigate(ROOT)}>Back to Providers</button></div>}>
            <ProviderFormPanel
              title={isEditing() ? "Edit connection" : `Add ${service()?.label || "connection"}`}
              busy={busy()}
              onSubmit={save}
              editing={isEditing()} dirty={dirty()} onDiscard={discard}
              onBack={() => navigate(query.returnTo ? returnTo() : isEditing() ? ROOT : "/settings/providers?tab=catalog&kind=web")}
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
          </div>
        )}
      </DataGate>
    </ProviderSettingsLayout>
  );
}
