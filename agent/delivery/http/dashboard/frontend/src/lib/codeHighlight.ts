import { createBundledHighlighter } from "shiki/core";
import { createOnigurumaEngine } from "shiki/engine/oniguruma";

const LIGHT_THEME = "github-light";
const DARK_THEME = "github-dark";

const LANGUAGE_LOADERS = {
  astro: () => import("shiki/dist/langs/astro.mjs"),
  c: () => import("shiki/dist/langs/c.mjs"),
  csharp: () => import("shiki/dist/langs/csharp.mjs"),
  css: () => import("shiki/dist/langs/css.mjs"),
  diff: () => import("shiki/dist/langs/diff.mjs"),
  docker: () => import("shiki/dist/langs/docker.mjs"),
  go: () => import("shiki/dist/langs/go.mjs"),
  html: () => import("shiki/dist/langs/html.mjs"),
  ini: () => import("shiki/dist/langs/ini.mjs"),
  java: () => import("shiki/dist/langs/java.mjs"),
  javascript: () => import("shiki/dist/langs/javascript.mjs"),
  json: () => import("shiki/dist/langs/json.mjs"),
  jsonc: () => import("shiki/dist/langs/jsonc.mjs"),
  jsx: () => import("shiki/dist/langs/jsx.mjs"),
  markdown: () => import("shiki/dist/langs/markdown.mjs"),
  mdx: () => import("shiki/dist/langs/mdx.mjs"),
  php: () => import("shiki/dist/langs/php.mjs"),
  powershell: () => import("shiki/dist/langs/powershell.mjs"),
  python: () => import("shiki/dist/langs/python.mjs"),
  rust: () => import("shiki/dist/langs/rust.mjs"),
  scss: () => import("shiki/dist/langs/scss.mjs"),
  shell: () => import("shiki/dist/langs/shell.mjs"),
  sql: () => import("shiki/dist/langs/sql.mjs"),
  svelte: () => import("shiki/dist/langs/svelte.mjs"),
  toml: () => import("shiki/dist/langs/toml.mjs"),
  tsx: () => import("shiki/dist/langs/tsx.mjs"),
  typescript: () => import("shiki/dist/langs/typescript.mjs"),
  vue: () => import("shiki/dist/langs/vue.mjs"),
  xml: () => import("shiki/dist/langs/xml.mjs"),
  yaml: () => import("shiki/dist/langs/yaml.mjs"),
} as const;

const THEME_LOADERS = {
  [LIGHT_THEME]: () => import("shiki/dist/themes/github-light.mjs"),
  [DARK_THEME]: () => import("shiki/dist/themes/github-dark.mjs"),
} as const;

type SupportedHighlightLanguage = keyof typeof LANGUAGE_LOADERS;
type HighlightLanguage = SupportedHighlightLanguage | "text";

const createDashboardHighlighter = createBundledHighlighter({
  langs: LANGUAGE_LOADERS,
  themes: THEME_LOADERS,
  engine: () => createOnigurumaEngine(import("shiki/wasm")),
});
type DashboardHighlighter = Awaited<ReturnType<typeof createDashboardHighlighter>>;

let highlighterPromise: Promise<DashboardHighlighter> | null = null;
const loadingLangs = new Map<string, Promise<void>>();

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

const SUPPORTED_LANG_NAMES = new Set<string>(Object.keys(LANGUAGE_LOADERS));
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

function getHighlighter(): Promise<DashboardHighlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createDashboardHighlighter({
      themes: [LIGHT_THEME, DARK_THEME],
      langs: [],
    });
  }
  return highlighterPromise;
}

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

async function ensureLanguageLoaded(
  highlighter: DashboardHighlighter,
  lang: HighlightLanguage,
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
  lang: HighlightLanguage,
  dark: boolean,
): Promise<string> {
  const highlighter = await getHighlighter();
  await ensureLanguageLoaded(highlighter, lang);
  return highlighter.codeToHtml(code, {
    lang,
    theme: dark ? DARK_THEME : LIGHT_THEME,
  });
}
