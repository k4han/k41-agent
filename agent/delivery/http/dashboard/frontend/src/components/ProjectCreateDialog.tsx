import { createSignal, onMount, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { WorkspaceSelector, type WorkspaceSelectionDraft } from "@/components/WorkspaceSelector";
import { apiFetch, postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { notifyProjectsChanged } from "@/lib/projects";
import type { ProjectSummary, WorkspaceBinding } from "@/types";

export function ProjectCreateDialog(props: { onClose: () => void }) {
  const [label, setLabel] = createSignal("");
  const [defaultDir, setDefaultDir] = createSignal("");
  const [selection, setSelection] = createSignal<WorkspaceSelectionDraft | null>(null);
  const [resolved, setResolved] = createSignal<WorkspaceBinding | null>(null);
  const [busy, setBusy] = createSignal(false);
  const [browserOpen, setBrowserOpen] = createSignal(false);
  const [error, setError] = createSignal("");
  onMount(async () => {
    try {
      const data = await apiFetch<{ workspace: WorkspaceBinding | null }>(API_PATHS.workspaceDefault);
      const path = data.workspace?.execution.backend === "local" ? data.workspace.execution.locator : "";
      setDefaultDir(path);
    } catch (error) { setError(error instanceof Error ? error.message : "Failed to load the default workspace."); }
  });
  const valid = () => {
    const value = selection();
    return !!value && (value.source === "path" ? !!value.localPath.trim()
      : value.source === "github" ? value.repositoryId !== null : value.source === "sandbox");
  };
  const save = async () => {
    if (!valid() || busy()) return;
    setBusy(true);
    setError("");
    try {
      const value = selection()!;
      let workspace = resolved();
      if (!workspace) {
        const result = await postJson<{ workspace: WorkspaceBinding }>(API_PATHS.workspaceResolve, {
          kind: value.source === "github" ? "github" : value.backend, backend: value.backend,
          locator: value.source === "path" ? value.localPath : value.sandboxId,
          repository_id: value.source === "github" ? value.repositoryId : null,
        });
        workspace = result.workspace;
        setResolved(workspace);
      }
      await postJson<ProjectSummary>(API_PATHS.projects, { workspace, ...(label().trim() ? { label: label().trim() } : {}) });
      notifyProjectsChanged();
      props.onClose();
    } catch (error) { setError(error instanceof Error ? error.message : "Failed to add project."); }
    finally { setBusy(false); }
  };
  return <Dialog open title="Add project" subtitle="Choose a workspace to keep in your project catalog." size="lg"
    closeOnEscape={!browserOpen()} closeOnBackdrop={!browserOpen()}
    onClose={() => { if (!busy()) props.onClose(); }}>
    <div class="project-form">
      <label class="field">Project name (optional)<input class="input" value={label()} maxLength={255}
        disabled={busy()} placeholder="Use the workspace name" onInput={(event) => setLabel(event.currentTarget.value)} /></label>
      <WorkspaceSelector workingDir="" defaultWorkingDir={defaultDir()} locked={false} disabled={busy()}
        allowTemporary={false} selection={selection()} embedded onBrowserOpenChange={setBrowserOpen}
        onSelectionChange={(value) => { setSelection(value); setResolved(null); }} />
      <Show when={error()}><p class="project-error" role="alert">{error()}</p></Show>
      <button class="btn btn-primary" type="button" disabled={!valid() || busy()} onClick={() => void save()}>
        {busy() ? "Adding..." : "Add project"}</button>
    </div>
  </Dialog>;
}
