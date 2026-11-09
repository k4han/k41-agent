import { createSignal } from "solid-js";

import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { STORAGE_KEYS } from "@/lib/uiConstants";

/** Model reasoning is shown unless the viewer turned it off in this browser. */
function readShowThinking(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEYS.SHOW_THINKING) !== "false";
  } catch {
    return true;
  }
}

const [showThinking, setShowThinkingSignal] = createSignal(
  typeof window === "undefined" ? true : readShowThinking(),
);

if (typeof window !== "undefined") {
  const refresh = () => setShowThinkingSignal(readShowThinking());
  window.addEventListener(CUSTOM_DOM_EVENTS.SHOW_THINKING_CHANGED, refresh);
  // Keep other tabs of the dashboard in sync with the latest choice.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEYS.SHOW_THINKING || event.key === null) refresh();
  });
}

function setShowThinking(next: boolean): void {
  setShowThinkingSignal(next);
  try {
    window.localStorage.setItem(STORAGE_KEYS.SHOW_THINKING, String(next));
  } catch {
    return;
  }
  window.dispatchEvent(new Event(CUSTOM_DOM_EVENTS.SHOW_THINKING_CHANGED));
}

/** Shared per-browser preference controlling whether thinking blocks render. */
export function useShowThinking() {
  return {
    showThinking,
    setShowThinking,
    toggleShowThinking: () => setShowThinking(!showThinking()),
  };
}
