import { useBeforeLeave } from "@solidjs/router";
import { onCleanup, onMount } from "solid-js";

export interface UnsavedChangesOptions {
  message?: string;
}

export function useUnsavedChanges(
  isDirty: () => boolean,
  discard?: () => void,
  options?: UnsavedChangesOptions,
) {
  const message = options?.message ?? "You have unsaved changes. Discard them and leave this page?";

  useBeforeLeave((event) => {
    if (!isDirty() || event.defaultPrevented) return;
    if (window.confirm(message)) {
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
