import { createMemo, JSX, Show, splitProps } from "solid-js";
import { AlertCircle, HelpCircle, Info } from "lucide-solid";

import { classNames } from "@/lib/utils";

export type FormFieldProps = {
  label: string;
  error?: string;
  helper?: string;
  required?: boolean;
  class?: string;
  labelClass?: string;
  children: JSX.Element;
};

export function FormField(props: FormFieldProps) {
  const [local, others] = splitProps(props, [
    "label",
    "error",
    "helper",
    "required",
    "class",
    "labelClass",
    "children",
  ]);

  const hasError = createMemo(() => Boolean(local.error));
  const hasHelper = createMemo(() => Boolean(local.helper));

  return (
    <div class={classNames("form-field", local.class, hasError() && "form-field--error")}>
      <Show when={local.label}>
        <label class={classNames("form-field-label", local.labelClass)}>
          {local.label}
          <Show when={local.required}>
            <span class="form-field-required" aria-label="required">
              *
            </span>
          </Show>
        </label>
      </Show>
      <div class="form-field-input">{local.children}</div>
      <Show when={hasError()}>
        <div class="form-field-error" role="alert">
          <AlertCircle size={12} />
          <span>{local.error}</span>
        </div>
      </Show>
      <Show when={hasHelper() && !hasError()}>
        <div class="form-field-helper">
          <HelpCircle size={12} />
          <span>{local.helper}</span>
        </div>
      </Show>
    </div>
  );
}

export type FormFieldGroupProps = {
  title?: string;
  description?: string;
  class?: string;
  children: JSX.Element;
};

export function FormFieldGroup(props: FormFieldGroupProps) {
  return (
    <div class={classNames("form-field-group", props.class)}>
      <Show when={props.title}>
        <div class="form-field-group-header">
          <h3 class="form-field-group-title">{props.title}</h3>
          <Show when={props.description}>
            <p class="form-field-group-description">{props.description}</p>
          </Show>
        </div>
      </Show>
      <div class="form-field-group-body">{props.children}</div>
    </div>
  );
}

export type FormFieldInlineProps = {
  label: string;
  error?: string;
  helper?: string;
  required?: boolean;
  class?: string;
  children: JSX.Element;
};

export function FormFieldInline(props: FormFieldInlineProps) {
  const [local, others] = splitProps(props, [
    "label",
    "error",
    "helper",
    "required",
    "class",
    "children",
  ]);

  const hasError = createMemo(() => Boolean(local.error));
  const hasHelper = createMemo(() => Boolean(local.helper));

  return (
    <div class={classNames("form-field-inline", local.class, hasError() && "form-field--error")}>
      <label class="form-field-inline-label">
        {local.label}
        <Show when={local.required}>
          <span class="form-field-required" aria-label="required">
            *
          </span>
        </Show>
      </label>
      <div class="form-field-inline-content">
        <div class="form-field-inline-input">{local.children}</div>
        <Show when={hasError()}>
          <div class="form-field-error form-field-error--inline" role="alert">
            <AlertCircle size={12} />
            <span>{local.error}</span>
          </div>
        </Show>
        <Show when={hasHelper() && !hasError()}>
          <div class="form-field-helper form-field-helper--inline">
            <HelpCircle size={12} />
            <span>{local.helper}</span>
          </div>
        </Show>
      </div>
    </div>
  );
}
