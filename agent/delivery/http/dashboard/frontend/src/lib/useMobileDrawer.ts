import { useLocation } from "@solidjs/router";
import { createEffect, createSignal, onCleanup, onMount } from "solid-js";
import { MOBILE_MEDIA_QUERY } from "@/lib/uiConstants";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { createMediaQuery } from "@/lib/useMediaQuery";

export interface UseMobileDrawerOptions {
  sidebarId: string;
  onClose?: () => void;
}

export interface UseMobileDrawerReturn {
  isMobileViewport: () => boolean;
  mobileDrawerOpen: () => boolean;
  setMobileDrawerOpen: (open: boolean) => void;
  closeMobileDrawer: () => void;
  handleAppLayoutClick: (event: MouseEvent) => void;
  handleKeydown: (event: KeyboardEvent) => void;
}

export function useMobileDrawer(options: UseMobileDrawerOptions): UseMobileDrawerReturn {
  const location = useLocation();
  const [mobileDrawerOpen, setMobileDrawerOpen] = createSignal(false);
  const isMobileViewport = createMediaQuery(MOBILE_MEDIA_QUERY);

  useBodyScrollLock(() => isMobileViewport() && mobileDrawerOpen());

  createEffect(() => {
    if (!isMobileViewport()) {
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
    if (isMobileViewport() && mobileDrawerOpen()) {
      setMobileDrawerOpen(false);
      options.onClose?.();
    }
  });

  let orientationDisposer: (() => void) | null = null;
  onMount(() => {
    const handleOrientationChange = () => {
      if (isMobileViewport() && mobileDrawerOpen()) {
        setMobileDrawerOpen(false);
      }
    };
    window.addEventListener("orientationchange", handleOrientationChange);
    orientationDisposer = () => window.removeEventListener("orientationchange", handleOrientationChange);
  });
  onCleanup(() => orientationDisposer?.());

  let focusTrapElement: HTMLElement | null = null;
  let previousActiveElement: HTMLElement | null = null;
  let focusTrapCleanup: (() => void) | null = null;

  const trapFocus = (element: HTMLElement) => {
    focusTrapElement = element;
    previousActiveElement = document.activeElement as HTMLElement;
    const focusableElements = element.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;

      if (event.shiftKey) {
        if (document.activeElement === firstElement) {
          event.preventDefault();
          lastElement?.focus();
        }
      } else {
        if (document.activeElement === lastElement) {
          event.preventDefault();
          firstElement?.focus();
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
    if (isMobileViewport() && mobileDrawerOpen()) {
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
      setMobileDrawerOpen(false);
      options.onClose?.();
    }
  };

  const handleKeydown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && mobileDrawerOpen()) {
      setMobileDrawerOpen(false);
      options.onClose?.();
    }
  };

  const closeMobileDrawer = () => {
    setMobileDrawerOpen(false);
    options.onClose?.();
  };

  return {
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer,
    handleAppLayoutClick,
    handleKeydown,
  };
}
