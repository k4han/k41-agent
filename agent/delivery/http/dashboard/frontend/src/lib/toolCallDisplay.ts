export const EXECUTE_TOOL_NAME = "execute_tool";
export const SEARCH_TOOLS_NAME = "search_tools";

export function toolCallDisplay(
  name: string,
  args: unknown,
): { name: string; args: unknown; dispatched?: true } {
  if (name === EXECUTE_TOOL_NAME) {
    let input = args;
    if (typeof input === "string") {
      try {
        input = JSON.parse(input);
      } catch {
        return { name, args };
      }
    }
    if (input && typeof input === "object" && !Array.isArray(input)) {
      const { tool_name: target, arguments: parameters } = input as {
        tool_name?: unknown;
        arguments?: unknown;
      };
      if (typeof target === "string" && target.trim() && target !== EXECUTE_TOOL_NAME
        && parameters && typeof parameters === "object" && !Array.isArray(parameters)) {
        return { name: target, args: parameters, dispatched: true };
      }
    }
  }
  return { name, args };
}

export function toolResultDisplay(name: string | null | undefined, result: unknown): unknown {
  if (name !== SEARCH_TOOLS_NAME) return result;

  let matches = result;
  if (typeof matches === "string" && matches.trimStart().startsWith("[")) {
    try {
      matches = JSON.parse(matches);
    } catch {
      return result;
    }
  }
  // Older conversations store the matching definitions as JSON.
  if (!Array.isArray(matches)) return result;
  if (matches.length === 0) return "No matching on-demand tools.";
  const names = matches.flatMap((match) => {
    const name = match && typeof match === "object" ? (match as { name?: unknown }).name : match;
    return typeof name === "string" && name.trim() ? [name] : [];
  });
  return names.length ? names.join("\n") : result;
}
