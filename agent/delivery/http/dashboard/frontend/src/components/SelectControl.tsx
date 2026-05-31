import { ChevronDown } from "lucide-solid";
import { createMemo, createSignal, For, JSX, onCleanup, onMount, Show } from "solid-js";

export type SelectControlOption = {
  value: string;
  label: string;
  disabled?: boolean;
  title?: string;
};

export function SelectControl(props: {
  value: string;
  options: SelectControlOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  class?: string;
  disabled?: boolean;
  icon?: JSX.Element;
  title?: string;
  style?: JSX.CSSProperties | string;
}) {
  const [open, setOpen] = createSignal(false);
  let controlRef: HTMLDivElement | undefined;

  const selectedOption = createMemo(() =>
    props.options.find((option) => option.value === props.value),
  );
  const selectedLabel = createMemo(() => selectedOption()?.label || props.value || "");

  const close = () => setOpen(false);
  const toggle = () => {
    if (!props.disabled) {
      setOpen((current) => !current);
    }
  };
  const selectOption = (option: SelectControlOption) => {
    if (option.disabled) {
      return;
    }
    props.onChange(option.value);
    close();
  };
  const handleDocumentPointerDown = (event: PointerEvent) => {
    const target = event.target;
    if (target instanceof Node && controlRef?.contains(target)) {
      return;
    }
    close();
  };
  const handleKeyDown = (event: KeyboardEvent) => {
    if (props.disabled) {
      return;
    }
    if (event.key === "Escape") {
      close();
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  };

  onMount(() => {
    document.addEventListener("pointerdown", handleDocumentPointerDown);
  });

  onCleanup(() => {
    document.removeEventListener("pointerdown", handleDocumentPointerDown);
  });

  return (
    <div
      ref={controlRef}
      class={`select-control ${open() ? "open" : ""} ${props.class || ""}`}
      title={props.title}
      style={props.style}
    >
      <button
        class={`select-control-trigger ${props.icon ? "select-control-with-icon" : ""}`}
        type="button"
        disabled={props.disabled}
        aria-label={props.ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open()}
        onClick={toggle}
        onKeyDown={handleKeyDown}
      >
        <Show when={props.icon}>
          <span class="select-control-icon">{props.icon}</span>
        </Show>
        <span class="select-control-value">{selectedLabel()}</span>
        <ChevronDown class="select-control-caret" size={14} />
      </button>
      <Show when={open()}>
        <div class="select-control-menu" role="listbox" aria-label={props.ariaLabel}>
          <For each={props.options}>
            {(option) => (
              <button
                class={`select-control-option ${option.value === props.value ? "active" : ""}`}
                type="button"
                disabled={option.disabled}
                role="option"
                aria-selected={option.value === props.value}
                title={option.title || option.label}
                onClick={() => selectOption(option)}
              >
                {option.label}
              </button>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
