import { JSX, Show } from "solid-js";
import { AgentEditNavigation } from "./AgentEditNavigation";

export function AgentEditSkeleton(props: {
  icon?: JSX.Element;
  title?: string;
  description?: string;
  actions?: JSX.Element;
  loading?: boolean;
}) {
  return (
    <div class="agent-editor">
      <AgentEditNavigation disabled />
      <section class="agent-editor-panel">
        <Show
          when={props.loading}
          fallback={
            <div class="agent-editor-empty">
              <Show when={props.icon}>
                <div
                  class="badge"
                  style="font-size: 1.125rem; padding: 12px 16px;"
                >
                  {props.icon}
                </div>
              </Show>
              <Show when={props.title}>
                <div style="font-size: 1.125rem; font-weight: 650;">
                  {props.title}
                </div>
              </Show>
              <Show when={props.description}>
                <p class="hint">{props.description}</p>
              </Show>
              <Show when={props.actions}>
                <div class="row-wrap" style="justify-content: center;">
                  {props.actions}
                </div>
              </Show>
            </div>
          }
        >
          <div class="stack agent-editor-loading" style="gap: 12px;">
            <div class="hint" aria-live="polite">
              Loading agent...
            </div>
            <div class="grid-2">
              <div class="field">
                <div class="skeleton-line" style="width: 60%; height: 12px;" />
                <div
                  class="skeleton-line"
                  style="width: 100%; height: 32px; margin-top: 6px;"
                />
              </div>
              <div class="field">
                <div class="skeleton-line" style="width: 60%; height: 12px;" />
                <div
                  class="skeleton-line"
                  style="width: 100%; height: 32px; margin-top: 6px;"
                />
              </div>
            </div>
            <div class="field">
              <div class="skeleton-line" style="width: 50%; height: 12px;" />
              <div
                class="skeleton-line"
                style="width: 100%; height: 32px; margin-top: 6px;"
              />
            </div>
            <div class="grid-2">
              <div class="field">
                <div class="skeleton-line" style="width: 50%; height: 12px;" />
                <div
                  class="skeleton-line"
                  style="width: 100%; height: 32px; margin-top: 6px;"
                />
              </div>
              <div class="field">
                <div class="skeleton-line" style="width: 50%; height: 12px;" />
                <div
                  class="skeleton-line"
                  style="width: 100%; height: 32px; margin-top: 6px;"
                />
              </div>
            </div>
          </div>
        </Show>
      </section>
    </div>
  );
}
