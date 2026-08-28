export const HTML_PREVIEW_TOOL_NAME = "html_preview";

export type HtmlPreviewContent = {
  title: string;
  html: string;
};

/**
 * Extracts previewable HTML content from the `html_preview` tool call args.
 * Args are persisted with the transcript, so the preview survives reloads.
 */
export function htmlPreviewFromArgs(args: unknown): HtmlPreviewContent | null {
  if (typeof args !== "object" || args === null) {
    return null;
  }
  const record = args as Record<string, unknown>;
  const html = typeof record.html === "string" ? record.html : "";
  if (!html.trim()) {
    return null;
  }
  const title =
    typeof record.title === "string" && record.title.trim()
      ? record.title.trim()
      : "HTML Preview";
  return { title, html };
}

let cachedProbe: HTMLSpanElement | null = null;
let cachedProbeRaw: string | null = null;
let cachedProbeComputed: string | null = null;

/**
 * Resolves the dashboard `--fg` color into a normalized `rgb(...)` string so
 * the preview scrollbar colors can adapt to the current theme. Falls back to
 * a neutral gray when unavailable.
 */
function resolveForegroundBaseColor(): string {
  const fallback = "#888888";
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback;
  }
  if (!document.body) {
    return fallback;
  }
  const raw = window
    .getComputedStyle(document.documentElement)
    .getPropertyValue("--fg")
    .trim();
  if (!raw) {
    return fallback;
  }
  if (cachedProbeRaw === raw && cachedProbeComputed) {
    return cachedProbeComputed;
  }
  // Normalize any CSS color (hex, var(), ...) into `rgb(...)` by probing the
  // computed style, so the luminance check below always has parseable input.
  try {
    let probe = cachedProbe;
    if (!probe || !probe.isConnected) {
      probe = document.createElement("span");
      probe.style.display = "none";
      document.body.appendChild(probe);
      cachedProbe = probe;
    }
    probe.style.color = raw;
    const computed = window.getComputedStyle(probe).color;
    if (computed) {
      cachedProbeRaw = raw;
      cachedProbeComputed = computed;
      return computed;
    }
    return fallback;
  } catch {
    return fallback;
  }
}

function parseRgbColor(color: string): { r: number; g: number; b: number } | null {
  const match = /rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/.exec(color);
  if (!match) {
    return null;
  }
  return { r: Number(match[1]), g: Number(match[2]), b: Number(match[3]) };
}

/**
 * Mirrors the dashboard scrollbar geometry from `styles/base.css`. The thumb
 * color adapts to the theme: a light foreground (dark theme) would be
 * invisible on the white preview background, so a neutral dark thumb is used
 * there. Explicit rgba values avoid color-mix() support pitfalls.
 */
export function buildPreviewChromeStyle(): string {
  const fg = parseRgbColor(resolveForegroundBaseColor());
  const isLightForeground =
    fg !== null && 0.2126 * fg.r + 0.7152 * fg.g + 0.0722 * fg.b > 150;
  const useNeutralThumb = isLightForeground || fg === null;
  const thumb = useNeutralThumb ? "rgba(0, 0, 0, 0.30)" : `rgba(${fg.r}, ${fg.g}, ${fg.b}, 0.30)`;
  const thumbHover = useNeutralThumb
    ? "rgba(0, 0, 0, 0.45)"
    : `rgba(${fg.r}, ${fg.g}, ${fg.b}, 0.45)`;
  return [
    "*, *::before, *::after {",
    "  scrollbar-width: thin;",
    `  scrollbar-color: ${thumb} transparent;`,
    "}",
    "::-webkit-scrollbar { width: 6px; height: 6px; }",
    "::-webkit-scrollbar-track { background: transparent; }",
    "::-webkit-scrollbar-thumb {",
    "  border-radius: 999px;",
    `  background: ${thumb};`,
    "}",
    "::-webkit-scrollbar-thumb:hover {",
    `  background: ${thumbHover};`,
    "}",
    "::-webkit-scrollbar-button { display: none; width: 0; height: 0; }",
  ].join("\n");
}

/**
 * Prepends the chrome style into the preview HTML so the iframe renders
 * scrollbars consistent with the dashboard. The tag goes into <head> when
 * present, otherwise it is simply prepended (browsers relocate styles).
 */
export function injectPreviewChrome(html: string, chromeStyle: string): string {
  const styleTag = `<style data-k41-preview-chrome>${chromeStyle}</style>`;
  // Avoid matching <head> inside HTML comments (e.g. `<!-- <head> -->`).
  // Replace comments with spaces to preserve indices, then search in the stripped copy
  // but slice the original string at the same offset.
  const stripped = html.replace(/<!--[\s\S]*?-->/g, (m) => " ".repeat(m.length));
  const headMatch = /<head[^>]*>/i.exec(stripped);
  if (headMatch && typeof headMatch.index === "number") {
    const insertAt = headMatch.index + headMatch[0].length;
    return html.slice(0, insertAt) + styleTag + html.slice(insertAt);
  }
  return styleTag + html;
}