import { createSignal, onCleanup } from "solid-js";

function readDarkMode(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  return document.documentElement.classList.contains("dark");
}

export function createDarkMode(): () => boolean {
  const [dark, setDark] = createSignal(readDarkMode());

  if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    const observer = new MutationObserver(() => {
      setDark(readDarkMode());
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    onCleanup(() => observer.disconnect());
  }

  return dark;
}
