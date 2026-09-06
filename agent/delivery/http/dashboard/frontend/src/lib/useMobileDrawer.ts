import { useLocation } from "@solidjs/router";
import { createEffect, createSignal, onCleanup, onMount } from "solid-js";
import { DRAWER_MEDIA_QUERY } from "@/lib/uiConstants";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { createMediaQuery } from "@/lib/useMediaQuery";

let activeCloseDrawerHandler: (() => void) | null = null;

export function closeMobileDrawer(): void {
  activeCloseDrawerHandler?.();
}

export interface UseMobileDrawerOptions {
  sidebarId: string;
  breakpointQuery?: string;
  onClose?: () => void;
  onOpen?: () => void;
}

export interface UseMobileDrawerReturn {
  isDrawerActive: () => boolean;
  isMobileViewport: () => boolean;
  mobileDrawerOpen: () => boolean;
  setMobileDrawerOpen: (open: boolean) => void;
  closeMobileDrawer: () => void;
  openMobileDrawer: () => void;
  handleAppLayoutClick: (event: MouseEvent) => void;
  handleNavClick: (event: MouseEvent) => void;
  handleKeydown: (event: KeyboardEvent) => void;
}

export function useMobileDrawer(options: UseMobileDrawerOptions): UseMobileDrawerReturn {
  const location = useLocation();
  const [mobileDrawerOpen, setMobileDrawerOpen] = createSignal(false);
  const isDrawerActive = createMediaQuery(options.breakpointQuery ?? DRAWER_MEDIA_QUERY);
  const isMobileViewport = isDrawerActive;

  const closeDrawer = () => {
    setMobileDrawerOpen(false);
    options.onClose?.();
  };

  const openDrawer = () => {
    setMobileDrawerOpen(true);
    options.onOpen?.();
  };

  activeCloseDrawerHandler = closeDrawer;
  onCleanup(() => {
    if (activeCloseDrawerHandler === closeDrawer) {
      activeCloseDrawerHandler = null;
    }
  });

  useBodyScrollLock(() => isDrawerActive() && mobileDrawerOpen());

  createEffect(() => {
    if (!isDrawerActive()) {
      setMobileDrawerOpen(false);
    }
  });

  let lastPathname = location.pathname;
  createEffect(() => {
    const currentPathname = location.pathname;
    if (currentPathname === lastPathname) {
      return;
    }
    lastPathname = currentPathname;
    if (isDrawerActive() && mobileDrawerOpen()) {
      closeDrawer();
    }
  });

  let orientationDisposer: (() => void) | null = null;
  onMount(() => {
    const handleOrientationChange = () => {
      if (isDrawerActive() && mobileDrawerOpen()) {
        closeDrawer();
      }
    };
    window.addEventListener("orientationchange", handleOrientationChange);
    orientationDisposer = () => window.removeEventListener("orientationchange", handleOrientationChange);
  });
  onCleanup(() => orientationDisposer?.());

  let focusTrapElement: HTMLElement | null = null;
  let previousActiveElement: HTMLElement | null = null;
  let focusTrapCleanup: (() => void) | null = null;

  const isElementVisible = (el: HTMLElement): boolean => {
    if (el.offsetParent === null && window.getComputedStyle(el).position !== "fixed") {
      return false;
    }
    const style = window.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden";
  };

  const getFocusableElements = (container: HTMLElement): HTMLElement[] => {
    const elements = container.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    return Array.from(elements).filter(isElementVisible);
  };

  const trapFocus = (element: HTMLElement) => {
    focusTrapElement = element;
    previousActiveElement = document.activeElement as HTMLElement;

    const focusableElements = getFocusableElements(element);
    const firstElement = focusableElements[0];

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;

      const currentFocusable = getFocusableElements(element);
      if (currentFocusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = currentFocusable[0];
      const last = currentFocusable[currentFocusable.length - 1];

      if (event.shiftKey) {
        if (document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
      } else {
        if (document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };

    element.addEventListener("keydown", handleTab);
    firstElement?.focus();

    return () => {
      element.removeEventListener("keydown", handleTab);
    };
  };

  const releaseFocusTrap = () => {
    if (focusTrapElement) {
      focusTrapElement = null;
      previousActiveElement?.focus();
      previousActiveElement = null;
    }
  };

  createEffect(() => {
    if (isDrawerActive() && mobileDrawerOpen()) {
      const sidebar = document.getElementById(options.sidebarId);
      if (sidebar) {
        focusTrapCleanup = trapFocus(sidebar);
      }
    } else {
      focusTrapCleanup?.();
      focusTrapCleanup = null;
      releaseFocusTrap();
    }
  });

  onCleanup(() => {
    focusTrapCleanup?.();
    releaseFocusTrap();
  });

  const handleAppLayoutClick = (event: MouseEvent) => {
    if (mobileDrawerOpen() && event.target === event.currentTarget) {
      closeDrawer();
    }
  };

  const handleNavClick = (event: MouseEvent) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest("a") && isDrawerActive() && mobileDrawerOpen()) {
      closeDrawer();
    }
  };

  const handleKeydown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && mobileDrawerOpen()) {
      closeDrawer();
    }
  };

  return {
    isDrawerActive,
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer: closeDrawer,
    openMobileDrawer: openDrawer,
    handleAppLayoutClick,
    handleNavClick,
    handleKeydown,
  };
}
