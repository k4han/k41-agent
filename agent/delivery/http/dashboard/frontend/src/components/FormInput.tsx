import { createMemo, createSignal, JSX, onMount, Show, splitProps } from "solid-js";
import { Eye, EyeOff, Check, X } from "lucide-solid";

import { classNames } from "@/lib/utils";

export type ValidationRule = {
  validate: (value: string) => boolean | string;
  message: string;
};

export type FormInputProps = {
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password" | "email" | "url" | "number";
  placeholder?: string;
  disabled?: boolean;
  readonly?: boolean;
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  min?: number;
  max?: number;
  step?: number;
  validation?: ValidationRule[];
  validateOnBlur?: boolean;
  validateOnChange?: boolean;
  showTogglePassword?: boolean;
  showValidationStatus?: boolean;
  class?: string;
  name?: string;
  id?: string;
  ariaLabel?: string;
  autoComplete?: string;
  ref?: HTMLInputElement | ((el: HTMLInputElement) => void);
  onBlur?: (event: FocusEvent) => void;
  onFocus?: (event: FocusEvent) => void;
};

export function FormInput(props: FormInputProps) {
  const [local, others] = splitProps(props, [
    "value",
    "onChange",
    "type",
    "placeholder",
    "disabled",
    "readonly",
    "required",
    "minLength",
    "maxLength",
    "pattern",
    "min",
    "max",
    "step",
    "validation",
    "validateOnBlur",
    "validateOnChange",
    "showTogglePassword",
    "showValidationStatus",
    "class",
    "name",
    "id",
    "ariaLabel",
    "autoComplete",
    "ref",
    "onBlur",
    "onFocus",
  ]);

  const [showPassword, setShowPassword] = createSignal(false);
  const [touched, setTouched] = createSignal(false);
  const [error, setError] = createSignal("");

  const inputType = createMemo(() => {
    if (local.type === "password" && showPassword()) {
      return "text";
    }
    return local.type || "text";
  });

  const isValid = createMemo(() => {
    if (!touched() || error()) {
      return false;
    }
    return local.value.length > 0;
  });

  const validate = (value: string): string => {
    if (local.required && !value.trim()) {
      return "This field is required";
    }

    if (local.minLength && value.length < local.minLength) {
      return `Minimum length is ${local.minLength} characters`;
    }

    if (local.maxLength && value.length > local.maxLength) {
      return `Maximum length is ${local.maxLength} characters`;
    }

    if (local.pattern && !new RegExp(local.pattern).test(value)) {
      return "Invalid format";
    }

    if (local.type === "number" && value) {
      const num = Number(value);
      if (local.min !== undefined && num < local.min) {
        return `Minimum value is ${local.min}`;
      }
      if (local.max !== undefined && num > local.max) {
        return `Maximum value is ${local.max}`;
      }
    }

    if (local.validation) {
      for (const rule of local.validation) {
        const result = rule.validate(value);
        if (result !== true) {
          return typeof result === "string" ? result : rule.message;
        }
      }
    }

    return "";
  };

  const handleChange = (event: Event & { currentTarget: HTMLInputElement }) => {
    const newValue = event.currentTarget.value;
    local.onChange(newValue);

    if (local.validateOnChange && touched()) {
      setError(validate(newValue));
    }
  };

  const handleBlur = (event: FocusEvent) => {
    setTouched(true);
    setError(validate(local.value));

    if (local.onBlur) {
      local.onBlur(event);
    }
  };

  const handleFocus = (event: FocusEvent) => {
    if (local.onFocus) {
      local.onFocus(event);
    }
  };

  const togglePassword = () => {
    setShowPassword((prev) => !prev);
  };

  return (
    <div class={classNames("form-input-wrapper", local.class)}>
      <input
        {...others}
        type={inputType()}
        value={local.value}
        placeholder={local.placeholder}
        disabled={local.disabled}
        readonly={local.readonly}
        required={local.required}
        minLength={local.minLength}
        maxLength={local.maxLength}
        pattern={local.pattern}
        name={local.name}
        id={local.id}
        aria-label={local.ariaLabel}
        aria-invalid={Boolean(error())}
        aria-describedby={
          error() ? `${local.id || local.name}-error` : undefined
        }
        autocomplete={local.autoComplete}
        min={local.min}
        max={local.max}
        step={local.step}
        class={classNames(
          "input",
          error() && "input--error",
          isValid() && local.showValidationStatus && "input--valid",
        )}
        ref={(el) => {
          if (typeof local.ref === "function") {
            local.ref(el);
          } else if (local.ref) {
            (local as any).ref = el;
          }
        }}
        onInput={handleChange}
        onBlur={handleBlur}
        onFocus={handleFocus}
      />

      <div class="form-input-actions">
        <Show when={local.type === "password" && local.showTogglePassword}>
          <button
            type="button"
            class="form-input-action form-input-action--toggle"
            onClick={togglePassword}
            aria-label={showPassword() ? "Hide password" : "Show password"}
            disabled={local.disabled}
          >
            <Show when={showPassword()} fallback={<Eye size={14} />}>
              <EyeOff size={14} />
            </Show>
          </button>
        </Show>

        <Show when={local.showValidationStatus && touched()}>
          <Show when={error()}>
            <div class="form-input-status form-input-status--error">
              <X size={14} />
            </div>
          </Show>
          <Show when={isValid()}>
            <div class="form-input-status form-input-status--valid">
              <Check size={14} />
            </div>
          </Show>
        </Show>
      </div>

      <Show when={error()}>
        <div
          id={`${local.id || local.name}-error`}
          class="form-input-error"
          role="alert"
        >
          {error()}
        </div>
      </Show>
    </div>
  );
}

export type FormTextareaProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  readonly?: boolean;
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  rows?: number;
  resize?: "none" | "vertical" | "horizontal" | "both";
  validation?: ValidationRule[];
  validateOnBlur?: boolean;
  validateOnChange?: boolean;
  showValidationStatus?: boolean;
  class?: string;
  name?: string;
  id?: string;
  ariaLabel?: string;
  ref?: HTMLTextAreaElement | ((el: HTMLTextAreaElement) => void);
  onBlur?: (event: FocusEvent) => void;
  onFocus?: (event: FocusEvent) => void;
};

export function FormTextarea(props: FormTextareaProps) {
  const [local, others] = splitProps(props, [
    "value",
    "onChange",
    "placeholder",
    "disabled",
    "readonly",
    "required",
    "minLength",
    "maxLength",
    "rows",
    "resize",
    "validation",
    "validateOnBlur",
    "validateOnChange",
    "showValidationStatus",
    "class",
    "name",
    "id",
    "ariaLabel",
    "ref",
    "onBlur",
    "onFocus",
  ]);

  const [touched, setTouched] = createSignal(false);
  const [error, setError] = createSignal("");

  const isValid = createMemo(() => {
    if (!touched() || error()) {
      return false;
    }
    return local.value.length > 0;
  });

  const validate = (value: string): string => {
    if (local.required && !value.trim()) {
      return "This field is required";
    }

    if (local.minLength && value.length < local.minLength) {
      return `Minimum length is ${local.minLength} characters`;
    }

    if (local.maxLength && value.length > local.maxLength) {
      return `Maximum length is ${local.maxLength} characters`;
    }

    if (local.validation) {
      for (const rule of local.validation) {
        const result = rule.validate(value);
        if (result !== true) {
          return typeof result === "string" ? result : rule.message;
        }
      }
    }

    return "";
  };

  const handleChange = (event: Event & { currentTarget: HTMLTextAreaElement }) => {
    const newValue = event.currentTarget.value;
    local.onChange(newValue);

    if (local.validateOnChange && touched()) {
      setError(validate(newValue));
    }
  };

  const handleBlur = (event: FocusEvent) => {
    setTouched(true);
    setError(validate(local.value));

    if (local.onBlur) {
      local.onBlur(event);
    }
  };

  const handleFocus = (event: FocusEvent) => {
    if (local.onFocus) {
      local.onFocus(event);
    }
  };

  const resizeStyle = createMemo(() => {
    switch (local.resize) {
      case "none":
        return "resize: none";
      case "vertical":
        return "resize: vertical";
      case "horizontal":
        return "resize: horizontal";
      case "both":
        return "resize: both";
      default:
        return "resize: vertical";
    }
  });

  return (
    <div class={classNames("form-textarea-wrapper", local.class)}>
      <textarea
        {...others}
        value={local.value}
        placeholder={local.placeholder}
        disabled={local.disabled}
        readonly={local.readonly}
        required={local.required}
        minLength={local.minLength}
        maxLength={local.maxLength}
        rows={local.rows || 4}
        name={local.name}
        id={local.id}
        aria-label={local.ariaLabel}
        aria-invalid={Boolean(error())}
        aria-describedby={
          error() ? `${local.id || local.name}-error` : undefined
        }
        class={classNames(
          "textarea",
          error() && "textarea--error",
          isValid() && local.showValidationStatus && "textarea--valid",
        )}
        style={resizeStyle()}
        ref={(el) => {
          if (typeof local.ref === "function") {
            local.ref(el);
          } else if (local.ref) {
            (local as any).ref = el;
          }
        }}
        onInput={handleChange}
        onBlur={handleBlur}
        onFocus={handleFocus}
      />

      <Show when={local.showValidationStatus && touched()}>
        <div class="form-textarea-status">
          <Show when={error()}>
            <div class="form-textarea-status--error">
              <X size={14} />
            </div>
          </Show>
          <Show when={isValid()}>
            <div class="form-textarea-status--valid">
              <Check size={14} />
            </div>
          </Show>
        </div>
      </Show>

      <Show when={error()}>
        <div
          id={`${local.id || local.name}-error`}
          class="form-textarea-error"
          role="alert"
        >
          {error()}
        </div>
      </Show>
    </div>
  );
}
