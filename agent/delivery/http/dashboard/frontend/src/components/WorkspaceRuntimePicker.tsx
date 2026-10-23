import { createEffect, createSignal, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import { getBackendDisplayName, getEnabledBackends } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import type { ThreadRuntimePayload, WorkspaceBackendKey } from "@/types";

export function WorkspaceRuntimePicker(props: {
  payload: ThreadRuntimePayload;
  busy: boolean;
  disabled?: boolean;
  error: string;
  portalMount?: HTMLElement;
  onSwitch: (backend: WorkspaceBackendKey) => Promise<void>;
}) {
  const [selected, setSelected] = createSignal<WorkspaceBackendKey>(props.payload.workspace?.execution.backend || "local");
  useCatalogAndLoad(async () => { setSelected(props.payload.workspace?.execution.backend || "local"); });
  createEffect(() => setSelected(props.payload.workspace?.execution.backend || "local"));
  const transition = () => props.payload.runtime?.transition;
  const unavailable = () => props.payload.runtime?.status === "unavailable";
  const phaseLabel = () => ({ saving: "Saving files", provisioning: "Creating environment", restoring: "Loading files",
    verifying: "Verifying files", activating: "Activating environment", completed: "Backend switched", failed: "Switch failed" }[transition()?.phase || "completed"]);
  return <div class="composer-runtime-picker">
    <p>Switching the backend updates all conversations sharing this project workspace. Running processes and installed dependencies are not transferred.</p>
    <label>Execution backend</label>
    <SelectControl value={selected()} ariaLabel="Execution backend" portalMount={props.portalMount}
      onChange={(value) => setSelected(value as WorkspaceBackendKey)}
      disabled={props.disabled || props.busy || unavailable()}
      options={getEnabledBackends().filter((backend) => ["local", "daytona", "modal"].includes(backend.name))
        .map((backend) => ({ value: backend.name, label: getBackendDisplayName(backend.name) }))} />
    <Show when={selected() === "local" && props.payload.workspace?.execution.backend !== "local"}>
      <p>Files will be loaded into a new application-managed local directory.</p>
    </Show>
    <button class="btn btn-primary" type="button"
      disabled={props.disabled || props.busy || unavailable() || selected() === props.payload.workspace?.execution.backend}
      onClick={() => void props.onSwitch(selected())}>{props.busy ? "Switching backend..." : "Switch backend"}</button>
    <Show when={unavailable() && !transition()}>
      <p role="status">The previous environment is gone. Recover the workspace (restore or move to local) before switching backends.</p>
    </Show>
    <Show when={transition()}><p role="status">{phaseLabel()}<Show when={props.busy}> · {transition()?.target_backend}</Show></p></Show>
    <Show when={props.error || transition()?.error}><p role="alert">{props.error || transition()?.error}</p></Show>
    <Show when={transition()?.cleanup_error}><p role="status">The current workspace is retained. Old environment cleanup will be retried: {transition()?.cleanup_error}</p></Show>
  </div>;
}
