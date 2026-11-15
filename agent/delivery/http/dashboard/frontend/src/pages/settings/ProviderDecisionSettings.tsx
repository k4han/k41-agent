import { A } from "@solidjs/router";
import { ArrowRight, BrainCircuit, Plus, Star } from "lucide-solid";
import { createEffect, createMemo, createSignal, onCleanup, Show } from "solid-js";
import { useToast } from "@/components/Toast";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { apiFetch, putJson } from "@/lib/api";
import { decisionProviderType } from "@/lib/providerConnections";
import type { DecisionProvidersPayload } from "@/types";
import { DecisionConnectionSelect } from "./DecisionConnectionSelect";
import { DecisionProvidersPage } from "./DecisionProvidersPage";

const API = "/dashboard-api/decision-providers";

export function ProviderDecisionSettings(props: { name: string; providerType: string; catalogId: string }) {
  const type = createMemo(() => decisionProviderType(props.providerType, props.catalogId));
  const [data, setData] = createSignal<DecisionProvidersPayload>();
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [selectedName, setSelectedName] = createSignal("");
  const [creating, setCreating] = createSignal(false);
  const [dirty, setDirty] = createSignal(false);
  const [editorBusy, setEditorBusy] = createSignal(false);
  const [savingDefault, setSavingDefault] = createSignal(false);
  const [pendingChange, setPendingChange] = createSignal<(() => void) | null>(null);
  const { showToast } = useToast();
  const providers = createMemo(() => (data()?.providers || []).filter((provider) => provider.type === type()));
  const selected = createMemo(() => providers().find((provider) => provider.name === selectedName()));
  const unavailable = () => loading() || editorBusy() || savingDefault();
  let generation = 0;
  onCleanup(() => { generation++; });
  const load = async (preferred = "") => {
    const request = ++generation;
    setLoading(true); setError("");
    try {
      const payload = await apiFetch<DecisionProvidersPayload>(API);
      if (request !== generation) return;
      setData(payload);
      const candidates = payload.providers.filter((provider) => provider.type === type());
      const current = candidates.find((provider) => provider.name.toLowerCase() === (preferred || props.name).toLowerCase())
        || candidates.find((provider) => provider.is_default) || candidates[0];
      setSelectedName(current?.name || "");
    } catch (err) {
      if (request === generation) setError(err instanceof Error ? err.message : "Failed to load Decision connections.");
    } finally { if (request === generation) setLoading(false); }
  };
  createEffect(() => {
    props.name; type();
    setData(undefined); setSelectedName(""); setCreating(false);
    if (type()) void load();
    else { generation++; setLoading(false); setError(""); }
  });
  const changeEditor = (next: () => void) => {
    if (dirty()) setPendingChange(() => next);
    else next();
  };
  const setDefault = async () => {
    const provider = selected();
    if (!provider?.configured || dirty() || unavailable()) return;
    setSavingDefault(true);
    try {
      await putJson(`${API}/default`, { name: provider.name });
      await load(provider.name);
      showToast("Default Decision connection updated.");
    } catch (err) { showToast(err instanceof Error ? err.message : "Failed to update Decision default.", "error"); }
    finally { setSavingDefault(false); }
  };
  return (
    <div class="stack" data-provider-decision>
      <Show when={type()} fallback={
        <section class="panel panel-body stack">
          <h3>Decision</h3>
          <p class="hint">Decision is available through Google and Cloudflare. This provider can supply Models.</p>
          <A class="btn btn-sm" href="/settings/providers?tab=catalog&kind=decision">Browse Decision providers<ArrowRight size={13} /></A>
        </section>
      }>
        <section class="panel panel-body stack">
          <div class="provider-form-heading"><h3 class="row-wrap items-center"><BrainCircuit size={16} />Decision configuration</h3>
            <button class="btn btn-sm" type="button" disabled={unavailable() || !data() || creating()} onClick={() => changeEditor(() => setCreating(true))}><Plus size={14} />Add Decision connection</button>
          </div>
          <p class="hint">Choose a {data()?.services.find((service) => service.type === type())?.label || "Decision"} connection. Decision credentials, model, endpoint, timeout, and retries are saved separately from Models.</p>
          <Show when={loading()}><p class="hint" role="status">Loading Decision connections...</p></Show>
          <Show when={error()}><div class="provider-source-error" role="alert"><span>{error()}</span><button class="btn btn-sm" type="button" disabled={unavailable()} onClick={() => void load(selectedName())}>Retry Decision</button></div></Show>
          <label class="stack">Decision connection
            <DecisionConnectionSelect providers={providers()} value={creating() ? "" : selectedName()} label="Provider Decision connection"
              disabled={unavailable() || !data() || Boolean(error())} placeholder={creating() ? "New Decision connection" : "Select a Decision connection"}
              onChange={(name) => changeEditor(() => { setCreating(false); setSelectedName(name); })} />
          </label>
          <Show when={data() && !providers().length && !creating()}><p class="hint">No Decision connections saved for this provider. Add a connection to configure Decision access.</p></Show>
          <Show when={selected() && !creating()}>
            <div class="row-wrap items-center">
              <span class={`badge ${selected()!.configured ? "badge-success" : "badge-warning"}`}>{selected()!.configured ? "Configured" : "Incomplete"}</span>
              <Show when={selected()!.is_default} fallback={
                <button class="btn btn-sm" type="button" disabled={unavailable() || dirty() || !selected()?.configured || Boolean(error())} onClick={() => void setDefault()}><Star size={13} />Use as Decision default</button>
              }><span class="provider-desk-default"><Star size={12} fill="currentColor" />Default Decision connection</span></Show>
            </div>
          </Show>
          <A class="hint provider-decision-link" href="/settings/providers?tab=defaults">Manage defaults<ArrowRight size={12} /></A>
        </section>
        <Show when={creating() ? "create" : selectedName() ? `edit/${selectedName()}` : ""} keyed>{(key) => (
          <DecisionProvidersPage embedded name={key === "create" ? undefined : key.slice(5)} providerType={type()}
            onDirtyChange={setDirty} onBusyChange={setEditorBusy}
            onSaved={async (savedName) => { await load(savedName); setCreating(false); }}
            onCancel={() => changeEditor(() => setCreating(false))} />
        )}</Show>
        <p class="hint">Test Decision access independently. Configure routing strategy and confidence thresholds in <A href="/settings/decisions">Decisions & Routing</A>.</p>
      </Show>
      <ConfirmDialog open={Boolean(pendingChange())} title="Discard Decision Changes" confirmLabel="Discard changes" confirmVariant="warning"
        message={<p>Discard unsaved Decision changes and open another configuration?</p>}
        onClose={() => setPendingChange(null)} onConfirm={() => { const next = pendingChange(); setPendingChange(null); next?.(); }} />
    </div>
  );
}
