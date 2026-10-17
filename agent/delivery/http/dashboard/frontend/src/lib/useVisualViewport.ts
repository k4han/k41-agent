import { createEffect, createSignal, onCleanup } from "solid-js";

export function useVisualViewport(enabled: () => boolean) {
  const [bounds, setBounds] = createSignal<{ height: number; offsetTop: number }>();

  createEffect(() => {
    const viewport = window.visualViewport;
    if (!enabled() || !viewport) {
      setBounds(undefined);
      return;
    }

    let frame: number | undefined;
    const update = () => {
      frame = undefined;
      // Preserve normal page zoom instead of resizing the layout while pinching.
      if (Math.abs(viewport.scale - 1) > 0.01) return;
      setBounds({ height: viewport.height, offsetTop: viewport.offsetTop });
    };
    const scheduleUpdate = () => {
      if (frame === undefined) frame = window.requestAnimationFrame(update);
    };

    update();
    viewport.addEventListener("resize", scheduleUpdate);
    viewport.addEventListener("scroll", scheduleUpdate);
    window.addEventListener("resize", scheduleUpdate);
    onCleanup(() => {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      viewport.removeEventListener("resize", scheduleUpdate);
      viewport.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
    });
  });

  return bounds;
}
