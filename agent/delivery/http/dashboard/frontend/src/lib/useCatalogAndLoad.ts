import { onMount } from "solid-js";

import { useToast } from "@/components/Toast";
import { fetchCatalog } from "@/lib/catalogStore";

/**
 * Ensures the global catalog is loaded before ``loader`` runs.
 *
 * Centralizes the ``await fetchCatalog(); await load();`` pattern that several
 * settings pages previously duplicated. On failure a toast is shown so users
 * are not silently left with empty dropdowns; ``loader`` still runs so the
 * page can render whatever data it managed to fetch.
 */
export function useCatalogAndLoad(loader: () => Promise<void>): void {
  const { showToast } = useToast();
  onMount(async () => {
    try {
      await fetchCatalog();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to load catalog options",
        "error",
      );
    }
    await loader();
  });
}
