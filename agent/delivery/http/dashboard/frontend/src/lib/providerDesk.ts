import type { DecisionProvidersPayload, SettingsPayload, WebConnectionsPayload } from "@/types";
import type { ProviderConnectionKind } from "./providerRoutes";

export const CONNECTION_KIND_LABELS: Record<ProviderConnectionKind, string> = {
  llm: "Models", decision: "Decision", web: "Search & Web",
};

export type DeskConnection = {
  id: string;
  kind: ProviderConnectionKind;
  name: string;
  label: string;
  type: string;
  catalogId: string;
  logoUrl?: string;
  capabilities: string[];
  model: string;
  models: string[];
  configured: boolean;
  enabled: boolean;
  isDefault: boolean;
  canSetDefault: boolean;
  canDelete: boolean;
  defaultReason?: string;
  deleteReason?: string;
};

export type DeskCatalogItem = {
  id: string;
  providerId: string;
  kind: ProviderConnectionKind;
  label: string;
  adapter: string;
  logoUrl?: string;
  modelCount?: number;
  count: number;
};

export type DeskSources = {
  llm?: SettingsPayload;
  decision?: DecisionProvidersPayload;
  web?: WebConnectionsPayload;
};

export function configuredModelIds(value: unknown): string[] {
  return [...new Set((Array.isArray(value) ? value : typeof value === "string" ? value.split(/[\n,]+/) : [])
    .filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean))];
}

export function buildDeskConnections(sources: DeskSources): DeskConnection[] {
  const catalog = Object.values(sources.llm?.providers_catalog || {});
  const llm: DeskConnection[] = (sources.llm?.provider_rows || []).map((row) => {
    const catalogId = row.catalog_id || row.name;
    const entry = catalog.find((item) => item.id.toLowerCase() === catalogId.toLowerCase());
    return {
      id: `llm:${row.name}`, kind: "llm", name: row.name, label: entry?.name || row.type_label || row.type,
      type: row.type, catalogId, logoUrl: entry?.logo_url, capabilities: ["Models"],
      model: String(row.fields.default_model?.info.value || ""), models: configuredModelIds(row.fields.models?.info.value),
      configured: row.ready, enabled: row.enabled, isDefault: row.is_default,
      canSetDefault: row.can_set_default, canDelete: row.can_delete,
      defaultReason: row.default_block_reason, deleteReason: row.delete_block_reason,
    };
  });
  const decision: DeskConnection[] = (sources.decision?.providers || []).map((row) => ({
    id: `decision:${row.name}`, kind: "decision", name: row.name,
    label: sources.decision?.services.find((item) => item.type === row.type)?.label || row.type,
    type: row.type, catalogId: row.type, capabilities: ["Decision"], model: String(row.fields.model || ""), models: [],
    configured: row.configured, enabled: true, isDefault: row.is_default,
    canSetDefault: row.configured && !row.is_default, canDelete: !row.is_default,
    deleteReason: row.is_default ? "Choose another default or clear it before deleting." : undefined,
  }));
  const web: DeskConnection[] = (sources.web?.connections || []).map((row) => ({
    id: `web:${row.name}`, kind: "web", name: row.name,
    label: sources.web?.services.find((item) => item.type === row.type)?.label || row.type,
    type: row.type, catalogId: row.type, capabilities: ["Search & Web", ...row.capabilities], model: "", models: [],
    configured: row.configured, enabled: true, isDefault: row.is_default,
    canSetDefault: row.configured && !row.is_default, canDelete: true,
  }));
  return [...llm, ...decision, ...web].sort((a, b) => a.name.localeCompare(b.name) || a.kind.localeCompare(b.kind));
}

export function buildDeskCatalog(sources: DeskSources, connections: DeskConnection[]): DeskCatalogItem[] {
  const items: DeskCatalogItem[] = Object.values(sources.llm?.providers_catalog || {}).map((entry) => ({
    id: `llm:${entry.id}`, providerId: entry.id, kind: "llm", label: entry.name,
    adapter: entry.provider_type || "", logoUrl: entry.logo_url, modelCount: entry.models?.length || 0,
    count: connections.filter((row) => row.kind === "llm" && row.catalogId.toLowerCase() === entry.id.toLowerCase()).length,
  }));
  if (sources.llm) items.push({ id: "llm:custom", providerId: "custom", kind: "llm", label: "Custom endpoint", adapter: "Custom", count: 0 });
  for (const kind of ["decision", "web"] as const) {
    for (const entry of sources[kind]?.services || []) {
      items.push({ id: `${kind}:${entry.type}`, providerId: entry.type, kind, label: entry.label, adapter: entry.type,
        count: connections.filter((row) => row.kind === kind && row.type === entry.type).length });
    }
  }
  return items.sort((a, b) => a.label.localeCompare(b.label) || a.kind.localeCompare(b.kind));
}

export function filterDeskConnections(items: DeskConnection[], search: string, kind = "all", status = "all"): DeskConnection[] {
  const needle = search.trim().toLowerCase();
  return items.filter((item) => (kind === "all" || item.kind === kind)
    && (status === "all" || status === "disabled" && !item.enabled
      || status === "configured" && item.enabled && item.configured
      || status === "incomplete" && item.enabled && !item.configured)
    && `${item.name} ${item.label} ${item.type} ${item.model} ${item.models.join(" ")} ${item.capabilities.join(" ")}`.toLowerCase().includes(needle));
}

export function connectionEndpoint(item: Pick<DeskConnection, "kind" | "name">): string {
  const root = item.kind === "llm" ? "providers" : item.kind === "decision" ? "decision-providers" : "web-connections";
  return `/dashboard-api/${root}/${encodeURIComponent(item.name)}`;
}
