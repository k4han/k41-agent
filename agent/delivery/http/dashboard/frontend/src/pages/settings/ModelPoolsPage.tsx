import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  CornerDownRight,
  Info,
  Layers,
  List,
  Plus,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  Sparkles,
  Star,
  Trash2,
} from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onMount, Show } from "solid-js";

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

type HealthStatus = {
  label: string;
  type: "success" | "warning" | "danger" | "neutral";
};

const emptyConfig = (): PoolConfig => ({
  members: [],
  retry_count: 2,
  cooldown_seconds: 60,
  quota_cooldown_seconds: 3600,
});

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return remainder > 0 ? `${mins}m ${remainder}s` : `${mins} min`;
  }
  const hours = Math.floor(seconds / 3600);
  const remainder = Math.floor((seconds % 3600) / 60);
  return remainder > 0 ? `${hours}h ${remainder}m` : `${hours} hr`;
}

function getPoolHealth(pool: Pool | undefined): HealthStatus {
  if (!pool || !pool.members.length) return { label: "Empty", type: "neutral" };
  if (!pool.status || !pool.status.length) return { label: "Operational", type: "success" };
  const allAvailable = pool.status.every((m) => m.available);
  if (allAvailable) return { label: "Operational", type: "success" };
  const allUnavailable = pool.status.every((m) => !m.available);
  if (allUnavailable) return { label: "Exhausted", type: "danger" };
  return { label: "Degraded", type: "warning" };
}

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

  // Mobile navigation and search filter
  const [filter, setFilter] = createSignal("");
  const [mobileView, setMobileView] = createSignal<"carousel" | "list">("carousel");
  const [mobileCollapsed, setMobileCollapsed] = createSignal(false);
  let listRef: HTMLDivElement | undefined;

  useUnsavedChanges(dirty);

  const currentPool = createMemo(() => pools().find((pool) => pool.name === selected()));

  const filteredPools = createMemo(() => {
    const query = filter().trim().toLowerCase();
    if (!query) return pools();
    return pools().filter(
      (pool) =>
        pool.name.toLowerCase().includes(query) ||
        pool.members.some(
          (m) => m.model.toLowerCase().includes(query) || m.provider.toLowerCase().includes(query),
        ),
    );
  });

  createEffect(() => {
    const sel = selected();
    if (!sel || !listRef) return;
    const activeEl = listRef.querySelector<HTMLElement>(".model-pool-card.is-selected");
    if (activeEl && typeof activeEl.scrollIntoView === "function") {
      activeEl.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    }
  });

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
    setForm(
      pool === "new"
        ? emptyConfig()
        : {
            members: pool.members.map((member) => ({ ...member })),
            retry_count: pool.retry_count,
            cooldown_seconds: pool.cooldown_seconds,
            quota_cooldown_seconds: pool.quota_cooldown_seconds,
          },
    );
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

  const addMember = () => {
    const catalog = catalogs()[0];
    if (!catalog) return;
    update({
      members: [...form().members, { provider: catalog.provider, model: catalog.default_model || "" }],
    });
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

  const status = () => currentPool()?.status || [];

  return (
    <ProviderSettingsLayout
      title="Model Pools"
      contentWidth="wide"
      actions={
        <button class="btn btn-primary" disabled={busy()} onClick={() => select("new")}>
          <Plus size={14} /> New Pool
        </button>
      }
    >
      <p class="hint">
        Group models under one name and use them in priority order. If a model encounters rate limits or downtime, requests automatically fall back to the next member.
      </p>

      <Show when={error()}>
        <div class="panel">
          <p role="alert">{error()}</p>
          <button class="btn" onClick={() => void load()}>Retry</button>
        </div>
      </Show>

      <Show when={!loading()} fallback={<p class="hint">Loading model pools...</p>}>
        <div class="model-pools-layout">
          {/* Left Sidebar */}
          <div class="model-pools-sidebar stack">
            <div class="model-pools-sidebar-header">
              <div class="model-pools-sidebar-header-left">
                <span class="model-pools-sidebar-title">Configured Pools</span>
                <span class="badge">{pools().length}</span>
              </div>
              <div class="model-pools-sidebar-controls">
                <Show when={pools().length > 1}>
                  <button
                    class="btn btn-icon btn-sm model-pools-mobile-only"
                    type="button"
                    title={mobileView() === "carousel" ? "Switch to list view" : "Switch to carousel view"}
                    onClick={() => setMobileView(mobileView() === "carousel" ? "list" : "carousel")}
                  >
                    <List size={13} />
                  </button>
                  <button
                    class="btn btn-icon btn-sm model-pools-mobile-only"
                    type="button"
                    title={mobileCollapsed() ? "Expand pools list" : "Collapse pools list"}
                    onClick={() => setMobileCollapsed(!mobileCollapsed())}
                  >
                    <Show when={mobileCollapsed()} fallback={<ChevronUp size={13} />}>
                      <ChevronDown size={13} />
                    </Show>
                  </button>
                </Show>
              </div>
            </div>

            <Show when={mobileCollapsed()}>
              <div class="model-pools-mobile-collapsed-bar">
                <span class="hint">
                  Pool: <strong class="mono">{selected() === "" ? "New Pool" : selected() ? `@pool/${selected()}` : "None"}</strong>
                </span>
                <button
                  class="btn btn-sm btn-secondary"
                  type="button"
                  onClick={() => setMobileCollapsed(false)}
                >
                  Show Pools ({pools().length})
                </button>
              </div>
            </Show>

            <Show when={!mobileCollapsed()}>
              <Show when={pools().length >= 4}>
                <div class="model-pools-search-wrap">
                  <Search size={13} class="model-pools-search-icon" />
                  <input
                    class="input model-pools-search-input"
                    type="search"
                    placeholder="Filter pools or models..."
                    value={filter()}
                    onInput={(e) => setFilter(e.currentTarget.value)}
                  />
                  <Show when={filter()}>
                    <button
                      class="model-pools-search-clear"
                      type="button"
                      title="Clear filter"
                      onClick={() => setFilter("")}
                    >
                      ✕
                    </button>
                  </Show>
                </div>
              </Show>

              <div
                ref={listRef}
                class={`model-pools-sidebar-list ${mobileView() === "carousel" ? "is-carousel" : "is-list"}`}
              >
                <Show
                  when={filteredPools().length}
                  fallback={
                    <div class="panel empty model-pools-empty-list">
                      <Show
                        when={filter()}
                        fallback={
                          <>
                            <div class="model-pools-empty-icon"><Layers size={24} /></div>
                            <p><strong>No model pools yet.</strong> Create a pool to add priority models.</p>
                            <button class="btn btn-primary btn-sm" disabled={busy()} onClick={() => select("new")}>
                              <Plus size={14} /> New Pool
                            </button>
                          </>
                        }
                      >
                        <p class="hint">No pools match "{filter()}".</p>
                        <button class="btn btn-sm" type="button" onClick={() => setFilter("")}>
                          Clear Filter
                        </button>
                      </Show>
                    </div>
                  }
                >
                  <For each={filteredPools()}>{(pool) => {
                    const health = () => getPoolHealth(pool);
                    const isSelected = () => selected() === pool.name;
                    return (
                      <button
                        class={`panel model-pool-card ${isSelected() ? "is-selected" : ""}`}
                        disabled={busy()}
                        onClick={() => select(pool)}
                        aria-pressed={isSelected()}
                      >
                        <div class="model-pool-card-header">
                          <strong>
                            {pool.name}
                            <Show when={pool.is_default}>
                              <Star size={12} fill="currentColor" aria-label="Default pool" />
                            </Show>
                          </strong>
                          <div class="model-pool-card-badges">
                            <Show when={pool.is_default}>
                              <span class="badge badge-warning model-pool-mini-badge">Default</span>
                            </Show>
                            <span class={`model-pool-health-dot status-${health().type}`} title={health().label} />
                          </div>
                        </div>
                        <Show when={pool.members.length} fallback={<span class="hint">No models configured</span>}>
                          <div class="model-pool-card-chain">
                            <For each={pool.members.slice(0, 3)}>{(m, idx) => (
                              <>
                                <Show when={idx() > 0}><ArrowRight size={10} class="text-muted" /></Show>
                                <span class="model-pool-card-chip" title={`${m.provider}/${m.model}`}>
                                  {m.model || m.provider}
                                </span>
                              </>
                            )}</For>
                            <Show when={pool.members.length > 3}>
                              <span class="model-pool-card-chip">+{pool.members.length - 3}</span>
                            </Show>
                          </div>
                        </Show>
                        <span class="hint">
                          {pool.members.length} {pool.members.length === 1 ? "model" : "models"} · Priority fallback
                        </span>
                      </button>
                    );
                  }}</For>
                </Show>
              </div>
            </Show>
          </div>

          {/* Right Detail / Form */}
          <Show
            when={selected() !== null}
            fallback={
              <div class="panel empty model-pools-empty-detail">
                <div class="model-pools-empty-icon"><Layers size={36} /></div>
                <h3>Select a pool or create a new one.</h3>
                <p class="hint">Configure a prioritized cascade of models with automatic failover and recovery cooldowns.</p>
                <button class="btn btn-primary" disabled={busy()} onClick={() => select("new")}>
                  <Plus size={14} /> New Pool
                </button>
              </div>
            }
          >
            <form class="panel stack model-pool-form" onSubmit={save}>
              {/* Header card */}
              <div class="model-pool-header-card">
                <div class="model-pool-header-main">
                  <div class="model-pool-icon-wrap">
                    <Layers size={20} />
                  </div>
                  <div>
                    <div class="model-pool-title-row">
                      <h2 class="model-pool-title mono">
                        {selected() === "" ? "New Model Pool" : `@pool/${name()}`}
                      </h2>
                      <Show when={selected() && currentPool()?.is_default}>
                        <span class="badge badge-warning">
                          <Star size={11} fill="currentColor" /> Default Pool
                        </span>
                      </Show>
                      <Show when={selected()}>
                        <span class={`badge badge-${getPoolHealth(currentPool()).type}`}>
                          ● {getPoolHealth(currentPool()).label}
                        </span>
                      </Show>
                    </div>
                    <p class="hint">
                      {selected() === ""
                        ? "Create a pool to combine multiple models under one name with automatic failover."
                        : `Target this pool in agents and chat with provider "@pool" and model "${name()}"`}
                    </p>
                  </div>
                </div>
              </div>

              {/* Pool Name */}
              <div class="field">
                <label for="model-pool-name">Name</label>
                <div class="model-pool-name-input-wrap">
                  <span class="model-pool-name-prefix">@pool/</span>
                  <input
                    class="input"
                    id="model-pool-name"
                    required
                    maxLength={64}
                    pattern="[a-z0-9][a-z0-9_-]{0,63}"
                    placeholder="small"
                    disabled={busy() || selected() !== ""}
                    value={name()}
                    onInput={(event) => {
                      setName(event.currentTarget.value);
                      setDirty(true);
                    }}
                  />
                </div>
                <p class="hint">Use lowercase letters, numbers, underscores or hyphens. The name cannot be changed after creation.</p>
              </div>

              {/* Priority Fallback Chain */}
              <div class="model-pool-cascade-section">
                <div class="model-pool-section-header">
                  <div>
                    <h3 class="model-pool-section-title">Priority models</h3>
                    <p class="hint">The first available model handles the request. The next model is used when an availability error occurs.</p>
                  </div>
                  <button
                    class="btn btn-sm"
                    type="button"
                    disabled={busy() || !catalogs().length}
                    onClick={addMember}
                  >
                    <Plus size={14} /> Add Model
                  </button>
                </div>

                <div class="model-pool-members-chain">
                  <Show
                    when={form().members.length}
                    fallback={
                      <div class="model-pool-members-empty">
                        <p class="hint">No models in this pool yet.</p>
                        <button
                          class="btn btn-sm btn-primary"
                          type="button"
                          disabled={busy() || !catalogs().length}
                          onClick={addMember}
                        >
                          <Plus size={14} /> Add Model
                        </button>
                      </div>
                    }
                  >
                    <For each={form().members}>{(member, index) => {
                      const isFirst = () => index() === 0;
                      const isLast = () => index() === form().members.length - 1;
                      const memberStatus = () =>
                        status().find((item) => item.provider === member.provider && item.model === member.model);
                      return (
                        <div class="model-pool-member-wrapper">
                          <Show when={index() > 0}>
                            <div class="model-pool-connector">
                              <div class="model-pool-connector-line" />
                              <div class="model-pool-connector-pill">
                                <ArrowDown size={11} />
                                <span>fallback on failure</span>
                              </div>
                              <div class="model-pool-connector-line" />
                            </div>
                          </Show>
                          <div class="model-pool-member">
                            <div class="model-pool-member-badge">
                              <span class={`pool-priority-tag ${isFirst() ? "priority-primary" : "priority-fallback"}`}>
                                <Show when={isFirst()} fallback={<CornerDownRight size={11} />}>
                                  <Sparkles size={11} />
                                </Show>
                                {isFirst() ? "1 · Primary" : `${index() + 1} · Fallback`}
                              </span>
                            </div>
                            <div class="model-pool-member-picker">
                              <ModelPicker
                                catalogs={catalogs()}
                                providerNames={catalogs().map((catalog) => catalog.provider)}
                                defaultProvider={catalogs()[0]?.provider || ""}
                                defaultModel=""
                                resolveDefault
                                provider={member.provider}
                                model={member.model}
                                disabled={busy()}
                                onChange={(provider, model) =>
                                  update({
                                    members: form().members.map((item, i) =>
                                      i === index() ? { provider, model } : item,
                                    ),
                                  })
                                }
                              />
                              <Show when={memberStatus()}>{(state) => (
                                <p class="hint model-pool-member-status-hint">
                                  <span
                                    class={`badge ${
                                      state().available
                                        ? "badge-success"
                                        : state().retry_at
                                          ? "badge-warning"
                                          : "badge-danger"
                                    }`}
                                  >
                                    <Show
                                      when={state().available}
                                      fallback={
                                        <Show when={state().retry_at} fallback={<AlertTriangle size={11} />}>
                                          <Clock size={11} />
                                        </Show>
                                      }
                                    >
                                      <CheckCircle2 size={11} />
                                    </Show>
                                    {state().available
                                      ? "Available"
                                      : state().retry_at
                                        ? `${state().error_code || "Unavailable"} · Retry after ${new Date(
                                            state().retry_at!,
                                          ).toLocaleString()}`
                                        : "Provider unavailable"}
                                  </span>
                                </p>
                              )}</Show>
                            </div>
                            <div class="model-pool-member-actions">
                              <button
                                class="btn btn-icon btn-sm"
                                type="button"
                                aria-label={`Move model ${index() + 1} up`}
                                disabled={busy() || isFirst()}
                                onClick={() => move(index(), -1)}
                                title="Move model up in priority"
                              >
                                <ArrowUp size={14} />
                              </button>
                              <button
                                class="btn btn-icon btn-sm"
                                type="button"
                                aria-label={`Move model ${index() + 1} down`}
                                disabled={busy() || isLast()}
                                onClick={() => move(index(), 1)}
                                title="Move model down in priority"
                              >
                                <ArrowDown size={14} />
                              </button>
                              <button
                                class="btn btn-icon btn-sm model-pool-remove-btn"
                                type="button"
                                aria-label={`Remove model ${index() + 1}`}
                                disabled={busy()}
                                onClick={() =>
                                  update({
                                    members: form().members.filter((_, i) => i !== index()),
                                  })
                                }
                                title="Remove model from pool"
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    }}</For>
                  </Show>
                </div>
              </div>

              {/* Retry & Recovery Settings */}
              <details class="model-pool-details" open>
                <summary class="model-pool-details-summary">
                  <div class="model-pool-details-title">
                    <SlidersHorizontal size={14} />
                    <span>Retry and recovery settings</span>
                  </div>
                  <span class="hint">Failure thresholds & recovery cooldowns</span>
                </summary>
                <div class="model-pool-settings-grid">
                  <div class="field">
                    <label for="pool-retry_count">Retries before fallback</label>
                    <input
                      class="input"
                      id="pool-retry_count"
                      type="number"
                      required
                      step="1"
                      min={0}
                      max={5}
                      value={form().retry_count}
                      disabled={busy()}
                      onInput={(event) => update({ retry_count: event.currentTarget.valueAsNumber })}
                    />
                    <p class="hint">Retries on active model before switching to next fallback (0 to 5).</p>
                  </div>
                  <div class="field">
                    <label for="pool-cooldown_seconds">Recovery delay (seconds)</label>
                    <input
                      class="input"
                      id="pool-cooldown_seconds"
                      type="number"
                      required
                      step="1"
                      min={1}
                      value={form().cooldown_seconds}
                      disabled={busy()}
                      onInput={(event) => update({ cooldown_seconds: event.currentTarget.valueAsNumber })}
                    />
                    <p class="hint">Transient error cooldown ({formatDuration(form().cooldown_seconds)}).</p>
                  </div>
                  <div class="field">
                    <label for="pool-quota_cooldown_seconds">Quota recovery delay (seconds)</label>
                    <input
                      class="input"
                      id="pool-quota_cooldown_seconds"
                      type="number"
                      required
                      step="1"
                      min={1}
                      value={form().quota_cooldown_seconds}
                      disabled={busy()}
                      onInput={(event) => update({ quota_cooldown_seconds: event.currentTarget.valueAsNumber })}
                    />
                    <p class="hint">Rate limit / quota exhaustion cooldown ({formatDuration(form().quota_cooldown_seconds)}).</p>
                  </div>
                </div>
              </details>

              {/* Architecture info */}
              <div class="model-pool-notice">
                <Info size={15} class="model-pool-notice-icon" />
                <div class="model-pool-notice-body">
                  <p class="hint">
                    Pools use automatic reasoning effort. Recovery state is held in memory and resets when the application restarts.
                  </p>
                </div>
              </div>

              {/* Action Buttons */}
              <div class="model-pool-actions">
                <div style="display: flex; align-items: center; gap: 8px;">
                  <button
                    class="btn btn-primary"
                    type="submit"
                    disabled={busy() || !dirty() || !form().members.length || form().members.some((member) => !member.model)}
                  >
                    <Save size={14} /> Save
                  </button>
                  <Show when={dirty()}>
                    <span class="badge badge-warning model-pool-unsaved-badge">Unsaved changes</span>
                  </Show>
                </div>
                <div class="model-pool-actions-secondary">
                  <Show when={selected()}>
                    <button
                      class="btn"
                      type="button"
                      disabled={busy() || dirty()}
                      onClick={() =>
                        void perform(
                          () => putJson("/settings/llm.default_model", { value: `@pool/${selected()}` }),
                          "Default model updated.",
                        )
                      }
                    >
                      <Star size={14} /> Set Default
                    </button>
                    <button
                      class="btn"
                      type="button"
                      disabled={busy()}
                      onClick={() =>
                        void perform(
                          () =>
                            postJson(
                              `/dashboard-api/model-pools/${encodeURIComponent(selected()!)}/reset`,
                              {},
                            ),
                          "Recovery state reset.",
                        )
                      }
                    >
                      <RefreshCw size={14} /> Reset State
                    </button>
                    <button
                      class="btn btn-danger"
                      type="button"
                      disabled={busy()}
                      onClick={() => setDeleteTarget(selected())}
                    >
                      <Trash2 size={14} /> Delete
                    </button>
                  </Show>
                </div>
              </div>
            </form>
          </Show>
        </div>
      </Show>

      <ConfirmDialog
        open={deleteTarget() !== null}
        title="Delete Model Pool"
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={busy()}
        message={`Delete pool '${deleteTarget()}'? Pools referenced by agents or the default model cannot be deleted.`}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() =>
          void perform(async () => {
            await deleteJson(`/dashboard-api/model-pools/${encodeURIComponent(deleteTarget()!)}`);
            setDeleteTarget(null);
            setSelected(null);
            setDirty(false);
          }, "Model pool deleted.")
        }
      />

      <ConfirmDialog
        open={pendingSelection() !== null}
        title="Discard Changes"
        confirmLabel="Discard"
        message="Discard unsaved changes to this pool?"
        onClose={() => setPendingSelection(null)}
        onConfirm={() => {
          const next = pendingSelection();
          setPendingSelection(null);
          if (next) edit(next);
        }}
      />
    </ProviderSettingsLayout>
  );
}
