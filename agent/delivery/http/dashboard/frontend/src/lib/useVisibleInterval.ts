import { onCleanup, onMount } from "solid-js";

/** Pause periodic UI work while hidden and refresh immediately on return. */
export function useVisibleInterval(callback: (immediate: boolean) => void, delayMs: number): void {
  onMount(() => {
    let timer: number | undefined;
    const tick = (immediate = false) => {
      if (document.visibilityState !== "hidden") callback(immediate);
    };
    const update = () => {
      window.clearInterval(timer);
      timer = undefined;
      if (document.visibilityState === "hidden") return;
      tick(true);
      timer = window.setInterval(tick, delayMs);
    };
    update();
    document.addEventListener("visibilitychange", update);
    onCleanup(() => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    });
  });
}
