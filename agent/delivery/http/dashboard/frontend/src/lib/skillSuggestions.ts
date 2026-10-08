export type SkillOption = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  shadowed: boolean;
  diagnostics?: Array<{ severity: string }>;
};

export type SkillQuery = {
  start: number;
  end: number;
  action: "load" | "refresh" | "unload";
  search: string;
};

export function getSkillQuery(prompt: string, start: number, end = start): SkillQuery | null {
  if (start !== end || start < 0 || start > prompt.length) return null;
  const lineStart = start === 0 ? 0 : prompt.lastIndexOf("\n", start - 1) + 1;
  const precedingLines = lineStart ? prompt.slice(0, lineStart - 1).split("\n") : [];
  if (precedingLines.some((line) => !/^\/skill\s+\S+/i.test(line))) return null;
  const text = prompt.slice(lineStart, start);
  const match = /^\/(?:skill(?:\s+(?:(load|refresh|unload)\s+)?([^\s]*))?|([^\s/]*))$/i.exec(text);
  if (!match) return null;
  const tokenEnd = prompt.slice(start).search(/\s/);
  return {
    start: lineStart,
    end: tokenEnd === -1 ? prompt.length : start + tokenEnd,
    action: (match[1]?.toLowerCase() as SkillQuery["action"]) || "load",
    search: (match[2] || match[3] || "").toLowerCase(),
  };
}

export function filterSkillOptions(options: SkillOption[], query: SkillQuery): SkillOption[] {
  return options
    .filter((item) => item.enabled && !item.shadowed && !item.diagnostics?.some((diagnostic) => diagnostic.severity === "error"))
    .filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(query.search))
    .sort((a, b) => Number(b.name.toLowerCase().startsWith(query.search)) - Number(a.name.toLowerCase().startsWith(query.search)) || a.name.localeCompare(b.name));
}

export function insertSkillSuggestion(prompt: string, query: SkillQuery, name: string): { prompt: string; caret: number } {
  const command = `/skill ${query.action === "load" ? "" : `${query.action} `}${name}`;
  const suffix = prompt.slice(query.end);
  const replacement = command + (suffix.startsWith(" ") ? "" : " ");
  return {
    prompt: prompt.slice(0, query.start) + replacement + suffix,
    caret: query.start + command.length + 1,
  };
}
