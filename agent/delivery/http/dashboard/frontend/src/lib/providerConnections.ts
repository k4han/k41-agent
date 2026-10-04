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
