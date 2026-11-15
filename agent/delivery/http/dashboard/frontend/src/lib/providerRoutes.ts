export type ProviderTab = "llm" | "pools" | "web" | "workspace" | "decision";
export type ProviderConnectionKind = "llm" | "decision" | "web";
export type ProviderSection = "connections" | "catalog" | "pools" | "defaults" | "workspace";

export function currentConnectionKind(path: string, search: string): ProviderConnectionKind | undefined {
  for (const kind of ["llm", "decision", "web"] as const) {
    if (path.startsWith(`/settings/providers/${kind}/`)) return kind;
  }
  const query = new URLSearchParams(search);
  const kind = query.get("kind") || query.get("tab");
  return kind === "llm" || kind === "decision" || kind === "web" ? kind : undefined;
}

export function currentProviderSection(path: string, search: string): ProviderSection {
  if (path.startsWith("/settings/providers/workspace/")) return "workspace";
  if (/^\/settings\/providers\/(llm|decision|web)\//.test(path)) return "connections";
  const query = new URLSearchParams(search);
  if (query.has("new")) return "catalog";
  const tab = query.get("tab");
  return tab === "catalog" || tab === "pools" || tab === "defaults" || tab === "workspace" ? tab : "connections";
}

export function currentProviderTab(path: string, search: string): ProviderTab {
  for (const tab of ["llm", "pools", "web", "workspace", "decision"] as const) {
    if (path.startsWith(`/settings/providers/${tab}/`) || path === `/settings/providers/${tab}`) return tab;
  }
  const tab = new URLSearchParams(search).get("tab");
  return tab === "pools" || tab === "web" || tab === "workspace" || tab === "decision" ? tab : "llm";
}

export function connectionReturnTo(path: string): string {
  return /^\/settings\/(tools|agents(?:\/[A-Za-z0-9_-]+)?)$/.test(path)
    ? path : "/settings/providers?tab=web";
}
