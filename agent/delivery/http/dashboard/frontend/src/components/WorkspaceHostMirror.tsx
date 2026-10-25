import { createMemo, createSignal, For, Show } from "solid-js";
import { postJson } from "@/lib/api";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { WorkspaceHostMirrorState } from "@/types";

export function WorkspaceHostMirror(props: {
  state: WorkspaceHostMirrorState;
  projectId?: number | null;
  workspaceId?: number | null;
  threadId?: string;
  generation?: number | string | null;
  disabled?: boolean;
}) {
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const context = createMemo(() => `${props.projectId}:${props.workspaceId}:${props.threadId}:${props.generation}`);
  const synchronize = async (action: "retry" | "keep_host" | "keep_sandbox") => {
    if (busy() || props.disabled) return;
    const requestedContext = context();
    setBusy(true); setError("");
    try {
      await postJson("/dashboard-api/workspace/host-sync", {
        project_id: props.projectId, project_workspace_id: props.workspaceId, thread_id: props.threadId,
        expected_generation: props.generation ?? props.state.generation,
        action, resolution_token: props.state.resolution_token,
      });
      if (requestedContext !== context()) return;
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED));
    } catch (reason) {
      if (requestedContext === context()) setError(reason instanceof Error ? reason.message : "Host synchronization failed.");
    } finally { setBusy(false); }
  };
  return <div class="project-storage-status workspace-host-mirror">
    <strong>Host workspace</strong>
    <p><code>{props.state.path}</code></p>
    <p role="status">{{ synced: "Synchronized", syncing: "Synchronizing", pending: "Waiting for synchronization",
      conflict: "Local changes need attention", error: "Synchronization failed" }[props.state.status]}
      <Show when={props.state.last_synced_at}> · {new Date(props.state.last_synced_at!).toLocaleString()}</Show>
    </p>
    <Show when={props.state.last_error}><p role="alert">{props.state.last_error}</p></Show>
    <Show when={props.state.conflicts?.length}>
      <p>Both copies are preserved. Choose which copy to keep for the conflicting paths.</p>
      <ul><For each={props.state.conflicts.slice(0, 100)}>{(path) => <li><code>{path}</code></li>}</For></ul>
      <button class="btn" disabled={busy() || props.disabled || !props.state.resolution_token}
        onClick={() => void synchronize("keep_sandbox")}>Keep sandbox changes</button>
      <button class="btn" disabled={busy() || props.disabled || !props.state.resolution_token}
        onClick={() => void synchronize("keep_host")}>Keep host changes</button>
    </Show>
    <button class="btn" disabled={busy() || props.disabled} onClick={() => void synchronize("retry")}>
      {busy() ? "Synchronizing..." : "Retry synchronization"}
    </button>
    <Show when={error()}><p role="alert">{error()}</p></Show>
  </div>;
}
