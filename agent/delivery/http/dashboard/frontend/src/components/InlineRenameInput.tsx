import { Show, createEffect } from "solid-js";

export function InlineRenameInput(props: {
  value: string;
  onInput: (value: string) => void;
  onBlur: () => void;
  onCancel: () => void;
  class?: string;
}) {
  let inputRef: HTMLInputElement | undefined;

  createEffect(() => {
    if (inputRef) {
      inputRef.focus();
      inputRef.select();
    }
  });

  return (
    <input
      class={props.class || "inline-rename-input"}
      value={props.value}
      ref={inputRef!}
      onInput={(event) => props.onInput(event.currentTarget.value)}
      onBlur={props.onBlur}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          event.currentTarget.blur();
        }
        if (event.key === "Escape") {
          event.preventDefault();
          props.onCancel();
        }
      }}
    />
  );
}
