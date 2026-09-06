export const FAVICON_IDLE = "/dashboard-assets/favicon.ico";
export const FAVICON_ACTIVE = "/dashboard-assets/favicon-yellow.ico";

let currentFaviconState: boolean | null = null;

/**
 * Updates the browser tab favicon based on whether an agent is currently running.
 * - true (active/running): switches favicon to yellow (FAVICON_ACTIVE)
 * - false (idle/normal): switches favicon to green (FAVICON_IDLE)
 */
export function updateDashboardFavicon(active: boolean): void {
  if (typeof document === "undefined") {
    return;
  }

  if (currentFaviconState === active) {
    return;
  }

  currentFaviconState = active;
  const targetHref = active ? FAVICON_ACTIVE : FAVICON_IDLE;

  let link = (document.getElementById("app-favicon") ||
    document.querySelector("link[rel*='icon']")) as HTMLLinkElement | null;

  if (link) {
    if (link.getAttribute("href") !== targetHref) {
      link.setAttribute("href", targetHref);
      // Re-assign href to trigger favicon refresh in browsers like Chrome/Firefox
      link.href = targetHref;
    }
  } else {
    link = document.createElement("link");
    link.id = "app-favicon";
    link.rel = "icon";
    link.type = "image/x-icon";
    link.href = targetHref;
    document.head.appendChild(link);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("k41:thread-start-running", () => {
    updateDashboardFavicon(true);
  });
  window.addEventListener("k41:session-started", () => {
    updateDashboardFavicon(true);
  });
}
