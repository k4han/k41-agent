import { createSignal } from "solid-js";
import { apiFetch } from "./api";
import type { CatalogResponse, ChannelCatalogItem, BackendCatalogItem, ProviderTypeOption, SelectOption } from "@/types";

const [catalog, setCatalog] = createSignal<CatalogResponse | null>(null);
const [loading, setLoading] = createSignal(false);
const [error, setError] = createSignal<string | null>(null);

let fetchPromise: Promise<CatalogResponse> | null = null;

export async function fetchCatalog(): Promise<CatalogResponse> {
  if (catalog()) {
    return catalog()!;
  }
  if (fetchPromise) {
    return fetchPromise;
  }
  setLoading(true);
  setError(null);
  fetchPromise = apiFetch<CatalogResponse>("/dashboard-api/catalog")
    .then((data) => {
      setCatalog(data);
      setLoading(false);
      fetchPromise = null;
      return data;
    })
    .catch((err) => {
      const message = err instanceof Error ? err.message : "Failed to load catalog";
      setError(message);
      setLoading(false);
      fetchPromise = null;
      throw err;
    });
  return fetchPromise;
}

export async function refreshCatalog(): Promise<CatalogResponse> {
  fetchPromise = null;
  return fetchCatalog();
}

export function getCatalog(): CatalogResponse | null {
  return catalog();
}

export function getProviderTypes(): ProviderTypeOption[] {
  return catalog()?.provider_types ?? [];
}

export function getChannels(): ChannelCatalogItem[] {
  return catalog()?.channels ?? [];
}

export function getBackends(): BackendCatalogItem[] {
  return catalog()?.backends ?? [];
}

export function getEnabledBackends(): BackendCatalogItem[] {
  return getBackends().filter((backend) => backend.enabled === true);
}

export function isBackendEnabled(name: string): boolean {
  if (name === "local") {
    return true;
  }
  return getBackends().find((backend) => backend.name === name)?.enabled === true;
}

export function getTriggerTypes(): SelectOption[] {
  return catalog()?.trigger_types ?? [];
}

export function getChannelStatuses(): SelectOption[] {
  return catalog()?.channel_statuses ?? [];
}

export function getPlatforms(): SelectOption[] {
  return catalog()?.platforms ?? [];
}

export function getMcpTransports(): SelectOption[] {
  return catalog()?.mcp_transports ?? [];
}

export function getPromptVariableNamePattern(): string {
  return catalog()?.prompt_variable_name_pattern ?? "^[A-Za-z][A-Za-z0-9_-]{0,63}$";
}

export function getSystemVariableNames(): string[] {
  return catalog()?.system_variable_names ?? [];
}

export function isLoading(): boolean {
  return loading();
}

export function getBackendDisplayName(name: string): string {
  const backend = getBackends().find((b) => b.name === name);
  return backend?.title ?? name;
}

export function getError(): string | null {
  return error();
}
