import { createSignal, onCleanup, Show, type Accessor, type JSX } from "solid-js";
import { Check, Copy } from "lucide-solid";

import { useToast } from "@/components/Toast";
import { writeToClipboard } from "@/lib/utils";

export type CopyButtonState = {
  copied: Accessor<boolean>;
  copying: Accessor<boolean>;
  failed: Accessor<boolean>;
};

type CopyButtonProps = {
  value: string | (() => string);
  class?: string;
  title?: string;
  ariaLabel?: string;
  copiedTitle?: string;
  failedTitle?: string;
  successMessage?: string;
  failureMessage?: string;
  disabled?: boolean;
  showIcon?: boolean;
  iconSize?: number;
  iconClass?: string;
  iconPosition?: "start" | "end";
  resetDelayMs?: number;
  children?: JSX.Element | ((state: CopyButtonState) => JSX.Element);
  onCopied?: () => void;
  onCopyFailed?: (error: unknown) => void;
};

export function CopyButton(props: CopyButtonProps) {
  const [copying, setCopying] = createSignal(false);
  const [copied, setCopied] = createSignal(false);
  const [failed, setFailed] = createSignal(false);
  const { showToast } = useToast();
  let resetTimer: number | undefined;
  let generation = 0;

  const clearResetTimer = () => {
    if (resetTimer === undefined) {
      return;
    }
    window.clearTimeout(resetTimer);
    resetTimer = undefined;
  };

  const resetLater = (currentGeneration: number) => {
    clearResetTimer();
    resetTimer = window.setTimeout(() => {
      if (currentGeneration !== generation) {
        return;
      }
      setCopied(false);
      setFailed(false);
      resetTimer = undefined;
    }, props.resetDelayMs ?? 2400);
  };

  const textValue = () => (typeof props.value === "function" ? props.value() : props.value);
  const currentTitle = () => {
    if (copied()) {
      return props.copiedTitle || "Copied";
    }
    if (failed()) {
      return props.failedTitle || "Copy failed";
    }
    return props.title || props.ariaLabel || "Copy";
  };
  const currentAriaLabel = () => {
    if (copied()) {
      return props.copiedTitle || "Copied";
    }
    if (failed()) {
      return props.failedTitle || "Copy failed";
    }
    return props.ariaLabel || props.title || "Copy";
  };
  const state: CopyButtonState = { copied, copying, failed };
  const defaultIcon = () => (
    <Show
      when={copied()}
      fallback={<Copy size={props.iconSize ?? 14} class={props.iconClass} />}
    >
      <Check size={props.iconSize ?? 14} class={props.iconClass} />
    </Show>
  );
  const content = () => {
    if (typeof props.children === "function") {
      return props.children(state);
    }
    if (props.children) {
      if (!props.showIcon) {
        return props.children;
      }
      return props.iconPosition === "end" ? (
        <>
          {props.children}
          {defaultIcon()}
        </>
      ) : (
        <>
          {defaultIcon()}
          {props.children}
        </>
      );
    }
    return defaultIcon();
  };

  const copy = async () => {
    const value = textValue().trim();
    if (!value || copying()) {
      return;
    }

    const currentGeneration = (generation += 1);
    setCopying(true);
    setCopied(false);
    setFailed(false);

    try {
      await writeToClipboard(value);
      if (currentGeneration !== generation) {
        return;
      }
      setCopied(true);
      props.onCopied?.();
      if (props.successMessage) {
        showToast(props.successMessage);
      }
    } catch (error) {
      if (currentGeneration !== generation) {
        return;
      }
      setFailed(true);
      props.onCopyFailed?.(error);
      showToast(props.failureMessage || "Copy failed", "error");
    } finally {
      if (currentGeneration !== generation) {
        return;
      }
      setCopying(false);
      resetLater(currentGeneration);
    }
  };

  onCleanup(clearResetTimer);

  return (
    <button
      class={props.class || "btn btn-icon"}
      type="button"
      title={currentTitle()}
      aria-label={currentAriaLabel()}
      disabled={props.disabled || copying()}
      onClick={() => void copy()}
    >
      {content()}
    </button>
  );
}
