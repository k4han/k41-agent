import { For } from "solid-js";
import type { Identity } from "@/types";

export function IdentityPicker(props: {
  value: string;
  onChange: (value: string) => void;
  identities: Identity[];
  emptyLabel?: string;
  disabled?: boolean;
}) {
  return (
    <select
      class="select"
      value={props.value}
      onChange={(event) => props.onChange(event.currentTarget.value)}
      disabled={props.disabled}
    >
      <option value="">{props.emptyLabel || "No notification"}</option>
      <For each={props.identities}>
        {(identity) => (
          <option value={`${identity.platform}:${identity.external_id}`}>
            {identity.platform} - {identity.external_id}
          </option>
        )}
      </For>
    </select>
  );
}
