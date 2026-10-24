import { createEffect, createMemo, createSignal, For, onCleanup, Show } from "solid-js";
import { postJson } from "@/lib/api";
import type { ProjectLocalLink, ProjectLocalSyncPreview } from "@/types";

export function ProjectLocalSync(props: {
  projectId: number;
  workspaceId?: number | null;
  generation: number;
  localLink: ProjectLocalLink;
  disabled?: boolean;
  onApplied: () => void;
}) {
  const [preview, setPreview] = createSignal<ProjectLocalSyncPreview | null>(null);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  let version = 0;
  const context = createMemo(() => `${props.projectId}:${props.workspaceId}:${props.generation}`);
  createEffect(() => {
    context();
    version += 1;
    setPreview(null); setError(""); setBusy(false);
  });
  onCleanup(() => { version += 1; });
  const synchronize = async (apply: boolean) => {
    if (busy() || props.disabled || (apply && !preview())) return;
    const requestVersion = version;
    setBusy(true); setError("");
    try {
      const result = await postJson<ProjectLocalSyncPreview>(`/dashboard-api/projects/${props.projectId}/local-sync`, {
        expected_generation: props.generation, project_workspace_id: props.workspaceId,
        ...(apply ? { preview_token: preview()!.preview_token } : {}),
      });
      if (version !== requestVersion) return;
      setPreview(result);
      if (result.applied) props.onApplied();
    } catch (reason) {
      if (version === requestVersion) {
        setPreview(null);
        setError(reason instanceof Error ? reason.message : "Local synchronization failed. Preview the changes again.");
      }
    } finally { if (version === requestVersion) setBusy(false); }
  };
  return <Show when={props.localLink.status !== "active"}>
    <div class="project-storage-status">
      <strong>Apply changes to linked local directory</strong>
      <p><code>{props.localLink.path}</code></p>
      <p>Preview compares project changes with the last local baseline. Conflicting local edits must be resolved before applying.</p>
      <button class="btn" disabled={props.disabled || busy() || props.localLink.status === "baseline_missing"}
        onClick={() => void synchronize(false)}>{busy() ? "Checking files..." : "Preview local changes"}</button>
      <Show when={preview()}>{(result) => <>
        <p role="status">{result().applied ? "Changes applied." : `${result().changes.length} changes · ${result().conflicts.length} conflicts`}</p>
        <ul><For each={result().changes.slice(0, 100)}>{(change) => <li>{change.action}: <code>{change.path}</code></li>}</For></ul>
        <Show when={result().changes.length > 100}><p>Showing the first 100 changes.</p></Show>
        <Show when={result().conflicts.length}><p role="alert">Local conflicts:</p>
          <ul><For each={result().conflicts.slice(0, 100)}>{(path) => <li><code>{path}</code></li>}</For></ul>
        </Show>
        <Show when={!result().applied}><button class="btn btn-primary"
          disabled={props.disabled || busy() || result().conflicts.length > 0}
          onClick={() => void synchronize(true)}>Apply previewed changes</button></Show>
      </>}</Show>
      <Show when={error()}><p role="alert">{error()}</p></Show>
    </div>
  </Show>;
}
