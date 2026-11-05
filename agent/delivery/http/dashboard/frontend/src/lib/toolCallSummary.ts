const TOOL_LABELS: Record<string, string> = {
  read_file: "read",
  write_file: "write",
  edit_file: "edit",
  list_dir: "list",
};

function acpToolSummary(name: string | null | undefined, args: unknown): string | null {
  if (!name?.startsWith("acp_") || !args || typeof args !== "object") return null;
  const { title, paths } = args as { title?: unknown; paths?: unknown };
  // Agents title pending edits like "Run create_file?", so a path says more.
  if (Array.isArray(paths) && typeof paths[0] === "string") return paths[0];
  return typeof title === "string" && title ? title : null;
}

export function toolCallSummary(name: string | null | undefined, args: unknown): string {
  const acpSummary = acpToolSummary(name, args);
  if (acpSummary) return acpSummary;
  const toolName = name || "unknown";
  const label = TOOL_LABELS[toolName] || toolName;

  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      return label;
    }
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return label;
  }

  const values = args as Record<string, unknown>;
  const text = (key: string): string => {
    const value = values[key];
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  };

  if (toolName === "skill") {
    return [text("action") === "unload" ? "Unload skill" : "Prepare skill", text("name")].filter(Boolean).join(" ");
  }

  if (toolName === "grep" || toolName === "glob") {
    const detail = [text("pattern"), text("path")].filter(Boolean).join(" ");
    return detail ? `${label} ${detail}` : label;
  }

  const detail = [
    "file_path", "path", "command", "pattern", "query", "url", "process_id", "output_ref",
  ].map(text).find(Boolean);
  return detail ? `${label} ${detail}` : label;
}
