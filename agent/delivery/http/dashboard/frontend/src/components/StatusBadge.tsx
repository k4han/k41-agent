import { type JSXElement } from "solid-js";
import { statusBadgeClass } from "@/lib/utils";

export function StatusBadge(props: { status: string; children?: JSXElement }) {
  return (
    <span class={statusBadgeClass(props.status)}>
      {props.children || props.status}
    </span>
  );
}
