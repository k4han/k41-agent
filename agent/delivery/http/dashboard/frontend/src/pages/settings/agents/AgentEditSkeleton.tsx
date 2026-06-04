import { JSX, Show } from "solid-js";

export function AgentEditSkeleton(props: {
  icon?: JSX.Element;
  title?: string;
  description?: string;
  actions?: JSX.Element;
  loading?: boolean;
}) {
  return (
    <div class="stack" style="gap: 16px;">
      <div class="tab-bar" aria-hidden="true">
        <button class="btn btn-sm" type="button" disabled>
          General Config
        </button>
        <button class="btn btn-sm" type="button" disabled>
          Capabilities & Tools
        </button>
        <button class="btn btn-sm" type="button" disabled>
          System Prompt
        </button>
      </div>
      <section class="panel" style="min-height: 360px;">
        <div class="panel-body">
          <Show
            when={props.loading}
            fallback={
              <div class="stack" style="align-items: center; text-align: center; padding: 48px 24px;">
                <Show when={props.icon}>
                  <div class="badge" style="font-size: 18px; padding: 12px 16px;">
                    {props.icon}
                  </div>
                </Show>
                <Show when={props.title}>
                  <div style="font-size: 18px; font-weight: 650;">{props.title}</div>
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
            <div class="stack" style="gap: 12px; padding: 8px 0;">
              <div class="hint" aria-live="polite">
                Loading agent...
              </div>
              <div class="grid-2">
                <div class="field">
                  <div class="skeleton-line" style="width: 60%; height: 12px;" />
                  <div class="skeleton-line" style="width: 100%; height: 32px; margin-top: 6px;" />
                </div>
                <div class="field">
                  <div class="skeleton-line" style="width: 60%; height: 12px;" />
                  <div class="skeleton-line" style="width: 100%; height: 32px; margin-top: 6px;" />
                </div>
              </div>
              <div class="field">
                <div class="skeleton-line" style="width: 50%; height: 12px;" />
                <div class="skeleton-line" style="width: 100%; height: 32px; margin-top: 6px;" />
              </div>
              <div class="grid-2">
                <div class="field">
                  <div class="skeleton-line" style="width: 50%; height: 12px;" />
                  <div class="skeleton-line" style="width: 100%; height: 32px; margin-top: 6px;" />
                </div>
                <div class="field">
                  <div class="skeleton-line" style="width: 50%; height: 12px;" />
                  <div class="skeleton-line" style="width: 100%; height: 32px; margin-top: 6px;" />
                </div>
              </div>
            </div>
          </Show>
        </div>
      </section>
    </div>
  );
}
