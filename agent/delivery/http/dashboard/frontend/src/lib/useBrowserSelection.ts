import { createEffect, createSignal, on, untrack } from "solid-js";
import type { BrowserSession } from "@/lib/useBrowserSession";

/** Keep the current viewer until its session ends or a handoff requests another session. */
export function useBrowserSelection(rootThread: () => string | null,
  session: Pick<BrowserSession, "sessions" | "sessionKey" | "status"> & Partial<Pick<BrowserSession, "requestedSession">>) {
  const [threadId, setThreadId] = createSignal<string | null>(null);
  let manualMain = false;
  createEffect(on(() => [rootThread(), session.sessionKey(), session.status()?.handoff?.handoff_id, session.requestedSession?.()] as const,
    ([root, , handoff, requested], previous) => {
      const sameRoot = previous?.[0] === root;
      if (!sameRoot) manualMain = false;
      const current = sameRoot ? untrack(threadId) : null;
      const sessions = untrack(session.sessions);
      if (handoff && (!sameRoot || handoff !== previous?.[2])) {
        manualMain = false;
        setThreadId(root);
      } else if (requested && requested !== previous?.[3]
          && (requested.thread_id === root || sessions.some(item => item.thread_id === requested.thread_id))) {
        manualMain = false;
        setThreadId(requested.thread_id);
      } else if (current && (sessions.some(item => item.thread_id === current) || (current === root && (manualMain || handoff)))) {
        return;
      } else {
        setThreadId(sessions[0]?.thread_id ?? root);
      }
    }));
  return {
    threadId,
    isMain: () => threadId() === rootThread(),
    select(thread: string) {
      if (thread !== rootThread() && !session.sessions().some(item => item.thread_id === thread)) return;
      manualMain = thread === rootThread() && !session.status()?.open;
      setThreadId(thread);
    },
  };
}
