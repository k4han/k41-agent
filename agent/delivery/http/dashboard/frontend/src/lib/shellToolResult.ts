const SHELL_TOOLS = new Set([
  "bash", "run_bash", "exec_command", "read_process_output", "write_process_input", "stop_process",
]);

export function isShellTool(name: string | null | undefined): boolean {
  return SHELL_TOOLS.has(name || "");
}

export function shellToolResult(result: unknown, name?: string | null): {
  output: string;
  outcome: string;
  failed: boolean;
  running: boolean;
} | null {
  if (typeof result !== "string") return null;
  const match = result.match(
    /^([\s\S]*)\n\n(Command exited with code (-?\d+)\.|Command timed out\.|Command was cancelled\.|Process is still running\.)$/,
  );
  if (!match) return null;
  const stopped = name === "stop_process" && match[2] === "Command was cancelled.";
  return {
    output: match[1],
    outcome: match[2],
    failed: !stopped && (match[3] !== undefined ? Number(match[3]) !== 0 : match[2] !== "Process is still running."),
    running: match[2] === "Process is still running.",
  };
}
