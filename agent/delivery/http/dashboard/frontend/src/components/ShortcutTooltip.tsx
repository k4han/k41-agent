import { createEffect, createMemo, createSignal, createUniqueId, on, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import {
  bindingLabels, hasOpenDialog, SHORTCUT_LABELS,
  type PrimaryModifier, type ShortcutAction, type ShortcutOverrides,
} from "@/lib/keyboardShortcuts";

const triggerSelector = "[data-shortcut-action]";
const gap = 7;
const margin = 8;

function triggerFor(target: EventTarget | null) {
  return target instanceof Element ? target.closest<HTMLElement>(triggerSelector) : null;
}

function positionTooltip(trigger: HTMLElement, tooltip: HTMLElement) {
  const anchor = trigger.getBoundingClientRect();
  const { width, height } = tooltip.getBoundingClientRect();
  const viewport = document.documentElement;
  const centered = anchor.left + (anchor.width - width) / 2;
  const below = anchor.bottom + gap;
  const top = below + height + margin <= viewport.clientHeight ? below : anchor.top - gap - height;

  tooltip.style.left = `${Math.max(margin, Math.min(centered, viewport.clientWidth - width - margin))}px`;
  tooltip.style.top = `${Math.max(margin, top)}px`;
  tooltip.style.visibility = "visible";
}

export function ShortcutTooltip(props: { bindings: ShortcutOverrides; primary: PrimaryModifier; path: string }) {
  const id = createUniqueId();
  const [trigger, setTrigger] = createSignal<HTMLElement>();
  let target: HTMLElement | undefined;
  let tooltip: HTMLSpanElement | undefined;
  let timer: number | undefined;

  const action = () => trigger()?.dataset.shortcutAction as ShortcutAction | undefined;
  const label = () => {
    const element = trigger();
    const key = action();
    return element?.dataset.shortcutLabel || element?.getAttribute("aria-label")
      || element?.textContent?.trim() || (key && SHORTCUT_LABELS[key]);
  };
  const shortcut = createMemo(() => {
    const key = action();
    const binding = key && props.bindings[key]?.[0];
    return binding ? bindingLabels(binding, props.primary).join("+") : "";
  });

  const hide = () => {
    window.clearTimeout(timer);
    target = undefined;
    setTrigger(undefined);
  };
  const show = (element: HTMLElement | null, delay: number) => {
    if (!element || element === target || element.matches(':disabled, [aria-disabled="true"]')) return;
    hide();
    target = element;
    timer = window.setTimeout(() => {
      if (element.isConnected && !hasOpenDialog()) setTrigger(element);
      else hide();
    }, delay);
  };

  const pointerOver = (event: PointerEvent) => {
    const element = triggerFor(event.target);
    if (event.pointerType !== "touch" && element !== triggerFor(event.relatedTarget)) show(element, 260);
  };
  const pointerOut = (event: PointerEvent) => {
    if (target && triggerFor(event.target) === target && triggerFor(event.relatedTarget) !== target
      && !target.matches(":focus-visible")) hide();
  };
  const focusIn = (event: FocusEvent) => {
    const element = triggerFor(event.target);
    if (element?.matches(":focus-visible")) show(element, 0);
  };
  const focusOut = (event: FocusEvent) => {
    if (triggerFor(event.target) === target) hide();
  };

  createEffect(on(() => props.path, hide, { defer: true }));
  createEffect(on(trigger, (element) => {
    if (!element) return;
    const describedBy = element.getAttribute("aria-describedby");
    element.setAttribute("aria-describedby", [describedBy, id].filter(Boolean).join(" "));
    onCleanup(() => {
      if (describedBy) element.setAttribute("aria-describedby", describedBy);
      else element.removeAttribute("aria-describedby");
    });
  }));
  createEffect(on([trigger, shortcut], ([element]) => {
    if (element) queueMicrotask(() => {
      if (trigger() === element && tooltip) positionTooltip(element, tooltip);
    });
  }));

  onMount(() => {
    document.addEventListener("pointerover", pointerOver);
    document.addEventListener("pointerout", pointerOut);
    document.addEventListener("focusin", focusIn);
    document.addEventListener("focusout", focusOut);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", hide, true);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
  });
  onCleanup(() => {
    hide();
    document.removeEventListener("pointerover", pointerOver);
    document.removeEventListener("pointerout", pointerOut);
    document.removeEventListener("focusin", focusIn);
    document.removeEventListener("focusout", focusOut);
    document.removeEventListener("pointerdown", hide, true);
    document.removeEventListener("keydown", hide, true);
    window.removeEventListener("scroll", hide, true);
    window.removeEventListener("resize", hide);
  });

  return (
    <Show when={trigger()} keyed>
      <Portal>
        <span class="shortcut-tooltip" ref={tooltip} id={id} role="tooltip">
          <span>{label()}</span>
          <Show when={shortcut()}>{(text) => <kbd>{text()}</kbd>}</Show>
        </span>
      </Portal>
    </Show>
  );
}
