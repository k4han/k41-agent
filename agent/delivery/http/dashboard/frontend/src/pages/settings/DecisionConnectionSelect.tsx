import { createEffect, For, Show } from "solid-js";
import type { DecisionProvidersPayload } from "@/types";

export function DecisionConnectionSelect(props: {
  providers: DecisionProvidersPayload["providers"];
  value: string;
  label: string;
  disabled?: boolean;
  placeholder: string;
  allowFallback?: boolean;
  configuredOnly?: boolean;
  onChange: (name: string) => void;
}) {
  let select: HTMLSelectElement | undefined;
  const selected = () => props.providers.find((provider) => provider.name.toLowerCase() === props.value.toLowerCase());
  // Apply the value after asynchronously loaded options have been reconciled.
  createEffect(() => {
    const value = selected()?.name || props.value;
    props.providers;
    queueMicrotask(() => { if (select?.isConnected) select.value = value; });
  });
  return (
    <select ref={select} class="input" aria-label={props.label} value={selected()?.name || props.value}
      disabled={props.disabled} onChange={(event) => {
        const name = event.currentTarget.value;
        event.currentTarget.value = selected()?.name || props.value;
        props.onChange(name);
      }}>
      <option value="" disabled={!props.allowFallback}>{props.placeholder}</option>
      <Show when={props.value && !selected()}><option value={props.value} disabled>{props.value} (unavailable)</option></Show>
      <For each={props.providers}>{(provider) => (
        <option value={provider.name} disabled={props.configuredOnly && !provider.configured}>
          {provider.name}{provider.fields.model ? ` / ${provider.fields.model}` : ""}{!provider.configured ? " (incomplete)" : ""}
        </option>
      )}</For>
    </select>
  );
}
