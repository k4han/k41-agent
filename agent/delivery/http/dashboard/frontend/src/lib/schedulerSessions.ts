import type { ActiveSession } from "@/types";

/** Platform the backend scheduler assigns to the sessions it starts. */
export const SCHEDULER_SESSION_PLATFORM = "bg";

export function isSchedulerSession(session: Pick<ActiveSession, "platform">): boolean {
  return session.platform === SCHEDULER_SESSION_PLATFORM;
}
