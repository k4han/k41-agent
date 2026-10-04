const TOOL_LABELS: Record<string, string> = {
  read_file: "read",
  write_file: "write",
  edit_file: "edit",
  list_dir: "list",
};

export function toolCallSummary(name: string | null | undefined, args: unknown): string {
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

  if (toolName === "grep" || toolName === "glob") {
    const detail = [text("pattern"), text("path")].filter(Boolean).join(" ");
    return detail ? `${label} ${detail}` : label;
  }

  const detail = [
    "file_path", "path", "command", "pattern", "query", "url", "process_id", "output_ref",
  ].map(text).find(Boolean);
  return detail ? `${label} ${detail}` : label;
}
