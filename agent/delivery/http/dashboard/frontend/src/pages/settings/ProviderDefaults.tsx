import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";
import { A } from "@solidjs/router";
import { ArrowRight, BrainCircuit, Plus, Save } from "lucide-solid";
import { apiFetch } from "@/lib/api";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import type { DeskConnection, DeskSources } from "@/lib/providerDesk";
import type { ProviderConnectionKind } from "@/lib/providerRoutes";
import { DecisionConnectionSelect } from "./DecisionConnectionSelect";

type DefaultPool = { name: string; members: Array<{ provider: string; model: string }> };

export function ProviderDefaults(props: {
  sources: DeskSources;
  connections: DeskConnection[];
  busy: boolean;
  unavailable: Partial<Record<ProviderConnectionKind, string>>;
  onSave: (kind: ProviderConnectionKind, path: string, body: unknown) => Promise<boolean>;
  onClear: (kind: "decision" | "web", label: string, type?: string) => void;
}) {
  const [pools, setPools] = createSignal<DefaultPool[]>([]);
  const [poolError, setPoolError] = createSignal("");
  const [poolsLoading, setPoolsLoading] = createSignal(true);
  const [target, setTarget] = createSignal("");
  const [model, setModel] = createSignal("");
  let targetSelect: HTMLSelectElement | undefined;
  const serverModel = createMemo(() => {
    const payload = props.sources.llm;
    return String(payload?.settings?.["llm.default_model"]?.value
      ?? (payload?.default_provider ? `${payload.default_provider}/${payload.default_model || ""}` : ""));
  });
  const resetSelection = () => {
    const [provider, ...parts] = serverModel().split("/");
    setTarget(provider || "");
    setModel(parts.join("/"));
  };
  createEffect(resetSelection);
  const candidates = createMemo(() => props.connections.filter((item) => item.kind === "llm" && item.enabled && item.configured));
  const selected = createMemo(() => candidates().find((item) => item.name === target()));
  const decisionProviders = () => props.sources.decision?.providers || [];
  const decisionDefault = () => props.sources.decision?.default_provider || "";
  const selectedDecision = createMemo(() => decisionProviders().find((item) => item.name.toLowerCase() === decisionDefault().toLowerCase()));
  const isPool = () => target() === "@pool";
  createEffect(() => {
    const value = isPool() ? `@pool/${model()}` : target();
    candidates(); pools();
    // Pool options can replace the temporary unavailable option after the value has been applied.
    queueMicrotask(() => { if (targetSelect?.isConnected) targetSelect.value = value; });
  });
  const selection = () => target() ? `${target()}/${model().trim()}` : "";
  useUnsavedChanges(() => Boolean(props.sources.llm) && selection() !== serverModel(), resetSelection);
  const valid = () => isPool() ? pools().some((pool) => pool.name === model() && pool.members.length) : Boolean(selected() && model().trim());
  const loadPools = async () => {
    setPoolsLoading(true); setPoolError("");
    try { setPools((await apiFetch<{ pools: DefaultPool[] }>("/dashboard-api/model-pools")).pools); }
    catch (error) { setPoolError(error instanceof Error ? error.message : "Failed to load model pools."); }
    finally { setPoolsLoading(false); }
  };
  onMount(() => void loadPools());

  return (
    <div class="provider-defaults-grid" data-provider-defaults>
      <form class="panel panel-body stack" onSubmit={(event) => {
        event.preventDefault();
        if (valid() && !props.busy) void props.onSave("llm", "/settings/llm.default_model", { value: selection() });
      }}>
        <div><h3>Global model selection</h3><p class="hint">Choose a connection and model, or an ordered model pool.</p></div>
        <label class="stack">Connection or model pool
          <select ref={targetSelect} class="input" aria-label="Global model target" value={isPool() ? `@pool/${model()}` : target()}
            disabled={props.busy || !props.sources.llm || Boolean(props.unavailable.llm)} onChange={(event) => {
              const value = event.currentTarget.value;
              if (value.startsWith("@pool/")) { setTarget("@pool"); setModel(value.slice(6)); }
              else { setTarget(value); setModel(candidates().find((item) => item.name === value)?.model || ""); }
            }}>
            <option value="" disabled>Select a connection or pool</option>
            <Show when={target() && !selected() && (!isPool() || !pools().some((pool) => pool.name === model()))}>
              <option value={isPool() ? `@pool/${model()}` : target()} disabled>{isPool() ? `@pool/${model()}` : target()} (unavailable)</option>
            </Show>
            <optgroup label="Connections"><For each={candidates()}>{(item) => <option value={item.name}>{item.name}</option>}</For></optgroup>
            <optgroup label="Model pools"><For each={pools()}>{(pool) => <option value={`@pool/${pool.name}`} disabled={!pool.members.length}>@pool/{pool.name}</option>}</For></optgroup>
          </select>
        </label>
        <Show when={!isPool()}>
          <label class="stack">Global model ID
            <input class="input mono" aria-label="Global model ID" list="provider-default-models" value={model()} required
              disabled={props.busy || !selected()} onInput={(event) => setModel(event.currentTarget.value)} />
            <datalist id="provider-default-models"><For each={selected()?.models || []}>{(id) => <option value={id} />}</For></datalist>
          </label>
        </Show>
        <p class="hint">This selection is independent of each connection's default model.</p>
        <Show when={poolsLoading()}><p class="hint" role="status">Loading model pools...</p></Show>
        <Show when={poolError()}><div class="provider-source-error" role="alert"><span>{poolError()}</span><button class="btn btn-sm" type="button" onClick={() => void loadPools()}>Retry model pools</button></div></Show>
        <div><button class="btn btn-primary" type="submit" disabled={props.busy || !valid() || selection() === serverModel() || Boolean(props.unavailable.llm)}><Save size={14} />Save selection</button></div>
      </form>
      <section class="panel panel-body stack" data-decision-default>
        <div><h3 class="row-wrap items-center"><BrainCircuit size={16} />Default Decision connection</h3><p class="hint">Choose the connection used for Decision requests. Its model and endpoint are independent of Models.</p></div>
        <label class="stack">Decision connection
          <DecisionConnectionSelect providers={decisionProviders()} value={decisionDefault()} label="Default Decision connection"
            disabled={props.busy || !props.sources.decision || Boolean(props.unavailable.decision)} configuredOnly allowFallback
            placeholder="Legacy / environment fallback" onChange={(name) => {
              if (name) void props.onSave("decision", "/dashboard-api/decision-providers/default", { name });
              else if (decisionDefault()) props.onClear("decision", "Decision");
            }} />
        </label>
        <Show when={selectedDecision()}>{(provider) => <div class="provider-decision-summary">
          <span class="hint">Decision model</span><code>{String(provider().fields.model || "Not set")}</code>
          <Show when={provider().fields.base_url}><span class="hint">Decision endpoint</span><code>{String(provider().fields.base_url)}</code></Show>
          <A class="hint" href={`/settings/providers/decision/${encodeURIComponent(provider().name)}`}>Configure {provider().name}<ArrowRight size={12} /></A>
        </div>}</Show>
        <Show when={props.sources.decision && !decisionProviders().some((provider) => provider.configured)}><p class="hint">Add and configure a Decision connection to choose it here.</p></Show>
        <div class="row-wrap items-center">
          <A class="btn btn-sm" href="/settings/providers?tab=catalog&kind=decision"><Plus size={13} />Add Decision connection</A>
          <Show when={decisionDefault()}><button class="btn btn-sm" type="button" disabled={props.busy || Boolean(props.unavailable.decision)} onClick={() => props.onClear("decision", "Decision")}>Clear default</button></Show>
        </div>
        <p class="hint">Legacy / environment fallback is used when no default is selected. Configure routing strategy and confidence thresholds in <A href="/settings/decisions">Decisions & Routing</A>.</p>
      </section>
      <section class="panel panel-body stack provider-web-defaults">
        <div><h3>Search & Web defaults</h3><p class="hint">Tools and agents can use these defaults or select a named connection.</p></div>
        <div class="provider-web-defaults-grid">
          <For each={props.sources.web?.services || []}>{(service) => (
            <div class="stack">
              <label class="stack">{service.label}
                <select class="input" aria-label={`${service.label} default connection`} value={props.sources.web?.defaults[service.type] || ""}
                  disabled={props.busy || Boolean(props.unavailable.web)} onChange={(event) => {
                    const name = event.currentTarget.value;
                    event.currentTarget.value = props.sources.web?.defaults[service.type] || "";
                    void props.onSave("web", `/dashboard-api/web-connections/defaults/${encodeURIComponent(service.type)}`, { name });
                  }}>
                  <option value="" disabled>Environment fallback</option>
                  <For each={props.connections.filter((item) => item.kind === "web" && item.type === service.type)}>
                    {(item) => <option value={item.name} disabled={!item.configured}>{item.name}</option>}
                  </For>
                </select>
              </label>
              <Show when={props.sources.web?.defaults[service.type]}><div><button class="btn btn-sm" type="button" disabled={props.busy || Boolean(props.unavailable.web)} onClick={() => props.onClear("web", service.label, service.type)}>Clear {service.label} default</button></div></Show>
            </div>
          )}</For>
        </div>
      </section>
    </div>
  );
}
