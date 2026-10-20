import { ArrowDown, ArrowUp, Plus, RefreshCw, Save, Star, Trash2 } from "lucide-solid";
import { createSignal, For, onMount, Show } from "solid-js";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ModelPicker } from "@/components/ModelPicker";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import type { ModelCatalog } from "@/types";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";

type Member = { provider: string; model: string };
type PoolConfig = {
  members: Member[];
  retry_count: number;
  cooldown_seconds: number;
  quota_cooldown_seconds: number;
};
type Pool = PoolConfig & {
  name: string;
  is_default: boolean;
  status: Array<Member & { available: boolean; error_code: string | null; retry_at: string | null }>;
};

const emptyConfig = (): PoolConfig => ({ members: [], retry_count: 2, cooldown_seconds: 60, quota_cooldown_seconds: 3600 });

export function ModelPoolsPage() {
  const { showToast } = useToast();
  const [pools, setPools] = createSignal<Pool[]>([]);
  const [catalogs, setCatalogs] = createSignal<ModelCatalog[]>([]);
  const [selected, setSelected] = createSignal<string | null>(null);
  const [name, setName] = createSignal("");
  const [form, setForm] = createSignal<PoolConfig>(emptyConfig());
  const [dirty, setDirty] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<string | null>(null);
  const [pendingSelection, setPendingSelection] = createSignal<Pool | "new" | null>(null);
  useUnsavedChanges(dirty);

  const load = async () => {
    try {
      setError("");
      const [poolData, modelData] = await Promise.all([
        apiFetch<{ pools: Pool[] }>("/dashboard-api/model-pools"),
        apiFetch<{ providers: ModelCatalog[] }>("/providers/models"),
      ]);
      setPools(poolData.pools);
      setCatalogs(modelData.providers.filter((catalog) => catalog.provider !== "@pool"));
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };
  onMount(() => void load());

  const edit = (pool: Pool | "new") => {
    setSelected(pool === "new" ? "" : pool.name);
    setName(pool === "new" ? "" : pool.name);
    setForm(pool === "new" ? emptyConfig() : {
      members: pool.members.map((member) => ({ ...member })), retry_count: pool.retry_count,
      cooldown_seconds: pool.cooldown_seconds, quota_cooldown_seconds: pool.quota_cooldown_seconds,
    });
    setDirty(false);
  };
  const select = (pool: Pool | "new") => {
    if (dirty()) setPendingSelection(pool);
    else edit(pool);
  };
  const update = (patch: Partial<PoolConfig>) => {
    setForm((current) => ({ ...current, ...patch }));
    setDirty(true);
  };
  const move = (index: number, direction: number) => {
    const members = [...form().members];
    const next = index + direction;
    if (next < 0 || next >= members.length) return;
    [members[index], members[next]] = [members[next], members[index]];
    update({ members });
  };
  const perform = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await action();
      showToast(message);
      await load();
    } catch (err) {
      showToast(String(err), "error");
    } finally {
      setBusy(false);
    }
  };
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy()) return;
    const creating = selected() === "";
    await perform(async () => {
      if (creating) await postJson("/dashboard-api/model-pools", { name: name(), ...form() });
      else await putJson(`/dashboard-api/model-pools/${encodeURIComponent(selected()!)}`, form());
      setSelected(name());
      setDirty(false);
    }, "Model pool saved.");
  };
  const status = () => pools().find((pool) => pool.name === selected())?.status || [];

  return (
    <ProviderSettingsLayout title="Model Pools" contentWidth="wide"
      actions={<button class="btn btn-primary" disabled={busy()} onClick={() => select("new")}><Plus size={14} /> New Pool</button>}>
      <p class="hint">Group models under one name and use them in priority order.</p>
      <Show when={error()}><div class="panel"><p role="alert">{error()}</p><button class="btn" onClick={() => void load()}>Retry</button></div></Show>
      <Show when={!loading()} fallback={<p class="hint">Loading model pools...</p>}>
        <div class="model-pools-layout">
          <div class="stack">
            <Show when={pools().length} fallback={<div class="panel empty">No model pools yet. Create a pool to add priority models.</div>}>
              <For each={pools()}>{(pool) => (
                <button class={`panel model-pool-card ${selected() === pool.name ? "is-selected" : ""}`}
                  disabled={busy()} onClick={() => select(pool)} aria-pressed={selected() === pool.name}>
                  <strong>{pool.name} <Show when={pool.is_default}><Star size={12} aria-label="Default pool" /></Show></strong>
                  <span class="hint">{pool.members.length} models · Priority fallback</span>
                </button>
              )}</For>
            </Show>
          </div>
          <Show when={selected() !== null} fallback={<div class="panel empty">Select a pool or create a new one.</div>}>
            <form class="panel stack" onSubmit={save}>
              <div class="field">
                <label for="model-pool-name">Name</label>
                <input class="input" id="model-pool-name" required maxLength={64} pattern="[a-z0-9][a-z0-9_-]{0,63}"
                  placeholder="small" disabled={busy() || selected() !== ""} value={name()}
                  onInput={(event) => { setName(event.currentTarget.value); setDirty(true); }} />
                <p class="hint">Use lowercase letters, numbers, underscores or hyphens. The name cannot be changed after creation.</p>
              </div>
              <div>
                <h3>Priority models</h3>
                <p class="hint">The first available model handles the request. The next model is used when an availability error occurs.</p>
              </div>
              <For each={form().members}>{(member, index) => (
                <div class="model-pool-member">
                  <span class="hint">{index() + 1}</span>
                  <div class="model-pool-member-picker">
                    <ModelPicker catalogs={catalogs()} providerNames={catalogs().map((catalog) => catalog.provider)}
                      defaultProvider={catalogs()[0]?.provider || ""} defaultModel="" resolveDefault
                      provider={member.provider} model={member.model} disabled={busy()}
                      onChange={(provider, model) => update({ members: form().members.map((item, i) => i === index() ? { provider, model } : item) })} />
                    <Show when={status().find((item) => item.provider === member.provider && item.model === member.model)}>{(state) => (
                      <p class="hint">{state().available ? "Available" : state().retry_at
                        ? `${state().error_code || "Unavailable"} · Retry after ${new Date(state().retry_at!).toLocaleString()}` : "Provider unavailable"}</p>
                    )}</Show>
                  </div>
                  <button class="btn btn-icon" type="button" aria-label={`Move model ${index() + 1} up`} disabled={busy() || index() === 0} onClick={() => move(index(), -1)}><ArrowUp size={14} /></button>
                  <button class="btn btn-icon" type="button" aria-label={`Move model ${index() + 1} down`} disabled={busy() || index() === form().members.length - 1} onClick={() => move(index(), 1)}><ArrowDown size={14} /></button>
                  <button class="btn btn-icon" type="button" aria-label={`Remove model ${index() + 1}`} disabled={busy()} onClick={() => update({ members: form().members.filter((_, i) => i !== index()) })}><Trash2 size={14} /></button>
                </div>
              )}</For>
              <button class="btn" type="button" disabled={busy() || !catalogs().length}
                onClick={() => update({ members: [...form().members, { provider: catalogs()[0]?.provider || "", model: "" }] })}><Plus size={14} /> Add Model</button>
              <details>
                <summary>Retry and recovery settings</summary>
                <div class="stack" style="margin-top: 12px;">
                  <For each={[
                    { key: "retry_count" as const, label: "Retries before fallback", min: 0, max: 5 },
                    { key: "cooldown_seconds" as const, label: "Recovery delay (seconds)", min: 1, max: undefined },
                    { key: "quota_cooldown_seconds" as const, label: "Quota recovery delay (seconds)", min: 1, max: undefined },
                  ]}>{(field) => <div class="field"><label for={`pool-${field.key}`}>{field.label}</label>
                    <input class="input" id={`pool-${field.key}`} type="number" required step="1" min={field.min} max={field.max}
                      value={form()[field.key]} disabled={busy()} onInput={(event) => update({ [field.key]: event.currentTarget.valueAsNumber })} />
                  </div>}</For>
                </div>
              </details>
              <p class="hint">Pools use automatic reasoning effort. Recovery state is held in memory and resets when the application restarts.</p>
              <div class="model-pool-actions">
                <button class="btn btn-primary" type="submit" disabled={busy() || !dirty() || !form().members.length || form().members.some((member) => !member.model)}><Save size={14} /> Save</button>
                <Show when={selected()}>
                  <button class="btn" type="button" disabled={busy() || dirty()} onClick={() => void perform(() => putJson("/settings/llm.default_model", { value: `@pool/${selected()}` }), "Default model updated.")}><Star size={14} /> Set Default</button>
                  <button class="btn" type="button" disabled={busy()} onClick={() => void perform(() => postJson(`/dashboard-api/model-pools/${encodeURIComponent(selected()!)}/reset`, {}), "Recovery state reset.")}><RefreshCw size={14} /> Reset State</button>
                  <button class="btn btn-danger" type="button" disabled={busy()} onClick={() => setDeleteTarget(selected())}><Trash2 size={14} /> Delete</button>
                </Show>
              </div>
            </form>
          </Show>
        </div>
      </Show>
      <ConfirmDialog open={deleteTarget() !== null} title="Delete Model Pool" confirmLabel="Delete" confirmVariant="danger" loading={busy()}
        message={`Delete pool '${deleteTarget()}'? Pools referenced by agents or the default model cannot be deleted.`}
        onClose={() => setDeleteTarget(null)} onConfirm={() => void perform(async () => {
          await deleteJson(`/dashboard-api/model-pools/${encodeURIComponent(deleteTarget()!)}`);
          setDeleteTarget(null); setSelected(null); setDirty(false);
        }, "Model pool deleted.")} />
      <ConfirmDialog open={pendingSelection() !== null} title="Discard Changes" confirmLabel="Discard"
        message="Discard unsaved changes to this pool?" onClose={() => setPendingSelection(null)} onConfirm={() => {
          const next = pendingSelection(); setPendingSelection(null); if (next) edit(next);
        }} />
    </ProviderSettingsLayout>
  );
}
