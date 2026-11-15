export function suggestProviderName(providerId: string, existingNames: string[], normalizeHyphens = false): string {
  const normalize = (value: string) => normalizeHyphens ? value.toLowerCase().replaceAll("-", "_") : value.toLowerCase();
  const base = providerId.replace(/[^A-Za-z0-9_-]/g, "-") || "provider";
  const occupied = new Set(existingNames.map(normalize));
  occupied.add("default");
  let candidate = base;
  let suffix = 2;
  while (occupied.has(normalize(candidate))) candidate = `${base}-${suffix++}`;
  return candidate;
}

export function decisionProviderType(providerType: string, catalogId: string): string | undefined {
  if (providerType === "google") return "google";
  if (providerType === "openai_compatible" && ["cloudflare", "cloudflare-workers-ai"].includes(catalogId.toLowerCase())) return "cloudflare";
  return undefined;
}

export function decisionProviderFields(
  fields: Record<string, string | number>,
  definitions: Array<{ name: string; input_type: string }>,
  editing: boolean,
  replacedSecrets: string[],
): Record<string, string | number> {
  return Object.fromEntries(definitions
    .filter((field) => field.input_type !== "password" || !editing || replacedSecrets.includes(field.name))
    .map((field) => [field.name, fields[field.name]]));
}
