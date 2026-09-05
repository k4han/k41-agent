import { JSX, Show } from "solid-js";
import { AlertTriangle, Info, Loader2 } from "lucide-solid";
import { Dialog, DialogSize } from "@/components/Dialog";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string | JSX.Element;
  description?: string | JSX.Element;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: "danger" | "warning" | "primary";
  size?: DialogSize;
  icon?: JSX.Element;
  loading?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  const variant = () => props.confirmVariant || "danger";
  const label = () => props.confirmLabel || "Confirm";
  const cancel = () => props.cancelLabel || "Cancel";

  const defaultIcon = () => {
    switch (variant()) {
      case "danger":
        return <AlertTriangle size={17} />;
      case "warning":
        return <AlertTriangle size={17} />;
      case "primary":
      default:
        return <Info size={17} />;
    }
  };

  return (
    <Dialog
      open={props.open}
      title={props.title}
      subtitle={props.description}
      size={props.size || "sm"}
      icon={props.icon || defaultIcon()}
      iconVariant={variant()}
      onClose={props.onClose}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "10px", width: "100%" }}>
          <button
            class="btn"
            type="button"
            disabled={props.loading}
            onClick={props.onClose}
          >
            {cancel()}
          </button>
          <button
            class={`btn btn-${variant()}`}
            type="button"
            disabled={props.loading}
            onClick={props.onConfirm}
          >
            <Show
              when={props.loading}
              fallback={
                <Show when={variant() === "danger"}>
                  <AlertTriangle size={14} />
                </Show>
              }
            >
              <Loader2 size={14} class="spin-icon" />
            </Show>
            {props.loading ? "Processing..." : label()}
          </button>
        </div>
      }
    >
      <div class="confirm-dialog-content">
        <Show when={typeof props.message === "string"} fallback={props.message}>
          <p class="confirm-dialog-message">{props.message as string}</p>
        </Show>
      </div>
    </Dialog>
  );
}

