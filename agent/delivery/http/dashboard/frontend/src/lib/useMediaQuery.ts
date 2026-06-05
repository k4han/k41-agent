import { createSignal, onCleanup } from "solid-js";

export function createMediaQuery(query: string): () => boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => false;
  }

  const mql = window.matchMedia(query);
  const [matches, setMatches] = createSignal(mql.matches);

  const handler = (event: MediaQueryListEvent) => {
    setMatches(event.matches);
  };

  mql.addEventListener("change", handler);
  onCleanup(() => mql.removeEventListener("change", handler));

  return matches;
}
