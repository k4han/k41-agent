import { createMemo, For, JSX, Show, splitProps } from "solid-js";
import { Loader2, Check, AlertCircle, RefreshCw } from "lucide-solid";

import { classNames } from "@/lib/utils";

export type FormLoadingProps = {
  loading?: boolean;
  size?: "sm" | "md" | "lg";
  text?: string;
  class?: string;
};

export function FormLoading(props: FormLoadingProps) {
  const [local] = splitProps(props, ["loading", "size", "text", "class"]);

  const sizeClass = createMemo(() => {
    switch (local.size) {
      case "sm":
        return "form-loading--sm";
      case "lg":
        return "form-loading--lg";
      default:
        return "form-loading--md";
    }
  });

  return (
    <Show when={local.loading}>
      <div class={classNames("form-loading", sizeClass(), local.class)}>
        <Loader2 class="form-loading-spinner" size={local.size === "sm" ? 16 : local.size === "lg" ? 24 : 20} />
        <Show when={local.text}>
          <span class="form-loading-text">{local.text}</span>
        </Show>
      </div>
    </Show>
  );
}

export type FormButtonProps = {
  loading?: boolean;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  loadingText?: string;
  children: JSX.Element;
  class?: string;
  onClick?: () => void;
  type?: "button" | "submit" | "reset";
};

export function FormButton(props: FormButtonProps) {
  const [local, others] = splitProps(props, [
    "loading",
    "disabled",
    "variant",
    "size",
    "loadingText",
    "children",
    "class",
    "onClick",
    "type",
  ]);

  const isDisabled = createMemo(() => local.disabled || local.loading);
  const variantClass = createMemo(() => {
    switch (local.variant) {
      case "primary":
        return "form-button--primary";
      case "secondary":
        return "form-button--secondary";
      case "danger":
        return "form-button--danger";
      case "ghost":
        return "form-button--ghost";
      default:
        return "form-button--primary";
    }
  });

  const sizeClass = createMemo(() => {
    switch (local.size) {
      case "sm":
        return "form-button--sm";
      case "lg":
        return "form-button--lg";
      default:
        return "form-button--md";
    }
  });

  return (
    <button
      {...others}
      type={local.type || "button"}
      disabled={isDisabled()}
      class={classNames(
        "form-button",
        variantClass(),
        sizeClass(),
        local.loading && "form-button--loading",
        local.class,
      )}
      onClick={local.onClick}
    >
      <Show when={local.loading}>
        <Loader2 class="form-button-spinner" size={local.size === "sm" ? 14 : local.size === "lg" ? 18 : 16} />
      </Show>
      <span class="form-button-content">
        {local.loading && local.loadingText ? local.loadingText : local.children}
      </span>
    </button>
  );
}

export type FormStateProps = {
  state: "idle" | "loading" | "success" | "error";
  loadingText?: string;
  successText?: string;
  errorText?: string;
  onRetry?: () => void;
  class?: string;
};

export function FormState(props: FormStateProps) {
  const getIcon = () => {
    switch (props.state) {
      case "loading":
        return <Loader2 size={20} class="form-state-icon form-state-icon--spinning" />;
      case "success":
        return <Check size={20} class="form-state-icon form-state-icon--success" />;
      case "error":
        return <AlertCircle size={20} class="form-state-icon form-state-icon--error" />;
      default:
        return null;
    }
  };

  const getText = () => {
    switch (props.state) {
      case "loading":
        return props.loadingText || "Loading...";
      case "success":
        return props.successText || "Success!";
      case "error":
        return props.errorText || "Something went wrong";
      default:
        return "";
    }
  };

  const getStateClass = () => {
    switch (props.state) {
      case "loading":
        return "form-state--loading";
      case "success":
        return "form-state--success";
      case "error":
        return "form-state--error";
      default:
        return "form-state--idle";
    }
  };

  return (
    <Show when={props.state !== "idle"}>
      <div class={classNames("form-state", getStateClass(), props.class)}>
        {getIcon()}
        <span class="form-state-text">{getText()}</span>
        <Show when={props.state === "error" && props.onRetry}>
          <button
            type="button"
            class="form-state-retry"
            onClick={props.onRetry}
            aria-label="Retry"
          >
            <RefreshCw size={14} />
            Retry
          </button>
        </Show>
      </div>
    </Show>
  );
}

export type FormFieldLoadingProps = {
  loading?: boolean;
  class?: string;
  children: JSX.Element;
};

export function FormFieldLoading(props: FormFieldLoadingProps) {
  return (
    <div class={classNames("form-field-loading-wrapper", props.class)}>
      <div class="form-field-loading-content">
        {props.children}
      </div>
      <Show when={props.loading}>
        <div class="form-field-loading-overlay">
          <div class="form-field-loading-spinner">
            <Loader2 size={16} class="form-field-loading-icon" />
          </div>
        </div>
      </Show>
    </div>
  );
}

export type FormSkeletonProps = {
  lines?: number;
  class?: string;
};

export function FormSkeleton(props: FormSkeletonProps) {
  const lineCount = () => props.lines || 3;

  return (
    <div class={classNames("form-skeleton", props.class)}>
      <For each={Array.from({ length: lineCount() })}>
        {() => (
          <div class="form-skeleton-line" />
        )}
      </For>
    </div>
  );
}

export type FormInputSkeletonProps = {
  class?: string;
  label?: boolean;
  helper?: boolean;
};

export function FormInputSkeleton(props: FormInputSkeletonProps) {
  return (
    <div class={classNames("form-input-skeleton", props.class)}>
      <Show when={props.label}>
        <div class="form-input-skeleton-label" />
      </Show>
      <div class="form-input-skeleton-input" />
      <Show when={props.helper}>
        <div class="form-input-skeleton-helper" />
      </Show>
    </div>
  );
}
