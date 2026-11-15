import { createEffect, createSignal, For, Show, type JSX } from "solid-js";
import { ArrowLeft } from "lucide-solid";

export function ProviderLogo(props: { label: string; url?: string }) {
  const [failed, setFailed] = createSignal(false);
  createEffect(() => { props.url; setFailed(false); });
  return <span class="provider-picker-logo-container"><Show when={props.url && !failed()} fallback={<span class="provider-picker-logo provider-picker-logo-fallback">{props.label.charAt(0).toUpperCase()}</span>}><img class="provider-picker-logo" src={props.url} alt="" onError={() => setFailed(true)} /></Show></span>;
}

export function ProviderPagination(props: { page: number; total: number; size: number; onChange: (page: number) => void }) {
  const pages = () => Math.max(1, Math.ceil(props.total / props.size));
  return <nav class="provider-pagination" aria-label="Pagination"><span>{props.total ? (props.page - 1) * props.size + 1 : 0}–{Math.min(props.page * props.size, props.total)} of {props.total}</span><div class="row-wrap items-center"><button class="btn btn-sm" type="button" disabled={props.page <= 1} onClick={() => props.onChange(props.page - 1)}>Previous</button><span>{props.page} / {pages()}</span><button class="btn btn-sm" type="button" disabled={props.page >= pages()} onClick={() => props.onChange(props.page + 1)}>Next</button></div></nav>;
}

export function ProviderEditorTabs<T extends string>(props: {
  id: string;
  items: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return <div class="provider-editor-tabs" role="tablist" aria-label="Connection configuration"><For each={props.items}>{(item, index) => <button
    id={`${props.id}-tab-${item.value}`} class="provider-editor-tab" type="button" role="tab"
    aria-selected={props.value === item.value} aria-controls={`${props.id}-panel-${item.value}`} tabindex={props.value === item.value ? 0 : -1}
    onClick={() => props.onChange(item.value)} onKeyDown={(event) => {
      const count = props.items.length;
      const next = event.key === "ArrowRight" ? (index() + 1) % count : event.key === "ArrowLeft" ? (index() + count - 1) % count : event.key === "Home" ? 0 : event.key === "End" ? count - 1 : -1;
      if (next < 0) return;
      event.preventDefault();
      props.onChange(props.items[next].value);
      document.getElementById(`${props.id}-tab-${props.items[next].value}`)?.focus();
    }}>{item.label}</button>}</For></div>;
}

export type ConnectionTestState = {
  loading?: boolean;
  ok?: boolean;
  latency_ms?: number;
  message?: string;
};

export function ProviderFormPanel(props: {
  title: string;
  busy: boolean;
  onBack: () => void;
  onSubmit: (event: SubmitEvent) => void;
  children: JSX.Element;
  canSubmit?: boolean;
  actions?: JSX.Element;
  editing?: boolean;
  dirty?: boolean;
  onDiscard?: () => void;
  hideBack?: boolean;
}) {
  return (
    <form class="panel panel-body stack provider-configuration-form" onSubmit={props.onSubmit}>
      <div class="provider-form-heading"><h3>{props.title}</h3><Show when={props.editing && !props.hideBack}><button class="btn btn-sm" type="button" disabled={props.busy} onClick={props.onBack}><ArrowLeft size={14} />All connections</button></Show></div>
      <fieldset disabled={props.busy} class="provider-form-fields">{props.children}</fieldset>
      <Show when={props.actions}><div class="provider-test-actions">{props.actions}</div></Show>
      <Show when={!props.editing || props.dirty}>
      <div class={`row-wrap items-center ${props.editing ? "provider-save-bar" : ""}`}>
        <Show when={props.editing}><span class="hint">Unsaved changes</span><button class="btn" type="button" disabled={props.busy} onClick={props.onDiscard}>Discard draft</button></Show>
        <button class="btn btn-primary" type="submit" disabled={props.busy || props.canSubmit === false}>{props.busy ? "Saving..." : "Save configuration"}</button>
        <Show when={!props.editing}><button class="btn" type="button" disabled={props.busy} onClick={props.onBack}><ArrowLeft size={14} />Cancel</button></Show>
      </div>
      </Show>
    </form>
  );
}
