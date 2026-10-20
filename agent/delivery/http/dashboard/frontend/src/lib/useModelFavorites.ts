import { createSignal, onCleanup, onMount } from "solid-js";

const favoritesStorageKey = "k41.dashboard.modelFavorites";
const favoritesChangedEvent = "k41:model-favorites-changed";
const keySeparator = "\u001f";

type ModelFavorite = { provider: string; model: string };

export function favoriteKey(provider: string, model: string): string {
  return `${provider}${keySeparator}${model}`;
}

export function parseFavoriteKey(key: string): ModelFavorite | null {
  const [provider, model] = key.split(keySeparator);
  return provider && model ? { provider, model } : null;
}

function readFavorites(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(favoritesStorageKey) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is ModelFavorite =>
        item && typeof item.provider === "string" && !!item.provider &&
        typeof item.model === "string" && !!item.model,
      )
      .map((item) => favoriteKey(item.provider, item.model));
  } catch {
    return [];
  }
}

function writeFavorites(keys: string[]): void {
  const favorites = keys.map(parseFavoriteKey).filter((item) => item !== null);
  try {
    window.localStorage.setItem(favoritesStorageKey, JSON.stringify(favorites));
  } catch {
    return;
  }
  window.dispatchEvent(new Event(favoritesChangedEvent));
}

export function useModelFavorites() {
  const [favorites, setFavorites] = createSignal<string[]>([]);

  const isFavorite = (choice: ModelFavorite) =>
    !!choice.model && favorites().includes(favoriteKey(choice.provider, choice.model));

  const toggleFavorite = (choice: ModelFavorite) => {
    if (!choice.provider || !choice.model) return;
    const key = favoriteKey(choice.provider, choice.model);
    const current = favorites();
    const next = current.includes(key)
      ? current.filter((item) => item !== key)
      : [...current, key].sort();
    setFavorites(next);
    writeFavorites(next);
  };

  onMount(() => {
    const refresh = () => setFavorites(readFavorites());
    const handleStorage = (event: StorageEvent) => {
      if (event.key === favoritesStorageKey || event.key === null) refresh();
    };
    refresh();
    window.addEventListener(favoritesChangedEvent, refresh);
    window.addEventListener("storage", handleStorage);
    onCleanup(() => {
      window.removeEventListener(favoritesChangedEvent, refresh);
      window.removeEventListener("storage", handleStorage);
    });
  });

  return { favorites, isFavorite, toggleFavorite };
}
