import { ChevronDown } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, JSX, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { computeFloatingPosition, floatingMenuStyle } from "@/lib/floating";

export type SelectControlOption = {
  value: string;
  label: string;
  disabled?: boolean;
  title?: string;
  icon?: JSX.Element;
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
  align?: "left" | "right";
}) {
  const [open, setOpen] = createSignal(false);
  const [menuPos, setMenuPos] = createSignal({ top: 0, left: 0, width: 0, maxHeight: 260 });
  let controlRef: HTMLDivElement | undefined;
  let triggerRef: HTMLButtonElement | undefined;
  let menuRef: HTMLDivElement | undefined;

  const resolveAlign = (): "left" | "right" => {
    if (props.align) return props.align;
    const anchor = triggerRef ?? controlRef;
    if (anchor) {
      const rect = anchor.getBoundingClientRect();
      if (rect.left < window.innerWidth / 2) {
        return "left";
      }
    }
    return "right";
  };

  const updateMenuPosition = (force = false) => {
    if (!triggerRef || (!open() && !force)) return;
    const prefersTop = Boolean(controlRef?.closest(".chat-agent-picker, .chat-composer"));
    const rect = computeFloatingPosition(triggerRef, {
      preferred: prefersTop ? "top" : "bottom",
      defaultMaxHeight: 260,
      minWidth: 220,
      align: resolveAlign(),
    });
    setMenuPos({ top: rect.top, left: rect.left, width: rect.width, maxHeight: rect.maxHeight });
  };

  createEffect(() => {
    if (!open()) return;
    updateMenuPosition();
    const handler = () => updateMenuPosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    onCleanup(() => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    });
  });

  const selectedOption = createMemo(() =>
    props.options.find((option) => option.value === props.value),
  );
  const selectedLabel = createMemo(() => selectedOption()?.label || props.value || "");

  const close = () => setOpen(false);
  const toggle = () => {
    if (!props.disabled) {
      const next = !open();
      if (next) {
        updateMenuPosition(true);
      }
      setOpen(next);
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
    if (target instanceof Node) {
      if (controlRef?.contains(target)) return;
      if (menuRef?.contains(target)) return;
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
        ref={triggerRef}
        class={`select-control-trigger ${props.icon || selectedOption()?.icon ? "select-control-with-icon" : ""}`}
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
        <Show when={selectedOption()?.icon && !props.icon}>
          <span class="select-control-icon select-control-option-icon">
            {selectedOption()?.icon}
          </span>
        </Show>
        <span class="select-control-value">{selectedLabel()}</span>
        <ChevronDown class="select-control-caret" size={14} />
      </button>
      <Show when={open()}>
        <Portal>
          <div
            ref={menuRef}
            class="select-control-menu select-control-menu--portal"
            role="listbox"
            aria-label={props.ariaLabel}
            style={{
              top: `${menuPos().top}px`,
              left: `${menuPos().left}px`,
              width: `${menuPos().width}px`,
              "max-height": `${menuPos().maxHeight}px`,
            }}
          >
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
                  <Show when={option.icon}>
                    <span class="select-control-icon select-control-option-icon" aria-hidden="true">
                      {option.icon}
                    </span>
                  </Show>
                  {option.label}
                </button>
              )}
            </For>
          </div>
        </Portal>
      </Show>
    </div>
  );
}
