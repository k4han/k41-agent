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
      <span class="row-wrap" style="gap: 6px; display: inline-flex; align-items: center; margin-left: 8px;">
        <Show when={props.isBackgroundThread}>
          <span class="badge badge-info" style="font-size: 10px; padding: 2px 6px;">background</span>
        </Show>
        <Show when={props.backgroundTask}>
          {(task) => <span class="badge" style="font-size: 10px; padding: 2px 6px;">{task().status}</span>}
        </Show>
        <Show when={props.backgroundLive}>
          <span class="badge badge-info" style="font-size: 10px; padding: 2px 6px;">live</span>
        </Show>
        <Show when={props.backgroundSession}>
          {(session) => <span class="badge" style="font-size: 10px; padding: 2px 6px;">{session().elapsed_display}</span>}
        </Show>
        <Show when={props.activeSession}>
          {(session) => (
            <>
              <span class="badge badge-warning" style="font-size: 10px; padding: 2px 6px;">running</span>
              <span class="badge" style="font-size: 10px; padding: 2px 6px;" title="Elapsed time">{session().elapsed_display}</span>
            </>
          )}
        </Show>
      </span>
    </Show>
  );
}
