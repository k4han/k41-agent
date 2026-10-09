import { useSessions } from "@/lib/sessionStore";
import type { ActiveSession } from "@/types";

export function useHomeSessions(initial: ActiveSession[]) {
  return useSessions(initial);
}
