import { createEffect, createMemo, createSignal, For, JSX, onCleanup, onMount, Show, splitProps } from "solid-js";
import { Portal } from "solid-js/web";
import { ChevronDown, Check, AlertCircle, Search } from "lucide-solid";

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
  const [filter, setFilter] = createSignal("");
  const [menuPos, setMenuPos] = createSignal({ top: 0, left: 0, width: 0, maxHeight: 240 });
  let controlRef: HTMLDivElement | undefined;
  let filterInputRef: HTMLInputElement | undefined;
  let menuRef: HTMLDivElement | undefined;

  const selectedOption = createMemo(() =>
    local.options.find((option) => option.value === local.value),
  );
  const selectedLabel = createMemo(() => selectedOption()?.label || local.placeholder || "Select...");
  const hasError = createMemo(() => Boolean(local.error || (local.required && touched() && !local.value)));
  const isValid = createMemo(() => !hasError() && touched() && local.value);
  const isSearchable = createMemo(() => local.options.length > 20);
  const filteredOptions = createMemo(() => {
    if (!isSearchable()) return local.options;
    const q = filter().trim().toLowerCase();
    if (!q) return local.options;
    return local.options.filter(
      (opt) => opt.label.toLowerCase().includes(q) || opt.value.toLowerCase().includes(q),
    );
  });

  const updateMenuPosition = () => {
    if (!controlRef || !open()) return;
    const rect = controlRef.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;
    const gap = 4;
    const defaultMax = 240;
    const spaceBelow = viewportHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    let top = rect.bottom + gap;
    let maxHeight = defaultMax;
    // Flip above if not enough space below and more space above
    if (spaceBelow < 140 && spaceAbove > spaceBelow) {
      maxHeight = Math.min(defaultMax, Math.max(120, spaceAbove - gap));
      top = rect.top - maxHeight - gap;
      // Ensure not off-screen top
      if (top < 8) {
        top = 8;
        maxHeight = rect.top - 16;
      }
    } else {
      maxHeight = Math.min(defaultMax, Math.max(120, spaceBelow - gap));
    }
    // Horizontal sizing: searchable lists (timezone) need wider menu
    const isLarge = isSearchable();
    const desiredWidth = isLarge ? Math.max(rect.width, 440) : rect.width;
    const maxAllowedWidth = viewportWidth - 16;
    const width = Math.min(desiredWidth, maxAllowedWidth);
    let left = rect.left;
    // Keep right edge aligned if we expanded width beyond trigger
    if (isLarge && width > rect.width) {
      // Prefer left-aligned but ensure it stays inside viewport;
      // if control is near right edge, shift left to keep width visible
    }
    if (left + width > viewportWidth - 8) {
      left = Math.max(8, viewportWidth - width - 8);
    }
    setMenuPos({ top, left, width, maxHeight });
  };

  createEffect(() => {
    if (!open()) {
      setFilter("");
    } else {
      // Focus filter input when menu opens and update position
      setTimeout(() => {
        filterInputRef?.focus();
        updateMenuPosition();
      }, 0);
    }
  });

  // Keep position synced on scroll/resize while open
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
    if (target instanceof Node) {
      if (controlRef?.contains(target)) return;
      if (menuRef?.contains(target)) return;
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
          <Portal>
            <div
              ref={menuRef}
              class="form-select-menu form-select-menu--portal"
              role="listbox"
              aria-label={local.ariaLabel || local.label}
              style={{
                top: `${menuPos().top}px`,
                left: `${menuPos().left}px`,
                width: `${menuPos().width}px`,
                "max-height": `${menuPos().maxHeight}px`,
              }}
            >
              <Show when={isSearchable()}>
                <div class="form-select-search">
                  <Search size={13} class="form-select-search-icon" />
                  <input
                    ref={filterInputRef}
                    class="form-select-search-input"
                    type="text"
                    placeholder="Search..."
                    value={filter()}
                    onInput={(e) => setFilter(e.currentTarget.value)}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  />
                </div>
              </Show>
              <Show
                when={filteredOptions().length > 0}
                fallback={<div class="form-select-empty">No results</div>}
              >
                <For each={filteredOptions()}>
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
              </Show>
            </div>
          </Portal>
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
