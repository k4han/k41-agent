import { A } from "@solidjs/router";
import { createEffect, createMemo, createSignal, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import { WorkspaceStorageStatus } from "@/components/WorkspaceStorageStatus";
import { WorkspaceHostMirror } from "@/components/WorkspaceHostMirror";
import { ProjectLocalSync } from "@/components/ProjectLocalSync";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { getBackendDisplayName, getEnabledBackends } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import type { ThreadRuntimePayload, WorkspaceBackendKey } from "@/types";

export function WorkspaceRuntimePicker(props: {
  threadId?: string;
  payload: ThreadRuntimePayload;
  busy: boolean;
  disabled?: boolean;
  error: string;
  draft?: boolean;
  draftBackend?: WorkspaceBackendKey;
  portalMount?: HTMLElement;
  onSwitch: (backend: WorkspaceBackendKey, previewToken?: string) => Promise<void>;
  onAbandon?: () => Promise<void>;
}) {
  const currentBackend = createMemo(() => (props.draft ? props.draftBackend : undefined) || props.payload.workspace?.execution.backend || "local");
  const [selected, setSelected] = createSignal<WorkspaceBackendKey>(currentBackend());
  const [abandoning, setAbandoning] = createSignal(false);
  useCatalogAndLoad(async () => { setSelected(currentBackend()); });
  createEffect(() => setSelected(currentBackend()));
  const transition = () => props.payload.runtime?.transition || props.payload.transition;
  const unavailable = () => !props.draft && props.payload.runtime?.status === "unavailable";
  const manual = () => props.payload.sync_policy === "manual_apply";
  const needsPreview = () => !props.draft && manual() && selected() === "local" && currentBackend() !== "local";
  const recoverTemporary = () => !props.payload.workspace?.project_id && currentBackend() !== "local"
    && ["stopped", "destroyed"].includes(props.payload.host_mirror?.backend_status || "");
  const abandonSwitch = async () => {
    if (!props.onAbandon || abandoning()) return;
    setAbandoning(true);
    try { await props.onAbandon(); } finally { setAbandoning(false); }
  };
  const phaseLabel = () => ({ saving: "Saving files", provisioning: "Creating environment", restoring: "Loading files",
    verifying: "Verifying files", activating: "Activating environment", completed: "Backend switched", failed: "Switch failed",
    allocation_unknown: "Allocation outcome unknown; reconcile the provider inventory, then abandon the switch" }[transition()?.phase || "completed"]);
  return <div class="composer-runtime-picker">
    <Show when={!props.draft}>
    <Show when={props.payload.workspace}>{(binding) => <WorkspaceStorageStatus name={props.payload.workspace_name}
      execution={binding().execution} persistence={props.payload.persistence} localLink={props.payload.local_link} />}</Show>
    <p>Switching changes this session's backend. Other sessions keep their current environments. Running processes and installed dependencies are not transferred.</p>
    <Show when={!manual() && props.payload.host_mirror}>{(state) => <WorkspaceHostMirror state={state()}
      projectId={props.payload.workspace?.project_id} workspaceId={props.payload.workspace?.project_workspace_id}
      threadId={props.threadId}
      generation={props.payload.workspace?.runtime_generation} disabled={props.busy || props.disabled} />}</Show>
    <Show when={!manual() && !props.payload.host_mirror && props.payload.local_link && props.payload.workspace?.project_id}>
      <A class="btn" href={`/projects/${props.payload.workspace!.project_id}`}>Manage local synchronization</A>
    </Show>
    <Show when={manual() && currentBackend() !== "local" && !unavailable()}>
      <p>Cloud changes are saved separately. Local files change only after you preview and apply them.</p>
      <ProjectLocalSync projectId={props.payload.workspace?.project_id || undefined}
        workspaceId={props.payload.workspace?.project_workspace_id} threadId={props.threadId}
        generation={props.payload.workspace?.runtime_generation || props.payload.host_mirror?.generation || ""}
        localLink={props.payload.local_link} disabled={props.busy || props.disabled}
        onApplied={() => window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED))}
        onContinue={needsPreview() ? (token) => props.onSwitch("local", token) : undefined} />
    </Show>
    </Show>
    <Show when={props.draft}><p>The selected backend will be used when you send your first message.</p></Show>
    <label>Execution backend</label>
    <SelectControl value={selected()} ariaLabel="Execution backend" portalMount={props.portalMount}
      onChange={(value) => setSelected(value as WorkspaceBackendKey)}
      disabled={props.disabled || props.busy || unavailable()}
      options={getEnabledBackends().filter((backend) => ["local", "daytona", "modal"].includes(backend.name))
        .map((backend) => ({ value: backend.name, label: getBackendDisplayName(backend.name) }))} />
    <Show when={!props.draft && selected() === "local" && props.payload.workspace?.execution.backend !== "local"}>
      <p>Execution will continue in the fixed host workspace after synchronization completes.</p>
    </Show>
    <button class="btn btn-primary" type="button"
      disabled={props.disabled || props.busy || unavailable() || needsPreview() || (selected() === currentBackend() && !recoverTemporary())}
      onClick={() => void props.onSwitch(selected())}>{props.busy ? "Switching backend..."
        : props.draft ? "Use selected backend" : recoverTemporary() && selected() === currentBackend() ? "Recover environment" : "Switch backend"}</button>
    <Show when={transition()?.phase === "allocation_unknown" && props.onAbandon}>
      <button class="btn" type="button" disabled={props.disabled || abandoning()}
        onClick={() => void abandonSwitch()}>{abandoning() ? "Abandoning..." : "Abandon switch"}</button>
      <p role="status">Reconcile the provider inventory before abandoning. This only clears the blocked transition; it never provisions a duplicate environment.</p>
    </Show>
    <Show when={unavailable() && !transition()}>
      <p role="status">The previous environment is gone. Recover the workspace (restore or move to local) before switching backends.</p>
    </Show>
    <Show when={transition()}><p role="status">{phaseLabel()}<Show when={props.busy}> · {transition()?.target_backend}</Show></p></Show>
    <Show when={props.error || transition()?.error}><p role="alert">{props.error || transition()?.error}</p></Show>
    <Show when={transition()?.cleanup_error}><p role="status">The current workspace is retained. Old environment cleanup will be retried: {transition()?.cleanup_error}</p></Show>
  </div>;
}
