import { createMemo, JSX, Show, splitProps } from "solid-js";
import { Loader2 } from "lucide-solid";
import { classNames } from "@/lib/utils";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "default";
export type ButtonSize = "sm" | "md" | "lg";

export type ButtonProps = Omit<JSX.ButtonHTMLAttributes<HTMLButtonElement>, "size"> & {
  loading?: boolean;
  variant?: ButtonVariant;
  size?: ButtonSize;
  loadingText?: string;
  icon?: JSX.Element;
};

export function Button(props: ButtonProps) {
  const [local, others] = splitProps(props, [
    "loading",
    "disabled",
    "variant",
    "size",
    "loadingText",
    "icon",
    "children",
    "class",
    "onClick",
    "type",
  ]);

  const isDisabled = createMemo(() => local.disabled || local.loading);
  const variantClass = createMemo(() => {
    switch (local.variant) {
      case "primary":
        return "btn-primary";
      case "danger":
        return "btn-danger";
      case "ghost":
        return "btn-ghost";
      case "secondary":
        return "btn-secondary";
      default:
        return "";
    }
  });

  const sizeClass = createMemo(() => {
    switch (local.size) {
      case "sm":
        return "btn-sm";
      case "lg":
        return "btn-lg";
      default:
        return "";
    }
  });

  return (
    <button
      {...others}
      type={local.type || "button"}
      disabled={isDisabled()}
      aria-busy={local.loading ? "true" : undefined}
      class={classNames(
        "btn",
        variantClass(),
        sizeClass(),
        local.loading && "is-loading",
        local.class,
      )}
      onClick={local.onClick}
    >
      <Show when={local.loading} fallback={local.icon}>
        <Loader2 class="spin-icon" size={local.size === "sm" ? 14 : 16} />
      </Show>
      <Show when={local.loading && local.loadingText} fallback={local.children}>
        <span>{local.loadingText}</span>
      </Show>
    </button>
  );
}
