import { useBeforeLeave } from "@solidjs/router";
import { onCleanup, onMount } from "solid-js";

export function useUnsavedChanges(isDirty: () => boolean, discard?: () => void) {
  useBeforeLeave((event) => {
    if (!isDirty() || event.defaultPrevented) return;
    if (window.confirm("Discard unsaved changes and leave this form?")) {
      discard?.();
    } else {
      event.preventDefault();
    }
  });
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (isDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  onMount(() => window.addEventListener("beforeunload", beforeUnload));
  onCleanup(() => window.removeEventListener("beforeunload", beforeUnload));
}
