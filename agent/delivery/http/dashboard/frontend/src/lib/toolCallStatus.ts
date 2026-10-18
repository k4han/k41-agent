export type ToolCallResultState = {
  result: unknown;
  resultReceived?: boolean;
};

/** A received result may be empty; older transcripts only carry its value. */
export function hasToolCallResult(tool: ToolCallResultState): boolean {
  return tool.resultReceived === true || tool.result != null;
}
