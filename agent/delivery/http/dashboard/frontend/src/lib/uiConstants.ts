export const STORAGE_KEYS = {
  SIDEBAR_COLLAPSED: "k41-dashboard-sidebar",
  HISTORY_PANEL: "k41-dashboard-history",
  WORKSPACE_FILTER: "k41-dashboard-workspace-filter",
  THEME: "k41-dashboard-theme",
} as const;

export const HISTORY_PAGE_SIZE = 20;
export const HISTORY_MENU_MIN_SPACE_PX = 78;

export const TASK_POLL_INTERVAL_MS = 5_000;
export const TASK_PAGE_SIZE = 20;

export const ACTIVE_TASK_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "running",
]);

export const SSE_RECONNECT_DELAY_MS = 3_000;

export const THEME_OPTIONS = {
  SYSTEM: "system",
  LIGHT: "light",
  DARK: "dark",
} as const;
export type ThemeOption = (typeof THEME_OPTIONS)[keyof typeof THEME_OPTIONS];

export const RESTART_REQUIRED_NOTICE = "Restart required to apply bootstrap changes.";

export const MOBILE_MAX_PX = 640;
export const MOBILE_MEDIA_QUERY = `(max-width: ${MOBILE_MAX_PX}px)`;

export const K41_LOGO_LETTER = "K";

export const UI_COLORS = {
  SUCCESS: "#10b981",
  WARNING: "#f59e0b",
  DANGER: "#ef4444",
  INFO: "#60a5fa",
  ACCENT: "#6366f1",
  PRIMARY: "#6366f1",
  PRIMARY_LIGHT: "#0076ff",
  VIOLET: "#8b5cf6",
  PURPLE: "#a855f7",
  PURPLE_BRIGHT: "#c084fc",
} as const;
