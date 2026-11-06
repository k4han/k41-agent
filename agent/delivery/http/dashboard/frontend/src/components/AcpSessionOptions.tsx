import { Brain, Sparkles } from "lucide-solid";
import { For } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import type { AcpConfigOption, AcpOptionValues } from "@/lib/acp";

export function AcpSessionOptions(props: {
  options: AcpConfigOption[];
  values: AcpOptionValues;
  onChange: (id: string, value: string) => void;
  disabled?: boolean;
}) {
  return (
    <For each={props.options}>
      {(option) => {
        const value = () => props.values[option.id] ?? "";
        const label = () => option.options.find((item) => item.value === value())?.name || value();
        return (
          <>
            <span class="agent-model-divider" aria-hidden="true" />
            <SelectControl
              class={`acp-session-option ${option.category === "model" ? "acp-session-option--model" : ""}`}
              value={value()}
              options={option.options.map((item) => ({ value: item.value, label: item.name }))}
              icon={option.category === "model" ? <Sparkles size={14} /> : <Brain size={14} />}
              disabled={props.disabled}
              ariaLabel={option.name}
              title={`${option.name}: ${label()}`}
              onChange={(next) => props.onChange(option.id, next)}
            />
          </>
        );
      }}
    </For>
  );
}
