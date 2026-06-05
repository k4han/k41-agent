export const STREAM_EVENTS = {
  THREAD_CREATED: "thread_created",
  MESSAGE: "message",
  FINAL: "final",
  TOOL_CALL: "tool_call",
  TOOL_RESULT: "tool_result",
  PLAN_REVIEW: "plan_review",
  USER_INPUT_REQUEST: "user_input_request",
  ERROR: "error",
} as const;

export const STREAM_ERROR_CODES = {
  RECURSION_LIMIT_REACHED: "recursion_limit_reached",
} as const;

export const SESSION_EVENTS = {
  SNAPSHOT: "snapshot",
  SESSION_STARTED: "session_started",
  SESSION_STOPPED: "session_stopped",
  SESSION_UPDATED: "session_updated",
} as const;

export const BACKGROUND_TASK_EVENTS = {
  SNAPSHOT: "snapshot",
  DONE: "done",
  HEARTBEAT: "heartbeat",
} as const;

export const CUSTOM_DOM_EVENTS = {
  SESSION_STARTED: "kaka:session-started",
  SESSION_STOPPED: "kaka:session-stopped",
  SESSION_UPDATED: "kaka:session-updated",
  THREAD_START_RUNNING: "kaka:thread-start-running",
  THREAD_STOP_RUNNING: "kaka:thread-stop-running",
  THREAD_EXTERNAL_ABORT: "kaka:thread-external-abort",
  THREADS_CHANGED: "kaka:threads-changed",
  TASKS_CHANGED: "kaka:tasks-changed",
} as const;

export function recursionLimitStorageKey(threadId: string): string {
  return `kaka:recursion-limit-reached:${threadId}`;
}
