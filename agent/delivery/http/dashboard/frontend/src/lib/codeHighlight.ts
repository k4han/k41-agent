import {
  bundledLanguages,
  type BundledLanguage,
  type BundledTheme,
  createHighlighter,
  type Highlighter,
} from "shiki";

const LIGHT_THEME: BundledTheme = "github-light";
const DARK_THEME: BundledTheme = "github-dark";

let highlighterPromise: Promise<Highlighter> | null = null;
const loadingLangs = new Map<string, Promise<void>>();

const EXTENSION_TO_LANG: Record<string, BundledLanguage> = {
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
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  swift: "swift",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  cxx: "cpp",
  hpp: "cpp",
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
  sass: "sass",
  less: "less",
  xml: "xml",
  sql: "sql",
  ini: "ini",
  dockerfile: "docker",
  lua: "lua",
  vue: "vue",
  svelte: "svelte",
  astro: "astro",
  diff: "diff",
  patch: "diff",
};

const BUNDLED_LANG_NAMES = new Set(Object.keys(bundledLanguages));
const LANGUAGE_ALIASES: Record<string, BundledLanguage> = {
  bash: "shell",
  js: "javascript",
  md: "markdown",
  ps1: "powershell",
  py: "python",
  sh: "shell",
  ts: "typescript",
};

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: [LIGHT_THEME, DARK_THEME],
      langs: [],
    });
  }
  return highlighterPromise;
}

export function languageFromPath(path: string): BundledLanguage | "text" {
  const lower = path.toLowerCase();
  const name = lower.split(/[\\/]/).pop() || lower;
  if (name === "dockerfile") {
    return "docker";
  }
  if (name === "makefile") {
    return "make";
  }
  const ext = name.includes(".") ? name.split(".").pop() || "" : "";
  const candidate = EXTENSION_TO_LANG[ext];
  if (candidate && BUNDLED_LANG_NAMES.has(candidate)) {
    return candidate;
  }
  return "text";
}

export function languageFromName(language: string): BundledLanguage | "text" {
  const normalized = language.trim().toLowerCase();
  if (!normalized) {
    return "text";
  }
  const candidate = LANGUAGE_ALIASES[normalized] || normalized;
  return BUNDLED_LANG_NAMES.has(candidate) ? (candidate as BundledLanguage) : "text";
}

async function ensureLanguageLoaded(
  highlighter: Highlighter,
  lang: BundledLanguage | "text",
): Promise<void> {
  if (lang === "text") {
    return;
  }
  if (highlighter.getLoadedLanguages().includes(lang)) {
    return;
  }
  const pending = loadingLangs.get(lang);
  if (pending) {
    await pending;
    return;
  }
  const task = highlighter.loadLanguage(lang).then(() => undefined);
  loadingLangs.set(lang, task);
  try {
    await task;
  } finally {
    loadingLangs.delete(lang);
  }
}

export async function highlightCode(
  code: string,
  lang: BundledLanguage | "text",
  dark: boolean,
): Promise<string> {
  const highlighter = await getHighlighter();
  await ensureLanguageLoaded(highlighter, lang);
  return highlighter.codeToHtml(code, {
    lang,
    theme: dark ? DARK_THEME : LIGHT_THEME,
  });
}
