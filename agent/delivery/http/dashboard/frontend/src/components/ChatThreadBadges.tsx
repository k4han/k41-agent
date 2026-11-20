import { Show } from "solid-js";

import type { ActiveSession, BackgroundTask } from "@/types";

export interface ChatThreadBadgesProps {
  isBackgroundThread: boolean;
  backgroundTask: BackgroundTask | null;
  backgroundLive: boolean;
  backgroundSession: ActiveSession | null;
  activeSession: ActiveSession | null;
}

export function ChatThreadBadges(props: ChatThreadBadgesProps) {
  const visible = () =>
    Boolean(
      props.isBackgroundThread
      || props.backgroundTask
      || props.backgroundLive
      || props.backgroundSession
      || props.activeSession,
    );

  return (
    <Show when={visible()}>
      <span class="chat-thread-badges row-wrap">
        <Show when={props.isBackgroundThread}>
          <span class="badge badge-info chat-thread-badge">background</span>
        </Show>
        <Show when={props.backgroundTask}>
          {(task) => <span class="badge chat-thread-badge">{task().status}</span>}
        </Show>
        <Show when={props.backgroundLive}>
          <span class="badge badge-info chat-thread-badge">live</span>
        </Show>
        <Show when={props.backgroundSession}>
          {(session) => <span class="badge chat-thread-badge">{session().elapsed_display}</span>}
        </Show>
        <Show when={props.activeSession}>
          {(session) => (
            <>
              <span class="badge badge-warning chat-thread-badge">running</span>
              <span class="badge chat-thread-badge" title="Elapsed time">{session().elapsed_display}</span>
            </>
          )}
        </Show>
      </span>
    </Show>
  );
}
