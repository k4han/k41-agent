import { createSignal, onCleanup, onMount } from "solid-js";

import {
  WORKSPACE_EXPLORER_DEFAULT_WIDTH,
  WORKSPACE_EXPLORER_MAX_WIDTH,
  WORKSPACE_EXPLORER_MIN_WIDTH,
  WORKSPACE_EXPLORER_OPEN_KEY,
  WORKSPACE_EXPLORER_WIDTH_KEY,
} from "@/lib/chatTypes";

export function useWorkspaceExplorer(getShellRef: () => HTMLDivElement | undefined) {
  const [open, setOpen] = createSignal(true);
  const [width, setWidth] = createSignal(WORKSPACE_EXPLORER_DEFAULT_WIDTH);
  const [resizing, setResizing] = createSignal(false);
  let stopResize: (() => void) | null = null;

  const clampWidth = (value: number, availableWidth = window.innerWidth) => {
    const viewportMax = Math.max(WORKSPACE_EXPLORER_MIN_WIDTH, availableWidth - 436);
    const maxWidth = Math.min(WORKSPACE_EXPLORER_MAX_WIDTH, viewportMax);
    return Math.min(maxWidth, Math.max(WORKSPACE_EXPLORER_MIN_WIDTH, Math.round(value)));
  };

  const setExplorerOpen = (next: boolean) => {
    setOpen(next);
    window.localStorage.setItem(WORKSPACE_EXPLORER_OPEN_KEY, next ? "open" : "closed");
  };

  const toggle = () => {
    setExplorerOpen(!open());
  };

  const applyWidth = (value: number, availableWidth?: number) => {
    const nextWidth = clampWidth(value, availableWidth);
    setWidth(nextWidth);
    window.localStorage.setItem(WORKSPACE_EXPLORER_WIDTH_KEY, String(nextWidth));
  };

  const endResize = () => {
    stopResize?.();
    stopResize = null;
    setResizing(false);
    document.body.classList.remove("workspace-resizing");
  };

  const startResize = (event: PointerEvent) => {
    const shellRef = getShellRef();
    if (!shellRef || !open()) {
      return;
    }
    event.preventDefault();
    setResizing(true);
    document.body.classList.add("workspace-resizing");

    const move = (moveEvent: PointerEvent) => {
      const shellRect = getShellRef()?.getBoundingClientRect();
      if (!shellRect) {
        return;
      }
      applyWidth(shellRect.right - moveEvent.clientX, shellRect.width);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      stopResize = null;
      setResizing(false);
      document.body.classList.remove("workspace-resizing");
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    stopResize = stop;
  };

  onMount(() => {
    const savedOpen = window.localStorage.getItem(WORKSPACE_EXPLORER_OPEN_KEY);
    setOpen(savedOpen !== "closed");

    const savedWidth = Number(window.localStorage.getItem(WORKSPACE_EXPLORER_WIDTH_KEY));
    if (Number.isFinite(savedWidth) && savedWidth > 0) {
      setWidth(clampWidth(savedWidth));
    }
  });

  onCleanup(() => {
    endResize();
  });

  return { open, width, resizing, toggle, startResize };
}
