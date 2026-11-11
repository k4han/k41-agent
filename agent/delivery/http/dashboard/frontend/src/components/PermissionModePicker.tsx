import { Show } from "solid-js";
import { SelectControl } from "@/components/SelectControl";
import { ACP_PERMISSION_LIMIT, FULL_ACCESS_NOTICE, PERMISSION_OPTIONS, type PermissionMode } from "@/lib/permissionMode";

export function PermissionModePicker(props: {
  value?: PermissionMode | null;
  onChange: (mode: PermissionMode | undefined) => void;
  disabled?: boolean;
  acp?: boolean;
}) {
  return <div class="permission-mode-picker" style={{ "min-width": "0", "max-width": "100%" }}>
    <SelectControl value={props.value ?? ""} options={PERMISSION_OPTIONS} ariaLabel="Permission mode"
      disabled={props.disabled} onChange={(value) => props.onChange((value || undefined) as PermissionMode | undefined)} />
    <Show when={props.value === "full_access"}><p class="hint" role="alert">{FULL_ACCESS_NOTICE}</p></Show>
    <Show when={props.acp}><p class="hint">{ACP_PERMISSION_LIMIT}</p></Show>
  </div>;
}
