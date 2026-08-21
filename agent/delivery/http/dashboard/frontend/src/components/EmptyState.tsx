import { Show, createMemo, type JSX } from "solid-js";
import { A } from "@solidjs/router";

export function EmptyState(props: {
  icon?: JSX.Element;
  message: string;
  hint?: string;
  href?: string;
  action?: string;
}) {
  const cta = createMemo(() =>
    props.href && props.action
      ? { href: props.href, label: props.action }
      : undefined,
  );

  return (
    <div class="empty-state">
      <Show when={props.icon}>
        <div class="empty-state-icon" aria-hidden="true">{props.icon}</div>
      </Show>
      <div class="empty-state-message">{props.message}</div>
      <Show when={props.hint}>
        <div class="empty-state-hint">{props.hint}</div>
      </Show>
      <Show when={cta()}>
        {(ctaValue) => (
          <A class="btn btn-sm empty-state-action" href={ctaValue().href}>
            {ctaValue().label}
          </A>
        )}
      </Show>
    </div>
  );
}
