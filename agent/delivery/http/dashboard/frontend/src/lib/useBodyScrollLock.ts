import { createEffect, onCleanup } from "solid-js";

let lockCount = 0;
const savedBodyOverflowStack: string[] = [];
const savedBodyPaddingRightStack: string[] = [];
const savedHtmlOverflowStack: string[] = [];

function applyLock() {
  if (typeof document === "undefined") {
    return;
  }
  const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
  savedBodyOverflowStack.push(document.body.style.overflow);
  savedBodyPaddingRightStack.push(document.body.style.paddingRight);
  savedHtmlOverflowStack.push(document.documentElement.style.overflow);
  document.body.style.overflow = "hidden";
  // iOS Safari often scrolls the documentElement instead of the body; lock
  // both so background content stays put while a drawer/modal is open.
  document.documentElement.style.overflow = "hidden";
  if (scrollbarWidth > 0) {
    document.body.style.paddingRight = `${scrollbarWidth}px`;
  }
}

function releaseLock() {
  if (typeof document === "undefined") {
    return;
  }
  document.body.style.overflow = savedBodyOverflowStack.pop() ?? "";
  document.body.style.paddingRight = savedBodyPaddingRightStack.pop() ?? "";
  document.documentElement.style.overflow = savedHtmlOverflowStack.pop() ?? "";
}

export function useBodyScrollLock(active: () => boolean) {
  let locked = false;

  createEffect(() => {
    const next = active();
    if (next === locked) {
      return;
    }
    locked = next;
    if (next) {
      if (lockCount === 0) {
        applyLock();
      }
      lockCount += 1;
    } else if (lockCount > 0) {
      lockCount -= 1;
      if (lockCount === 0) {
        releaseLock();
      }
    }
  });

  onCleanup(() => {
    if (locked) {
      locked = false;
      if (lockCount > 0) {
        lockCount -= 1;
        if (lockCount === 0) {
          releaseLock();
        }
      }
    }
  });
}
