import { createEffect, createMemo, createSignal, onCleanup } from "solid-js";

import { apiFetch } from "@/lib/api";
import { filterSkillOptions, getSkillQuery, insertSkillSuggestion, type SkillOption } from "@/lib/skillSuggestions";
import type { WorkspaceRef } from "@/types";

export function useSkillSuggestions(props: {
  prompt: string;
  workspace?: WorkspaceRef | null;
  inputDisabled: boolean;
  currentThreadId: string;
  onPromptChange: (value: string) => void;
}, input: () => HTMLTextAreaElement | undefined) {
  const [focused, setFocused] = createSignal(false);
  const [selection, setSelection] = createSignal({ start: 0, end: 0 });
  const [dismissed, setDismissed] = createSignal(false);
  const [options, setOptions] = createSignal<SkillOption[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [activeIndex, setActiveIndex] = createSignal(0);
  const workspaceKey = createMemo(() => JSON.stringify(props.workspace || null));
  const query = createMemo(() => getSkillQuery(props.prompt, selection().start, selection().end));
  const open = createMemo(() => focused() && !props.inputDisabled && !dismissed() && query() !== null);
  const matches = createMemo(() => {
    const currentQuery = query();
    return currentQuery ? filterSkillOptions(options(), currentQuery) : [];
  });

  createEffect(() => {
    props.currentThreadId;
    workspaceKey();
    setDismissed(true);
  });

  createEffect(() => {
    if (!open()) return;
    const workspace = JSON.parse(workspaceKey()) as WorkspaceRef | null;
    const controller = new AbortController();
    setOptions([]);
    setError("");
    setLoading(true);
    const params = workspace ? `?workspace=${encodeURIComponent(JSON.stringify(workspace))}` : "";
    void apiFetch<{ packages: SkillOption[] }>(`/dashboard-api/skill-packages${params}`, { signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) setOptions(response.packages);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed to load skills");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    onCleanup(() => controller.abort());
  });

  createEffect(() => {
    query();
    options();
    setActiveIndex(0);
  });

  createEffect(() => {
    if (!open()) return;
    const index = activeIndex();
    document.getElementById(`chat-skill-option-${index}`)?.scrollIntoView({ block: "nearest" });
  });

  const syncSelection = (resetDismissal = false) => {
    const textarea = input();
    if (!textarea) return;
    setFocused(document.activeElement === textarea);
    setSelection((current) => current.start === textarea.selectionStart && current.end === textarea.selectionEnd
      ? current
      : { start: textarea.selectionStart, end: textarea.selectionEnd });
    if (resetDismissal) setDismissed(false);
  };

  const select = (item: SkillOption) => {
    const currentQuery = query();
    if (!currentQuery || !open()) return;
    const result = insertSkillSuggestion(props.prompt, currentQuery, item.name);
    setDismissed(true);
    props.onPromptChange(result.prompt);
    input()?.focus();
    input()?.setSelectionRange(result.caret, result.caret);
    syncSelection();
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (!open() || event.isComposing || event.keyCode === 229) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      setDismissed(true);
      return true;
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && matches().length) {
      event.preventDefault();
      setActiveIndex((index) => (index + (event.key === "ArrowDown" ? 1 : -1) + matches().length) % matches().length);
      return true;
    }
    if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const item = matches()[activeIndex()];
      if (item) {
        event.preventDefault();
        select(item);
        return true;
      }
      if (event.key === "Enter" && loading()) {
        event.preventDefault();
        return true;
      }
    }
    return false;
  };

  return { open, matches, loading, error, activeIndex, setActiveIndex, select, syncSelection, handleKeyDown, setFocused };
}
