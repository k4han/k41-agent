import { JSX, Show } from "solid-js";
import { AlertTriangle } from "lucide-solid";
import { Dialog } from "@/components/Dialog";

export function ConfirmDialog(props: {
  open: boolean;
  title: string;
  message: string | JSX.Element;
  confirmLabel?: string;
  confirmVariant?: "danger" | "warning" | "primary";
  loading?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const variant = () => props.confirmVariant || "danger";
  const label = () => props.confirmLabel || "Confirm";

  return (
    <Dialog
      open={props.open}
      title={props.title}
      onClose={props.onClose}
      footer={
        <div class="row-wrap">
          <button
            class="btn"
            type="button"
            disabled={props.loading}
            onClick={props.onClose}
          >
            Cancel
          </button>
          <button
            class={`btn btn-${variant()}`}
            type="button"
            disabled={props.loading}
            onClick={props.onConfirm}
          >
            <Show when={variant() === "danger"}>
              <AlertTriangle size={14} />
            </Show>
            {props.loading ? "Processing..." : label()}
          </button>
        </div>
      }
    >
      <Show when={typeof props.message === "string"} fallback={props.message}>
        <p>{props.message as string}</p>
      </Show>
    </Dialog>
  );
}
