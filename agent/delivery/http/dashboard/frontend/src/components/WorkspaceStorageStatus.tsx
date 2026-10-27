import { Show } from "solid-js";
import type { ProjectLocalLink, ProjectPersistence, WorkspaceRef } from "@/types";

export function workspaceSaveLabel(persistence?: ProjectPersistence) {
  return persistence?.status === "clean" ? "Saved" : persistence?.status === "error" ? "Save failed" : "Unsaved changes";
}

export function WorkspaceStorageStatus(props: {
  name?: string;
  execution: WorkspaceRef;
  persistence?: ProjectPersistence;
  localLink?: ProjectLocalLink | null;
}) {
  const location = () => props.execution.backend === "local" ? props.execution.locator
    : `${props.execution.locator}:${String(props.execution.metadata?.root || "/workspace")}`;
  return <div class="workspace-storage-summary">
    <p>Workspace: <strong>{props.name || "main"}</strong></p>
    <p>Working location: <code>{location()}</code></p>
    <Show when={props.execution.backend === "local"}><p>Commands run directly on this computer with the application's permissions.</p></Show>
    <Show when={props.execution.metadata?.files_restored && props.execution.metadata?.environment_state === "unknown"}>
      <p>Files have been restored. Installed dependencies and running processes have not been restored.</p>
    </Show>
    <Show when={props.persistence}>{(saved) => <>
      <p role="status">{workspaceSaveLabel(saved())}
        <Show when={saved().last_saved_at}> · {new Date(saved().last_saved_at!).toLocaleString()}</Show>
      </p>
      <Show when={saved().last_error}><p role="alert">{saved().last_error}</p></Show>
    </>}</Show>
    <Show when={props.localLink}>{(link) => <>
      <p>Linked local directory: <code>{link().path}</code></p>
      <p>{link().status === "active" ? "This environment writes directly to the linked directory."
        : link().status === "baseline_missing" ? "The linked directory has no comparison baseline. Its files are preserved."
        : link().status === "pending" ? "Saved project changes are waiting to be applied to the linked directory."
        : "The latest saved revision was applied to the linked directory. External local edits are kept separately."}</p>
    </>}</Show>
  </div>;
}
