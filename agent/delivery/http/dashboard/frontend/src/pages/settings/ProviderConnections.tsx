import { createEffect, createMemo, createSignal, For, Show, type JSX } from "solid-js";
import { ArrowLeft, Edit3, Plus, RefreshCw, Sparkles, Star, Trash2 } from "lucide-solid";

const PROVIDER_PAGE_SIZE = 12;

export type ProviderConnectionItem = {
  name: string;
  label: string;
  model?: string;
  configured: boolean;
  isDefault: boolean;
  enabled?: boolean;
  canSetDefault: boolean;
  canDelete: boolean;
  defaultReason?: string;
  deleteReason?: string;
};

export type ConnectionTestState = {
  loading?: boolean;
  ok?: boolean;
  latency_ms?: number;
  message?: string;
};

export function ProviderConnectionList(props: {
  items: ProviderConnectionItem[];
  busy?: boolean;
  emptyMessage?: string;
  onEdit: (name: string) => void;
  onSetDefault: (name: string) => void;
  onDelete: (name: string) => void;
  onTest?: (name: string) => void;
  testStates?: Record<string, ConnectionTestState>;
}) {
  return (
    <section class="panel settings-section-card" data-provider-connections>
      <div class="panel-header"><div class="panel-title">Saved configurations</div><span class="badge">{props.items.length}</span></div>
      <div class="panel-body provider-connection-list">
        <Show when={props.items.length} fallback={<p class="hint">{props.emptyMessage || "No saved configurations."}</p>}>
          <For each={props.items}>{(item) => (
            <div class="provider-connection-row" data-connection-name={item.name}>
              <div class="provider-connection-info">
                <div class="provider-connection-heading">
                  <div class="provider-connection-name">
                    <span class={`provider-connection-status ${item.configured ? "is-ready" : "is-incomplete"}`} role="img" aria-label={item.configured ? "Configuration complete" : "Configuration incomplete"} title={item.configured ? "Configuration complete" : "Configuration incomplete"} />
                    <strong title={item.name}>{item.name}</strong>
                  </div>
                  <Show when={!item.configured}><span class="badge badge-warning">Incomplete</span></Show>
                  <Show when={item.enabled === false}><span class="badge">Disabled</span></Show>
                  <Show when={item.isDefault}><span class="badge badge-info">Default</span></Show>
                  <Show when={props.testStates?.[item.name] && !props.testStates?.[item.name]?.loading}>
                    <span
                      class={`badge ${props.testStates?.[item.name]?.ok ? "badge-success" : "badge-danger"} connection-latency-badge`}
                      title={props.testStates?.[item.name]?.message}
                    >
                      {props.testStates?.[item.name]?.ok ? `⚡ ${props.testStates?.[item.name]?.latency_ms ?? 0}ms` : "Error"}
                    </span>
                  </Show>
                </div>
                <span class="hint provider-connection-detail" title={`${item.label}${item.model ? ` · ${item.model}` : ""}`}>{item.label}<Show when={item.model}> · {item.model}</Show></span>
              </div>
              <div class="provider-connection-actions">
                <Show when={props.onTest}>
                  <button
                    class="btn btn-icon btn-sm"
                    type="button"
                    aria-label={`Test connection for ${item.name}`}
                    title={props.testStates?.[item.name]?.message || "Test connection"}
                    disabled={props.busy || props.testStates?.[item.name]?.loading}
                    onClick={() => props.onTest!(item.name)}
                  >
                    <Show
                      when={props.testStates?.[item.name]?.loading}
                      fallback={<Sparkles size={14} aria-hidden="true" />}
                    >
                      <RefreshCw size={14} class="spin" aria-hidden="true" />
                    </Show>
                  </button>
                </Show>
                <button class="btn btn-icon btn-sm" type="button" aria-label={`Edit ${item.name}`} title="Edit configuration" disabled={props.busy} onClick={() => props.onEdit(item.name)}><Edit3 size={14} aria-hidden="true" /></button>
                <button class="btn btn-icon btn-sm" type="button" aria-label={`Set ${item.name} as default`} aria-pressed={item.isDefault} disabled={props.busy || !item.canSetDefault} title={item.defaultReason || (item.isDefault ? "Default configuration" : "Set as default")} onClick={() => props.onSetDefault(item.name)}><Star size={14} fill={item.isDefault ? "currentColor" : "none"} aria-hidden="true" /></button>
                <button class="btn btn-icon btn-sm btn-danger" type="button" aria-label={`Delete ${item.name}`} disabled={props.busy || !item.canDelete} title={item.deleteReason || "Delete configuration"} onClick={() => props.onDelete(item.name)}><Trash2 size={14} aria-hidden="true" /></button>
              </div>
            </div>
          )}</For>
        </Show>
      </div>
    </section>
  );
}

function ProviderPickerCard(props: { id: string; label: string; description?: string; logoUrl?: string; count: number; onClick: () => void }) {
  const [logoError, setLogoError] = createSignal(false);
  return (
    <button class="provider-picker-card" type="button" data-provider-id={props.id} onClick={props.onClick}>
      <div class="provider-picker-logo-container">
        <Show when={props.logoUrl && !logoError()} fallback={<div class="provider-picker-logo provider-picker-logo-fallback">{props.label.charAt(0).toUpperCase()}</div>}>
          <img class="provider-picker-logo" src={props.logoUrl} alt="" onError={() => setLogoError(true)} />
        </Show>
      </div>
      <div class="provider-connection-info"><strong>{props.label}</strong><Show when={props.description}><span class="hint">{props.description}</span></Show><span class="hint">{props.count} saved · Add configuration</span></div>
      <Plus size={16} />
    </button>
  );
}

export function ProviderPicker(props: {
  items: Array<{ id: string; label: string; description?: string; logoUrl?: string; count: number }>;
  onSelect: (id: string) => void;
}) {
  const [visibleCount, setVisibleCount] = createSignal(PROVIDER_PAGE_SIZE);
  const itemIds = createMemo(() => JSON.stringify(props.items.map((item) => item.id)));
  const visibleItems = createMemo(() => props.items.slice(0, visibleCount()));

  createEffect(() => {
    itemIds();
    setVisibleCount(PROVIDER_PAGE_SIZE);
  });

  return (
    <section data-provider-picker>
      <h3>Add a provider</h3>
      <div class="provider-picker-grid">
        <For each={visibleItems()}>{(item) => <ProviderPickerCard {...item} onClick={() => props.onSelect(item.id)} />}</For>
      </div>
      <Show when={visibleCount() < props.items.length}>
        <div class="row-wrap-end provider-picker-actions">
          <button class="btn" type="button" onClick={() => setVisibleCount(props.items.length)}>
            Load more
          </button>
        </div>
      </Show>
      <Show when={!props.items.length}><p class="hint">No providers match your search.</p></Show>
    </section>
  );
}

export function ProviderFormPanel(props: {
  title: string;
  busy: boolean;
  onBack: () => void;
  onSubmit: (event: SubmitEvent) => void;
  children: JSX.Element;
  canSubmit?: boolean;
  actions?: JSX.Element;
}) {
  return (
    <form class="panel panel-body stack provider-configuration-form" onSubmit={props.onSubmit}>
      <h3>{props.title}</h3>
      <fieldset disabled={props.busy} class="provider-form-fields">{props.children}</fieldset>
      <div class="row-wrap items-center">
        <button class="btn btn-primary" type="submit" disabled={props.busy || props.canSubmit === false}>{props.busy ? "Saving..." : "Save configuration"}</button>
        {props.actions}
        <button class="btn" type="button" disabled={props.busy} onClick={props.onBack}><ArrowLeft size={14} />Cancel</button>
      </div>
    </form>
  );
}
