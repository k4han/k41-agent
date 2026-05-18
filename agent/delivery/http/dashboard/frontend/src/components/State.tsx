import { JSX, Show } from "solid-js";

export function LoadingPanel() {
  return (
    <div class="panel">
      <div class="empty">Loading...</div>
    </div>
  );
}

export function ErrorPanel(props: { message: string; onRetry?: () => void }) {
  return (
    <div class="panel">
      <div class="panel-body stack">
        <div class="badge badge-danger">Error</div>
        <div>{props.message}</div>
        <Show when={props.onRetry}>
          <button class="btn" type="button" onClick={props.onRetry}>
            Retry
          </button>
        </Show>
      </div>
    </div>
  );
}

export function DataGate<T>(props: {
  data: T | undefined;
  error: string;
  onRetry?: () => void;
  children: (data: T) => JSX.Element;
}) {
  return (
    <Show
      when={props.data}
      keyed
      fallback={
        props.error ? (
          <ErrorPanel message={props.error} onRetry={props.onRetry} />
        ) : (
          <LoadingPanel />
        )
      }
    >
      {(data) => props.children(data)}
    </Show>
  );
}
