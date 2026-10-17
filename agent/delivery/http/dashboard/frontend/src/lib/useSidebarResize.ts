import { createEffect, createMemo, createSignal, onCleanup, onMount, type Accessor } from "solid-js";

import { STORAGE_KEYS } from "@/lib/uiConstants";

const DEFAULT_WIDTH = 290;
const MIN_WIDTH = 240;

export function useSidebarResize(enabled: Accessor<boolean>) {
  const savedWidth = Number(window.localStorage.getItem(STORAGE_KEYS.SIDEBAR_WIDTH));
  const [preferredWidth, setPreferredWidth] = createSignal(
    Number.isFinite(savedWidth) && savedWidth > 0 ? savedWidth : DEFAULT_WIDTH,
  );
  const [viewportWidth, setViewportWidth] = createSignal(window.innerWidth);
  const [resizing, setResizing] = createSignal(false);
  const maxWidth = createMemo(() => Math.max(MIN_WIDTH, Math.floor(viewportWidth() / 4)));
  const clampWidth = (value: number) => Math.max(MIN_WIDTH, Math.min(maxWidth(), Math.round(value)));
  const width = createMemo(() => clampWidth(preferredWidth()));
  let stopResize: (() => void) | undefined;

  const applyWidth = (value: number) => {
    const next = clampWidth(value);
    setPreferredWidth(next);
    window.localStorage.setItem(STORAGE_KEYS.SIDEBAR_WIDTH, String(next));
  };

  const startResize = (event: PointerEvent) => {
    if (!enabled() || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    stopResize?.();
    const handle = event.currentTarget as HTMLElement;
    const startX = event.clientX;
    const startWidth = width();
    handle.setPointerCapture(event.pointerId);
    setResizing(true);
    document.body.classList.add("sidebar-resizing");

    const move = (next: PointerEvent) => {
      if (next.pointerId === event.pointerId) applyWidth(startWidth + next.clientX - startX);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("blur", stop);
      handle.removeEventListener("lostpointercapture", stop);
      stopResize = undefined;
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      setResizing(false);
      document.body.classList.remove("sidebar-resizing");
    };
    const finish = (next: PointerEvent) => {
      if (next.pointerId === event.pointerId) stop();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    window.addEventListener("blur", stop);
    handle.addEventListener("lostpointercapture", stop);
    stopResize = stop;
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (!enabled()) return;
    const step = event.shiftKey ? 32 : 16;
    const next = {
      ArrowLeft: width() - step,
      ArrowRight: width() + step,
      Home: MIN_WIDTH,
      End: maxWidth(),
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    applyWidth(next);
  };

  createEffect(() => {
    if (!enabled()) stopResize?.();
  });

  onMount(() => {
    const handleResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", handleResize);
    onCleanup(() => window.removeEventListener("resize", handleResize));
  });
  onCleanup(() => stopResize?.());

  return { width, minWidth: MIN_WIDTH, maxWidth, resizing, startResize, handleKeyDown, reset: () => applyWidth(DEFAULT_WIDTH) };
}
