import {
  JSX,
  Show,
  createEffect,
  createUniqueId,
  onCleanup,
} from "solid-js";
import { Portal } from "solid-js/web";
import { X } from "lucide-solid";

export type DialogSize = "sm" | "md" | "lg" | "xl" | "full";
export type DialogIconVariant = "danger" | "warning" | "primary" | "success" | "default";

export interface DialogProps {
  open: boolean;
  title: string | JSX.Element;
  subtitle?: string | JSX.Element;
  icon?: JSX.Element;
  iconVariant?: DialogIconVariant;
  size?: DialogSize;
  wide?: boolean;
  class?: string;
  bodyClass?: string;
  headerClass?: string;
  footerClass?: string;
  children: JSX.Element;
  footer?: JSX.Element;
  showCloseButton?: boolean;
  closeOnBackdrop?: boolean;
  closeOnEscape?: boolean;
  onClose: () => void;
}

// Track active dialogs count to properly manage body scroll locking with nested dialogs
let activeDialogsCount = 0;
let savedBodyOverflow = "";

function acquireBodyScrollLock() {
  if (typeof document === "undefined") return;
  if (activeDialogsCount === 0) {
    savedBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  activeDialogsCount++;
}

function releaseBodyScrollLock() {
  if (typeof document === "undefined") return;
  activeDialogsCount = Math.max(0, activeDialogsCount - 1);
  if (activeDialogsCount === 0) {
    document.body.style.overflow = savedBodyOverflow;
  }
}

export function Dialog(props: DialogProps) {
  let dialogRef: HTMLElement | undefined;
  let previousFocusedElement: HTMLElement | null = null;
  let isBackdropTarget = false;

  const titleId = createUniqueId();
  const descId = createUniqueId();

  // Manage body scroll lock and previous focus restoration
  createEffect(() => {
    if (props.open) {
      acquireBodyScrollLock();
      if (typeof document !== "undefined") {
        previousFocusedElement = document.activeElement as HTMLElement | null;
      }
    } else {
      releaseBodyScrollLock();
      if (previousFocusedElement && typeof previousFocusedElement.focus === "function") {
        previousFocusedElement.focus();
        previousFocusedElement = null;
      }
    }
  });

  onCleanup(() => {
    if (props.open) {
      releaseBodyScrollLock();
    }
  });

  // Escape key and Focus Trap listener
  createEffect(() => {
    if (!props.open || typeof window === "undefined") return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (props.closeOnEscape ?? true)) {
        event.preventDefault();
        event.stopPropagation();
        props.onClose();
        return;
      }

      if (event.key === "Tab" && dialogRef) {
        const focusableElements = dialogRef.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );

        if (!focusableElements || focusableElements.length === 0) {
          event.preventDefault();
          return;
        }

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (event.shiftKey) {
          if (document.activeElement === firstElement) {
            event.preventDefault();
            lastElement.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            event.preventDefault();
            firstElement.focus();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    onCleanup(() => window.removeEventListener("keydown", handleKeyDown));
  });

  // Focus the first interactive element or close button upon open
  createEffect(() => {
    if (props.open && dialogRef) {
      const timer = setTimeout(() => {
        if (!dialogRef) return;
        const initialFocus = dialogRef.querySelector<HTMLElement>(
          'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [autofocus], .dialog-close-btn, button:not([disabled])'
        );
        initialFocus?.focus();
      }, 40);
      onCleanup(() => clearTimeout(timer));
    }
  });

  const resolvedSizeClass = () => {
    if (props.size) return `dialog-${props.size}`;
    if (props.wide) return "dialog-xl dialog-wide";
    return "dialog-md";
  };

  const handleBackdropMouseDown = (event: MouseEvent) => {
    isBackdropTarget = event.target === event.currentTarget;
  };

  const handleBackdropMouseUp = (event: MouseEvent) => {
    if (isBackdropTarget && event.target === event.currentTarget && (props.closeOnBackdrop ?? true)) {
      props.onClose();
    }
    isBackdropTarget = false;
  };

  return (
    <Show when={props.open}>
      <Portal>
        <div
          class="dialog-backdrop"
          onMouseDown={handleBackdropMouseDown}
          onMouseUp={handleBackdropMouseUp}
        >
          <section
            ref={(el) => (dialogRef = el)}
            class={`dialog ${resolvedSizeClass()} ${props.class || ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={props.subtitle ? descId : undefined}
            onClick={(e) => e.stopPropagation()}
          >
            <header class={`dialog-header ${props.headerClass || ""}`}>
              <div class="dialog-header-leading">
                <Show when={props.icon}>
                  <div class={`dialog-header-icon ${props.iconVariant ? `variant-${props.iconVariant}` : ""}`}>
                    {props.icon}
                  </div>
                </Show>
                <div class="dialog-header-text">
                  <div id={titleId} class="dialog-title panel-title">
                    {props.title}
                  </div>
                  <Show when={props.subtitle}>
                    <div id={descId} class="dialog-subtitle">
                      {props.subtitle}
                    </div>
                  </Show>
                </div>
              </div>
              <Show when={props.showCloseButton ?? true}>
                <button
                  class="dialog-close-btn"
                  type="button"
                  onClick={props.onClose}
                  aria-label="Close dialog"
                  title="Close"
                >
                  <X size={15} />
                </button>
              </Show>
            </header>
            <div class={`dialog-body ${props.bodyClass || ""}`}>{props.children}</div>
            <Show when={props.footer}>
              <footer class={`dialog-footer ${props.footerClass || ""}`}>{props.footer}</footer>
            </Show>
          </section>
        </div>
      </Portal>
    </Show>
  );
}

