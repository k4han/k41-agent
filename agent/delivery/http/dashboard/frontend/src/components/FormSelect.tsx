import { createMemo, createSignal, For, JSX, onCleanup, onMount, Show, splitProps } from "solid-js";
import { ChevronDown, Check, AlertCircle } from "lucide-solid";

import { classNames } from "@/lib/utils";
import type { SelectControlOption } from "./SelectControl";

export type FormSelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: SelectControlOption[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  helper?: string;
  label?: string;
  showValidationStatus?: boolean;
  class?: string;
  name?: string;
  id?: string;
  ariaLabel?: string;
  ref?: HTMLDivElement | ((el: HTMLDivElement) => void);
};

export function FormSelect(props: FormSelectProps) {
  const [local, others] = splitProps(props, [
    "value",
    "onChange",
    "options",
    "placeholder",
    "disabled",
    "required",
    "error",
    "helper",
    "label",
    "showValidationStatus",
    "class",
    "name",
    "id",
    "ariaLabel",
    "ref",
  ]);

  const [open, setOpen] = createSignal(false);
  const [touched, setTouched] = createSignal(false);
  let controlRef: HTMLDivElement | undefined;

  const selectedOption = createMemo(() =>
    local.options.find((option) => option.value === local.value),
  );
  const selectedLabel = createMemo(() => selectedOption()?.label || local.placeholder || "Select...");
  const hasError = createMemo(() => Boolean(local.error || (local.required && touched() && !local.value)));
  const isValid = createMemo(() => !hasError() && touched() && local.value);

  const close = () => setOpen(false);
  const toggle = () => {
    if (!local.disabled) {
      setOpen((current) => !current);
      setTouched(true);
    }
  };

  const selectOption = (option: SelectControlOption) => {
    if (option.disabled) {
      return;
    }
    local.onChange(option.value);
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
    if (local.disabled) {
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
      ref={(el) => {
        controlRef = el;
        if (typeof local.ref === "function") {
          local.ref(el);
        } else if (local.ref) {
          (local as any).ref = el;
        }
      }}
      class={classNames("form-select-wrapper", local.class)}
    >
      <Show when={local.label}>
        <label class="form-select-label" for={local.id}>
          {local.label}
          <Show when={local.required}>
            <span class="form-field-required" aria-label="required">
              *
            </span>
          </Show>
        </label>
      </Show>

      <div
        class={classNames(
          "form-select",
          open() && "form-select--open",
          hasError() && "form-select--error",
          isValid() && local.showValidationStatus && "form-select--valid",
        )}
        ref={controlRef}
      >
        <button
          class="form-select-trigger"
          type="button"
          disabled={local.disabled}
          aria-label={local.ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open()}
          onClick={toggle}
          onKeyDown={handleKeyDown}
        >
          <span class="form-select-value">
            <Show when={!local.value && local.placeholder} fallback={selectedLabel()}>
              <span class="form-select-placeholder">{local.placeholder}</span>
            </Show>
          </span>
          <ChevronDown class="form-select-caret" size={14} />
        </button>

        <Show when={open()}>
          <div class="form-select-menu" role="listbox" aria-label={local.ariaLabel || local.label}>
            <For each={local.options}>
              {(option) => (
                <button
                  class={classNames(
                    "form-select-option",
                    option.value === local.value && "form-select-option--active",
                    option.disabled && "form-select-option--disabled",
                  )}
                  type="button"
                  disabled={option.disabled}
                  role="option"
                  aria-selected={option.value === local.value}
                  title={option.title || option.label}
                  onClick={() => selectOption(option)}
                >
                  <span class="form-select-option-label">{option.label}</span>
                  <Show when={option.value === local.value}>
                    <Check size={14} class="form-select-option-check" />
                  </Show>
                </button>
              )}
            </For>
          </div>
        </Show>

        <Show when={local.showValidationStatus && touched()}>
          <div class="form-select-status">
            <Show when={hasError()}>
              <div class="form-select-status--error">
                <AlertCircle size={14} />
              </div>
            </Show>
            <Show when={isValid()}>
              <div class="form-select-status--valid">
                <Check size={14} />
              </div>
            </Show>
          </div>
        </Show>
      </div>

      <Show when={hasError()}>
        <div
          id={`${local.id || local.name}-error`}
          class="form-select-error"
          role="alert"
        >
          {local.error || (local.required && "This field is required")}
        </div>
      </Show>

      <Show when={local.helper && !hasError()}>
        <div class="form-select-helper">
          {local.helper}
        </div>
      </Show>
    </div>
  );
}

export type FormMultiSelectProps = {
  values: string[];
  onChange: (values: string[]) => void;
  options: SelectControlOption[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  helper?: string;
  label?: string;
  maxSelections?: number;
  class?: string;
  name?: string;
  id?: string;
  ariaLabel?: string;
};

export function FormMultiSelect(props: FormMultiSelectProps) {
  const [local, others] = splitProps(props, [
    "values",
    "onChange",
    "options",
    "placeholder",
    "disabled",
    "required",
    "error",
    "helper",
    "label",
    "maxSelections",
    "class",
    "name",
    "id",
    "ariaLabel",
  ]);

  const [open, setOpen] = createSignal(false);
  const [touched, setTouched] = createSignal(false);
  let controlRef: HTMLDivElement | undefined;

  const selectedOptions = createMemo(() =>
    local.options.filter((option) => local.values.includes(option.value)),
  );
  const hasError = createMemo(() => Boolean(local.error || (local.required && touched() && local.values.length === 0)));
  const canSelectMore = createMemo(() => !local.maxSelections || local.values.length < local.maxSelections);

  const close = () => setOpen(false);
  const toggle = () => {
    if (!local.disabled) {
      setOpen((current) => !current);
      setTouched(true);
    }
  };

  const toggleOption = (option: SelectControlOption) => {
    if (option.disabled || !canSelectMore()) {
      return;
    }

    const isSelected = local.values.includes(option.value);
    if (isSelected) {
      local.onChange(local.values.filter((v) => v !== option.value));
    } else {
      local.onChange([...local.values, option.value]);
    }
  };

  const removeOption = (optionValue: string, event: Event) => {
    event.stopPropagation();
    local.onChange(local.values.filter((v) => v !== optionValue));
  };

  const handleDocumentPointerDown = (event: PointerEvent) => {
    const target = event.target;
    if (target instanceof Node && controlRef?.contains(target)) {
      return;
    }
    close();
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (local.disabled) {
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
      class={classNames("form-select-wrapper", local.class)}
    >
      <Show when={local.label}>
        <label class="form-select-label" for={local.id}>
          {local.label}
          <Show when={local.required}>
            <span class="form-field-required" aria-label="required">
              *
            </span>
          </Show>
        </label>
      </Show>

      <div
        class={classNames(
          "form-select form-select--multi",
          open() && "form-select--open",
          hasError() && "form-select--error",
        )}
      >
        <button
          class="form-select-trigger"
          type="button"
          disabled={local.disabled}
          aria-label={local.ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open()}
          onClick={toggle}
          onKeyDown={handleKeyDown}
        >
          <div class="form-select-multi-values">
            <Show
              when={selectedOptions().length > 0}
              fallback={
                <span class="form-select-placeholder">{local.placeholder || "Select..."}</span>
              }
            >
              <For each={selectedOptions()}>
                {(option) => (
                  <span class="form-select-tag">
                    {option.label}
                    <button
                      type="button"
                      class="form-select-tag-remove"
                      onClick={(e) => removeOption(option.value, e)}
                      aria-label={`Remove ${option.label}`}
                    >
                      ×
                    </button>
                  </span>
                )}
              </For>
            </Show>
          </div>
          <ChevronDown class="form-select-caret" size={14} />
        </button>

        <Show when={open()}>
          <div class="form-select-menu" role="listbox" aria-label={local.ariaLabel || local.label}>
            <For each={local.options}>
              {(option) => {
                const isSelected = local.values.includes(option.value);
                const isDisabled = option.disabled || (!isSelected && !canSelectMore());
                return (
                  <button
                    class={classNames(
                      "form-select-option",
                      isSelected && "form-select-option--active",
                      isDisabled && "form-select-option--disabled",
                    )}
                    type="button"
                    disabled={isDisabled}
                    role="option"
                    aria-selected={isSelected}
                    title={option.title || option.label}
                    onClick={() => toggleOption(option)}
                  >
                    <span class="form-select-option-label">{option.label}</span>
                    <Show when={isSelected}>
                      <Check size={14} class="form-select-option-check" />
                    </Show>
                  </button>
                );
              }}
            </For>
          </div>
        </Show>
      </div>

      <Show when={hasError()}>
        <div
          id={`${local.id || local.name}-error`}
          class="form-select-error"
          role="alert"
        >
          {local.error || (local.required && "At least one option must be selected")}
        </div>
      </Show>

      <Show when={local.helper && !hasError()}>
        <div class="form-select-helper">
          {local.helper}
        </div>
      </Show>
    </div>
  );
}
