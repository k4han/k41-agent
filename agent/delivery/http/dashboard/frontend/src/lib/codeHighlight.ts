import type { SupportedHighlightLanguage } from "./codeHighlight.worker";

type HighlightLanguage = SupportedHighlightLanguage | "text";

const EXTENSION_TO_LANG: Partial<Record<string, SupportedHighlightLanguage>> = {
  ts: "typescript",
  tsx: "tsx",
  cts: "typescript",
  mts: "typescript",
  js: "javascript",
  jsx: "jsx",
  cjs: "javascript",
  mjs: "javascript",
  py: "python",
  pyi: "python",
  go: "go",
  rs: "rust",
  java: "java",
  c: "c",
  h: "c",
  cs: "csharp",
  php: "php",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  ps1: "powershell",
  json: "json",
  jsonc: "jsonc",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  md: "markdown",
  mdx: "mdx",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  xml: "xml",
  sql: "sql",
  ini: "ini",
  dockerfile: "docker",
  vue: "vue",
  svelte: "svelte",
  astro: "astro",
  diff: "diff",
  patch: "diff",
};

const SUPPORTED_LANG_NAMES = new Set<string>([
  "astro", "c", "csharp", "css", "diff", "docker", "go",
  "html", "ini", "java", "javascript", "json", "jsonc", "jsx",
  "markdown", "mdx", "php", "powershell", "python", "rust", "scss",
  "shell", "sql", "svelte", "toml", "tsx", "typescript", "vue",
  "xml", "yaml",
]);
const LANGUAGE_ALIASES: Record<string, SupportedHighlightLanguage> = {
  bash: "shell",
  cjs: "javascript",
  cts: "typescript",
  dockerfile: "docker",
  js: "javascript",
  md: "markdown",
  mjs: "javascript",
  mts: "typescript",
  patch: "diff",
  ps1: "powershell",
  py: "python",
  rs: "rust",
  cs: "csharp",
  sh: "shell",
  ts: "typescript",
  yml: "yaml",
  zsh: "shell",
};

function toSupportedLanguage(language: string): HighlightLanguage {
  const normalized = language.trim().toLowerCase();
  if (!normalized) {
    return "text";
  }
  const candidate = LANGUAGE_ALIASES[normalized] || normalized;
  return SUPPORTED_LANG_NAMES.has(candidate)
    ? (candidate as SupportedHighlightLanguage)
    : "text";
}

export function languageFromPath(path: string): HighlightLanguage {
  const lower = path.toLowerCase();
  const name = lower.split(/[\\/]/).pop() || lower;
  if (name === "dockerfile") {
    return "docker";
  }
  const ext = name.includes(".") ? name.split(".").pop() || "" : "";
  const candidate = EXTENSION_TO_LANG[ext];
  return candidate ? toSupportedLanguage(candidate) : "text";
}

export function languageFromName(language: string): HighlightLanguage {
  return toSupportedLanguage(language);
}

interface PendingHighlight {
  resolve: (html: string | null) => void;
}

const MAX_HIGHLIGHT_LENGTH = 100_000;
const MAX_CACHE_SIZE = 2_000_000;
const WORKER_STALL_TIMEOUT = 30_000;
const WORKER_RETRY_DELAY = 5_000;
const cache = new Map<string, string>();
const inFlight = new Map<string, Promise<string>>();
const pending = new Map<number, PendingHighlight>();
let cachedSize = 0;
let nextRequestId = 0;
let worker: Worker | undefined;
let workerRetryAt = 0;
let workerTimeout: ReturnType<typeof setTimeout> | undefined;

function plainCode(code: string, dark: boolean): string {
  const escaped = code.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  return `<pre class="shiki" style="background-color:${dark ? "#24292e" : "#fff"};color:${dark ? "#e1e4e8" : "#24292e"}"><code>${escaped}</code></pre>`;
}

// Queued requests share a deadline that resets whenever the worker makes progress.
function resetWorkerTimeout(): void {
  clearTimeout(workerTimeout);
  workerTimeout = pending.size > 0 ? setTimeout(stopWorker, WORKER_STALL_TIMEOUT) : undefined;
}

function stopWorker(): void {
  workerRetryAt = Date.now() + WORKER_RETRY_DELAY;
  worker?.terminate();
  worker = undefined;
  for (const request of pending.values()) {
    request.resolve(null);
  }
  pending.clear();
  resetWorkerTimeout();
}

function getWorker(): Worker | undefined {
  if (Date.now() < workerRetryAt || typeof Worker === "undefined") return undefined;
  if (!worker) {
    try {
      const target = new Worker(new URL("./codeHighlight.worker.ts", import.meta.url), { type: "module" });
      worker = target;
      target.onmessage = (event: MessageEvent<{ id: number; html: string | null }>) => {
        if (worker !== target) return;
        const request = pending.get(event.data.id);
        if (!request) return;
        pending.delete(event.data.id);
        resetWorkerTimeout();
        request.resolve(event.data.html);
      };
      target.onerror = (event) => { event.preventDefault(); if (worker === target) stopWorker(); };
      target.onmessageerror = () => { if (worker === target) stopWorker(); };
    } catch {
      stopWorker();
    }
  }
  return worker;
}

export async function highlightCode(
  code: string,
  lang: HighlightLanguage,
  dark: boolean,
): Promise<string> {
  // Bound token spans and pathological grammar work for large or minified files.
  if (lang === "text" || code.length > MAX_HIGHLIGHT_LENGTH
    || code.split("\n").some((line, index) => index > 2_000 || line.length > 4_000)) {
    return plainCode(code, dark);
  }
  const key = JSON.stringify([code, lang, dark]);
  const cached = cache.get(key);
  if (cached !== undefined) {
    cache.delete(key);
    cache.set(key, cached);
    return cached;
  }
  const active = inFlight.get(key);
  if (active) return active;
  const target = getWorker();
  if (!target) return plainCode(code, dark);
  const task = new Promise<string | null>((resolve) => {
    const id = ++nextRequestId;
    pending.set(id, { resolve });
    if (pending.size === 1) resetWorkerTimeout();
    try { target.postMessage({ id, code, lang, dark }); }
    catch { stopWorker(); }
  }).then((html) => {
    if (html === null) return plainCode(code, dark);
    const size = key.length + html.length;
    if (size <= MAX_CACHE_SIZE) {
      cache.set(key, html);
      cachedSize += size;
      while (cache.size > 32 || cachedSize > MAX_CACHE_SIZE) {
        const oldest = cache.keys().next().value!;
        cachedSize -= oldest.length + cache.get(oldest)!.length;
        cache.delete(oldest);
      }
    }
    return html;
  }).finally(() => { inFlight.delete(key); });
  inFlight.set(key, task);
  return task;
}
