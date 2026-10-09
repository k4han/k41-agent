import { createSignal, getOwner, onCleanup } from "solid-js";

function readDarkMode(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  return document.documentElement.classList.contains("dark");
}

const [dark, setDark] = createSignal(readDarkMode());
let observer: MutationObserver | null = null;
let consumers = 0;

export function createDarkMode(): () => boolean {
  setDark(readDarkMode());
  if (!getOwner()) return dark;
  consumers += 1;
  if (!observer && typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    observer = new MutationObserver(() => {
      setDark(readDarkMode());
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }
  onCleanup(() => {
    consumers -= 1;
    if (consumers === 0) {
      observer?.disconnect();
      observer = null;
    }
  });
  return dark;
}

export const getSharedDarkMode = createDarkMode;
