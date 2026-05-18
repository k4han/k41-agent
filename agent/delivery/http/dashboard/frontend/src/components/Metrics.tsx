import { For, JSX, type JSXElement } from "solid-js";

export function MetricCard(props: { value: JSXElement; label: string }) {
  return (
    <div class="panel metric">
      <div class="metric-value">{props.value}</div>
      <div class="metric-label">{props.label}</div>
    </div>
  );
}

export function MetricsRow(props: { children: JSXElement }) {
  return <div class="grid-3">{props.children}</div>;
}

export function MetricGrid(props: {
  items: { value: JSXElement; label: string }[];
}) {
  return (
    <MetricsRow>
      <For each={props.items}>
        {(item) => <MetricCard value={item.value} label={item.label} />}
      </For>
    </MetricsRow>
  );
}
