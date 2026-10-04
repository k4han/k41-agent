export type ProviderTab = "llm" | "web" | "workspace" | "decision";

export function currentProviderTab(path: string, search: string): ProviderTab {
  for (const tab of ["llm", "web", "workspace", "decision"] as const) {
    if (path.startsWith(`/settings/providers/${tab}/`) || path === `/settings/providers/${tab}`) return tab;
  }
  const tab = new URLSearchParams(search).get("tab");
  return tab === "web" || tab === "workspace" || tab === "decision" ? tab : "llm";
}

export function connectionReturnTo(path: string): string {
  return /^\/settings\/(tools|agents(?:\/[A-Za-z0-9_-]+)?)$/.test(path)
    ? path : "/settings/providers?tab=web";
}
