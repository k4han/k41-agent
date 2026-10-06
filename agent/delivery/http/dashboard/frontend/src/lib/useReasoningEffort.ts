import { createEffect, createMemo, createSignal, on, type Accessor } from "solid-js";
import type { ModelOption } from "@/types";

export function useReasoningEffort(modelKey: Accessor<string>, modelOption: Accessor<ModelOption | undefined>) {
  const [override, setOverride] = createSignal<{ key: string; effort: string }>();
  createEffect(on(modelKey, () => setOverride(undefined), { defer: true }));
  const levels = createMemo(() => modelOption()?.reasoning_effort_levels || []);
  const effort = createMemo(() => {
    const selected = override();
    if (selected && selected.key === modelKey() && (selected.effort === "" || levels().includes(selected.effort))) {
      return selected.effort;
    }
    const defaultEffort = modelOption()?.reasoning_effort_default;
    return defaultEffort && levels().includes(defaultEffort) ? defaultEffort : "";
  });
  const setEffort = (value: string) => {
    if (value === "" || levels().includes(value)) setOverride({ key: modelKey(), effort: value });
  };
  const requestEffort = () => levels().includes(effort()) ? effort() : undefined;
  return { levels, effort, setEffort, requestEffort };
}
