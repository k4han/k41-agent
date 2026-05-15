import { JSX, Show } from "solid-js";
import { X } from "lucide-solid";

export function Dialog(props: {
  open: boolean;
  title: string;
  wide?: boolean;
  children: JSX.Element;
  footer?: JSX.Element;
  onClose: () => void;
}) {
  return (
    <Show when={props.open}>
      <div class="dialog-backdrop" onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          props.onClose();
        }
      }}>
        <section class={`dialog ${props.wide ? "dialog-wide" : ""}`}>
          <header class="dialog-header">
            <div class="panel-title">{props.title}</div>
            <button class="btn btn-icon btn-sm" type="button" onClick={props.onClose}>
              <X size={15} />
            </button>
          </header>
          <div class="dialog-body">{props.children}</div>
          <Show when={props.footer}>
            <footer class="dialog-footer">{props.footer}</footer>
          </Show>
        </section>
      </div>
    </Show>
  );
}

