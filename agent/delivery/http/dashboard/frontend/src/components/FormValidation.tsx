import { createMemo, For, JSX, Show } from "solid-js";
import { AlertCircle, CheckCircle, Info, AlertTriangle } from "lucide-solid";

import { classNames } from "@/lib/utils";

export type ValidationMessage = {
  type: "error" | "warning" | "info" | "success";
  message: string;
  field?: string;
};

export type FormValidationProps = {
  messages: ValidationMessage[];
  class?: string;
  showSummary?: boolean;
  onDismiss?: (index: number) => void;
};

export function FormValidation(props: FormValidationProps) {
  const hasMessages = createMemo(() => props.messages.length > 0);
  const errorCount = createMemo(() =>
    props.messages.filter((m) => m.type === "error").length,
  );
  const warningCount = createMemo(() =>
    props.messages.filter((m) => m.type === "warning").length,
  );

  const getIcon = (type: ValidationMessage["type"]) => {
    switch (type) {
      case "error":
        return <AlertCircle size={14} />;
      case "warning":
        return <AlertTriangle size={14} />;
      case "info":
        return <Info size={14} />;
      case "success":
        return <CheckCircle size={14} />;
      default:
        return null;
    }
  };

  const getTypeClass = (type: ValidationMessage["type"]) => {
    switch (type) {
      case "error":
        return "form-validation-message--error";
      case "warning":
        return "form-validation-message--warning";
      case "info":
        return "form-validation-message--info";
      case "success":
        return "form-validation-message--success";
      default:
        return "";
    }
  };

  return (
    <Show when={hasMessages()}>
      <div class={classNames("form-validation", props.class)}>
        <Show when={props.showSummary}>
          <div class="form-validation-summary">
            <Show when={errorCount() > 0}>
              <span class="form-validation-summary-count form-validation-summary-count--error">
                {errorCount()} error{errorCount() !== 1 ? "s" : ""}
              </span>
            </Show>
            <Show when={warningCount() > 0}>
              <span class="form-validation-summary-count form-validation-summary-count--warning">
                {warningCount()} warning{warningCount() !== 1 ? "s" : ""}
              </span>
            </Show>
          </div>
        </Show>

        <div class="form-validation-messages">
          <For each={props.messages}>
            {(message, index) => (
              <div class={classNames("form-validation-message", getTypeClass(message.type))}>
                <div class="form-validation-message-icon">
                  {getIcon(message.type)}
                </div>
                <div class="form-validation-message-content">
                  <Show when={message.field}>
                    <span class="form-validation-message-field">{message.field}</span>
                  </Show>
                  <span class="form-validation-message-text">{message.message}</span>
                </div>
                <Show when={props.onDismiss}>
                  <button
                    type="button"
                    class="form-validation-message-dismiss"
                    onClick={() => props.onDismiss?.(index())}
                    aria-label="Dismiss message"
                  >
                    ×
                  </button>
                </Show>
              </div>
            )}
          </For>
        </div>
      </div>
    </Show>
  );
}

export type FormFieldErrorProps = {
  error?: string;
  warning?: string;
  class?: string;
};

export function FormFieldError(props: FormFieldErrorProps) {
  return (
    <Show when={props.error || props.warning}>
      <div class={classNames(
        "form-field-error-inline",
        props.error && "form-field-error-inline--error",
        props.warning && "form-field-error-inline--warning",
        props.class
      )}>
        <Show when={props.error}>
          <AlertCircle size={12} class="form-field-error-icon" />
        </Show>
        <Show when={props.warning && !props.error}>
          <AlertTriangle size={12} class="form-field-error-icon" />
        </Show>
        <span class="form-field-error-text">
          {props.error || props.warning}
        </span>
      </div>
    </Show>
  );
}

export type FormSuccessProps = {
  message: string;
  show?: boolean;
  class?: string;
  onDismiss?: () => void;
};

export function FormSuccess(props: FormSuccessProps) {
  return (
    <Show when={props.show}>
      <div class={classNames("form-success", props.class)}>
        <CheckCircle size={14} class="form-success-icon" />
        <span class="form-success-message">{props.message}</span>
        <Show when={props.onDismiss}>
          <button
            type="button"
            class="form-success-dismiss"
            onClick={props.onDismiss}
            aria-label="Dismiss"
          >
            ×
          </button>
        </Show>
      </div>
    </Show>
  );
}

export type FormProgressProps = {
  steps: Array<{ label: string; completed: boolean; current?: boolean }>;
  class?: string;
};

export function FormProgress(props: FormProgressProps) {
  const currentStep = createMemo(() =>
    props.steps.findIndex((step) => step.current),
  );
  const completedSteps = createMemo(() =>
    props.steps.filter((step) => step.completed).length,
  );
  const progress = createMemo(() =>
    (completedSteps() / props.steps.length) * 100,
  );

  return (
    <div class={classNames("form-progress", props.class)}>
      <div class="form-progress-bar">
        <div
          class="form-progress-fill"
          style={{ width: `${progress()}%` }}
        />
      </div>
      <div class="form-progress-steps">
        <For each={props.steps}>
          {(step, index) => (
            <div
              class={classNames(
                "form-progress-step",
                step.completed && "form-progress-step--completed",
                step.current && "form-progress-step--current",
              )}
            >
              <div class="form-progress-step-indicator">
                <Show when={step.completed}>
                  <CheckCircle size={12} />
                </Show>
                <Show when={!step.completed}>
                  <span>{index() + 1}</span>
                </Show>
              </div>
              <span class="form-progress-step-label">{step.label}</span>
            </div>
          )}
        </For>
      </div>
    </div>
  );
}
