export type FloatingPlacement = "top" | "bottom";

export type FloatingRect = {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  placement: FloatingPlacement;
};

const GAP = 6;
const MIN_MENU_HEIGHT = 120;
const DEFAULT_MAX_HEIGHT = 260;
const VIEWPORT_MARGIN = 8;

export function computeFloatingPosition(
  trigger: HTMLElement,
  options?: {
    preferred?: FloatingPlacement;
    defaultMaxHeight?: number;
    minWidth?: number;
    align?: "left" | "right";
  },
): FloatingRect {
  const rect = trigger.getBoundingClientRect();
  const viewportHeight = window.innerHeight;
  const viewportWidth = window.innerWidth;
  const defaultMax = options?.defaultMaxHeight ?? DEFAULT_MAX_HEIGHT;
  const preferred = options?.preferred ?? "bottom";
  const spaceBelow = viewportHeight - rect.bottom - VIEWPORT_MARGIN;
  const spaceAbove = rect.top - VIEWPORT_MARGIN;

  let placement: FloatingPlacement = preferred;
  if (preferred === "bottom" && spaceBelow < 140 && spaceAbove > spaceBelow) {
    placement = "top";
  } else if (preferred === "top" && spaceAbove < 140 && spaceBelow > spaceAbove) {
    placement = "bottom";
  }

  let maxHeight = defaultMax;
  let top = rect.bottom + GAP;
  if (placement === "top") {
    maxHeight = Math.min(defaultMax, Math.max(MIN_MENU_HEIGHT, spaceAbove - GAP));
    top = rect.top - maxHeight - GAP;
    if (top < VIEWPORT_MARGIN) {
      top = VIEWPORT_MARGIN;
      maxHeight = Math.max(MIN_MENU_HEIGHT, rect.top - VIEWPORT_MARGIN * 2);
    }
  } else {
    maxHeight = Math.min(defaultMax, Math.max(MIN_MENU_HEIGHT, spaceBelow - GAP));
  }

  const minWidth = options?.minWidth ?? rect.width;
  let width = Math.max(rect.width, minWidth);
  const maxAllowedWidth = viewportWidth - VIEWPORT_MARGIN * 2;
  width = Math.min(width, maxAllowedWidth);

  let left = options?.align === "right" ? rect.right - width : rect.left;
  if (left + width > viewportWidth - VIEWPORT_MARGIN) {
    left = Math.max(VIEWPORT_MARGIN, viewportWidth - width - VIEWPORT_MARGIN);
  }
  if (left < VIEWPORT_MARGIN) {
    left = VIEWPORT_MARGIN;
  }

  return { top, left, width, maxHeight, placement };
}

export function floatingMenuStyle(rect: FloatingRect): Record<string, string> {
  return {
    top: `${Math.round(rect.top)}px`,
    left: `${Math.round(rect.left)}px`,
    width: `${Math.round(rect.width)}px`,
    "max-height": `${Math.round(rect.maxHeight)}px`,
  };
}
