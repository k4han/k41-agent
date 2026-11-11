import { Shield, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-solid";
import { createMemo, Show } from "solid-js";
import { SelectControl, type SelectControlOption } from "@/components/SelectControl";
import { ACP_PERMISSION_LIMIT, FULL_ACCESS_NOTICE, type PermissionMode } from "@/lib/permissionMode";

export function PermissionModePicker(props: {
  value?: PermissionMode | null;
  onChange: (mode: PermissionMode | undefined) => void;
  disabled?: boolean;
  acp?: boolean;
  class?: string;
}) {
  const isFullAccess = () => props.value === "full_access";

  const options = createMemo<SelectControlOption[]>(() => [
    {
      value: "",
      label: "Inherit permissions",
      icon: <Shield size={14} />,
    },
    {
      value: "ask",
      label: "Ask for approval",
      icon: <ShieldQuestion size={14} />,
    },
    {
      value: "auto",
      label: "Approve for me",
      icon: <ShieldCheck size={14} />,
    },
    {
      value: "full_access",
      label: "Full access",
      title: FULL_ACCESS_NOTICE,
      icon: <ShieldAlert size={14} />,
      class: "select-control-option--full-access",
    },
  ]);

  return (
    <div
      class={`permission-mode-picker ${isFullAccess() ? "permission-mode--full-access" : ""} ${props.class || ""}`}
      data-mode={props.value || "inherit"}
      title={isFullAccess() ? FULL_ACCESS_NOTICE : undefined}
      style={{ "min-width": "0", "max-width": "100%" }}
    >
      <SelectControl
        value={props.value ?? ""}
        options={options()}
        ariaLabel="Permission mode"
        disabled={props.disabled}
        onChange={(value) => props.onChange((value || undefined) as PermissionMode | undefined)}
      />
      <Show when={props.acp}><p class="hint">{ACP_PERMISSION_LIMIT}</p></Show>
    </div>
  );
}
