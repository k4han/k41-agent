import { createEffect, createSignal, JSX, Show } from "solid-js";
import { TriangleAlert } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import "@/styles/instabox.css";

/** Destructive confirmation that only unlocks after the user types the exact value. */
export function TypedConfirmDialog(props: {
  open: boolean;
  title: string;
  expected: string;
  confirmLabel: string;
  busy?: boolean;
  children?: JSX.Element;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = createSignal("");
  createEffect(() => {
    void props.expected;
    if (props.open) setTyped("");
  });
  const matches = () => typed().trim() === props.expected;

  return (
    <Dialog
      open={props.open}
      size="sm"
      icon={<TriangleAlert size={17} />}
      iconVariant="danger"
      title={props.title}
      onClose={() => { if (!props.busy) props.onClose(); }}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "8px" }}>
          <button class="btn" type="button" disabled={props.busy} onClick={props.onClose}>Cancel</button>
          <button class="btn btn-danger" type="button" disabled={props.busy || !matches()} onClick={props.onConfirm}>
            {props.busy ? "Working…" : props.confirmLabel}
          </button>
        </div>
      }
    >
      <div class="instabox-confirm">
        <Show when={props.children}>{props.children}</Show>
        <label>
          Type <code>{props.expected}</code> to confirm.
          <input
            type="text"
            value={typed()}
            autocomplete="off"
            spellcheck={false}
            aria-label="Confirmation"
            onInput={(event) => setTyped(event.currentTarget.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && matches() && !props.busy) props.onConfirm(); }}
          />
        </label>
      </div>
    </Dialog>
  );
}
