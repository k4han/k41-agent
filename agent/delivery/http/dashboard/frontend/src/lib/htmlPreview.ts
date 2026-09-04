export const HTML_PREVIEW_TOOL_NAME = "html_preview";

export type HtmlPreviewMode = "auto" | "card" | "full_page";

export type HtmlPreviewContent = {
  title: string;
  html: string;
  mode?: HtmlPreviewMode;
};

export type PreviewViewport = "responsive" | "desktop" | "tablet" | "mobile";

export const PREVIEW_VIEWPORT_WIDTHS: Record<PreviewViewport, number | null> = {
  responsive: null,
  desktop: 1280,
  tablet: 768,
  mobile: 375,
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
  const rawMode = typeof record.mode === "string" ? record.mode.toLowerCase().trim() : "";
  const mode: HtmlPreviewMode =
    rawMode === "card" || rawMode === "full_page" ? (rawMode as HtmlPreviewMode) : "auto";
  return { title, html, mode };
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
 * Heuristic to detect full-page website HTML vs a compact card fragment.
 * Full pages render poorly in the small inline chat frame (~960px x 420px)
 * so the transcript can give them a taller frame plus an expand hint.
 */
export function isFullPageHtml(html: string, mode?: HtmlPreviewMode): boolean {
  if (mode === "full_page") {
    return true;
  }
  if (mode === "card") {
    return false;
  }
  if (!html || typeof html !== "string") {
    return false;
  }
  const stripped = html.replace(/<!--[\s\S]*?-->/g, (m) => " ".repeat(m.length));
  if (
    /<!doctype\s+html/i.test(stripped) ||
    /<html[\s>]/i.test(stripped) ||
    /<head[\s>]/i.test(stripped) ||
    /<body[\s>]/i.test(stripped)
  ) {
    return true;
  }
  // Long documents are only treated as pages when they also carry page
  // structure. A bare length check misclassifies large cards (big SVG
  // charts, wide tables) that have no <html>/<head>/<body>.
  const landmarks = stripped.match(/<(section|nav|header|footer|main|article|aside)[\s>]/gi);
  const landmarkCount = landmarks ? landmarks.length : 0;
  if (landmarkCount >= 3 && html.length > 6000) {
    return true;
  }
  if (landmarkCount >= 2 && html.length > 20000) {
    return true;
  }
  return false;
}

/**
 * Base responsive reset so full-page HTML designed for wide desktop widths
 * does not blow out horizontally inside the narrow chat iframe.
 */
export function buildPreviewResponsiveStyle(): string {
  return [
    "html, body {",
    "  margin: 0;",
    "  padding: 0;",
    "  max-width: 100%;",
    "  overflow-x: auto;",
    "}",
    "body { box-sizing: border-box; }",
    "img, video, canvas, svg { max-width: 100%; height: auto; }",
    "table { max-width: 100%; }",
    "pre { white-space: pre-wrap; word-break: break-word; }",
  ].join("\n");
}

/**
 * Light observer script injected into the preview frame to inform the host
 * of the document's content height, enabling automatic height fitting.
 */
export function buildPreviewObserverScript(): string {
  return [
    "<script data-k41-preview-observer>",
    "(function() {",
    "  var last = -1;",
    "  var scheduled = false;",
    "  function send(h) {",
    "    if (Math.abs(h - last) <= 1) return;",
    "    last = h;",
    "    window.parent.postMessage({ type: 'k41-preview-height', height: h }, '*');",
    "  }",
    "  function reportHeight() {",
    "    try {",
    "      var body = document.body;",
    "      var doc = document.documentElement;",
    "      var h = Math.max(",
    "        body ? body.scrollHeight : 0,",
    "        body ? body.offsetHeight : 0,",
    "        doc ? doc.scrollHeight : 0,",
    "        doc ? doc.offsetHeight : 0",
    "      );",
    "      if (h > 0 && Number.isFinite(h)) {",
    "        if (typeof requestAnimationFrame !== 'undefined') {",
    "          if (scheduled) return;",
    "          scheduled = true;",
    "          requestAnimationFrame(function() { scheduled = false; send(h); });",
    "        } else { send(h); }",
    "      }",
    "    } catch(e) {}",
    "  }",
    "  if (document.readyState === 'complete' || document.readyState === 'interactive') {",
    "    reportHeight();",
    "  } else {",
    "    window.addEventListener('DOMContentLoaded', reportHeight);",
    "  }",
    "  window.addEventListener('load', reportHeight);",
    "  if (typeof ResizeObserver !== 'undefined' && document.body) {",
    "    try {",
    "      new ResizeObserver(reportHeight).observe(document.body);",
    "    } catch(e) {}",
    "  }",
    "})();",
    "</script>",
  ].join("\n");
}

/**
 * Prepends chrome styling, responsive reset, and the height reporter
 * into the preview HTML. The tags go into <head> when present.
 */
export function injectPreviewChrome(html: string, chromeStyle: string): string {
  const responsiveStyle = buildPreviewResponsiveStyle();
  const combined = `${responsiveStyle}\n${chromeStyle}`;
  const styleTag = `<style data-k41-preview-chrome>${combined}</style>`;
  const observerScript = buildPreviewObserverScript();
  // Avoid matching <head> inside HTML comments (e.g. `<!-- <head> -->`).
  // Replace comments with spaces to preserve indices, then search in the stripped copy
  // but slice the original string at the same offset.
  const stripped = html.replace(/<!--[\s\S]*?-->/g, (m) => " ".repeat(m.length));
  const hasViewport = /<meta[^>]*name=["']viewport["']/i.test(stripped);
  const viewportTag = hasViewport
    ? ""
    : '<meta name="viewport" content="width=device-width, initial-scale=1">';
  const payload = viewportTag + styleTag + observerScript;
  // Insert before </head> so an existing <meta charset> / <title> stays first
  // (charset should remain within the first ~1024 bytes).
  const closeHeadMatch = /<\/head\s*>/i.exec(stripped);
  if (closeHeadMatch && typeof closeHeadMatch.index === "number") {
    const insertAt = closeHeadMatch.index;
    return html.slice(0, insertAt) + payload + html.slice(insertAt);
  }
  const headMatch = /<head[^>]*>/i.exec(stripped);
  if (headMatch && typeof headMatch.index === "number") {
    const insertAt = headMatch.index + headMatch[0].length;
    return (
      html.slice(0, insertAt) +
      viewportTag +
      styleTag +
      observerScript +
      html.slice(insertAt)
    );
  }
  return viewportTag + styleTag + observerScript + html;
}