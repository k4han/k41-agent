import { createMemo, type JSX } from "solid-js";

import { SelectControl, type SelectControlOption } from "@/components/SelectControl";
import type { Identity } from "@/types";

export function IdentityPicker(props: {
  value: string;
  onChange: (value: string) => void;
  identities: Identity[];
  emptyLabel?: string;
  disabled?: boolean;
  class?: string;
  style?: JSX.CSSProperties | string;
  ariaLabel?: string;
}) {
  const options = createMemo<SelectControlOption[]>(() => [
    { value: "", label: props.emptyLabel || "No notification" },
    ...props.identities.map((identity) => ({
      value: `${identity.platform}:${identity.external_id}`,
      label: `${identity.platform} - ${identity.external_id}`,
    })),
  ]);

  return (
    <SelectControl
      class={props.class}
      style={props.style}
      value={props.value}
      options={options()}
      onChange={props.onChange}
      disabled={props.disabled}
      ariaLabel={props.ariaLabel || "Notification identity"}
    />
  );
}
