import DOMPurify from "dompurify";
import { Check, Code2, Copy, Eye } from "lucide-solid";
import { marked } from "marked";
import { createEffect, createMemo, onCleanup, onMount, untrack } from "solid-js";
import { render } from "solid-js/web";

import { highlightCode, languageFromName } from "@/lib/codeHighlight";
import { createDarkMode } from "@/lib/theme";

marked.setOptions({
  gfm: true,
  breaks: true,
});

type MermaidApi = typeof import("mermaid").default;

const MERMAID_RENDER_DELAY_MS = 700;

let mermaidPromise: Promise<MermaidApi> | null = null;
let nextMermaidRenderId = 1;

function getMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((module) => module.default);
  }
  return mermaidPromise;
}

function codeBlockLanguage(pre: HTMLPreElement): string {
  const code = pre.querySelector("code");
  const languageClass = Array.from(code?.classList || []).find((className) =>
    className.startsWith("language-"),
  );
  return languageClass ? languageClass.slice("language-".length) : "";
}

function isMermaidLanguage(language: string): boolean {
  const normalized = language.trim().toLowerCase();
  return normalized === "mermaid" || normalized === "mmd";
}

function normalizeMermaidSource(source: string): string {
  return source.trim();
}

function collectCompleteMermaidBlocks(markdown: string): Map<string, number> {
  const blocks = new Map<string, number>();
  const lines = markdown.split(/\r?\n/);
  let openFence:
    | {
        char: "`" | "~";
        content: string[];
        length: number;
        mermaid: boolean;
      }
    | null = null;

  for (const line of lines) {
    if (openFence) {
      const closeMatch = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
      const closeFence = closeMatch?.[1] || "";
      if (
        closeFence &&
        closeFence[0] === openFence.char &&
        closeFence.length >= openFence.length
      ) {
        if (openFence.mermaid) {
          const source = normalizeMermaidSource(openFence.content.join("\n"));
          if (source) {
            blocks.set(source, (blocks.get(source) || 0) + 1);
          }
        }
        openFence = null;
        continue;
      }

      openFence.content.push(line);
      continue;
    }

    const openMatch = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    const fence = openMatch?.[1] || "";
    if (!fence) {
      continue;
    }

    const language = (openMatch?.[2] || "").trim().split(/\s+/)[0] || "";
    openFence = {
      char: fence[0] as "`" | "~",
      content: [],
      length: fence.length,
      mermaid: isMermaidLanguage(language),
    };
  }

  return blocks;
}

function consumeMermaidBlock(blocks: Map<string, number>, source: string): boolean {
  const count = blocks.get(source) || 0;
  if (count <= 0) {
    return false;
  }
  if (count === 1) {
    blocks.delete(source);
  } else {
    blocks.set(source, count - 1);
  }
  return true;
}

function formatCodeLanguage(language: string): string {
  const normalized = language.trim().toLowerCase();
  const labels: Record<string, string> = {
    js: "JavaScript",
    jsx: "JavaScript",
    ts: "TypeScript",
    tsx: "TypeScript",
    typescript: "TypeScript",
  };
  if (!normalized) {
    return "Code";
  }
  return labels[normalized] || normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function mermaidConfig(dark: boolean): Parameters<MermaidApi["initialize"]>[0] {
  const fontFamily =
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

  return {
    htmlLabels: false,
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    flowchart: {
      htmlLabels: false,
    },
    themeVariables: dark
      ? {
          background: "transparent",
          fontFamily,
          lineColor: "#a3a3a3",
          mainBkg: "#171717",
          primaryBorderColor: "rgba(255, 255, 255, 0.28)",
          primaryColor: "#171717",
          primaryTextColor: "#fafafa",
          secondaryBorderColor: "rgba(255, 255, 255, 0.2)",
          secondaryColor: "#111111",
          secondaryTextColor: "#fafafa",
          tertiaryBorderColor: "rgba(255, 255, 255, 0.16)",
          tertiaryColor: "#262626",
          tertiaryTextColor: "#fafafa",
          textColor: "#fafafa",
        }
      : {
          background: "transparent",
          fontFamily,
          lineColor: "#737373",
          mainBkg: "#ffffff",
          primaryBorderColor: "#d4d4d4",
          primaryColor: "#ffffff",
          primaryTextColor: "#0a0a0a",
          secondaryBorderColor: "#d4d4d4",
          secondaryColor: "#f5f5f5",
          secondaryTextColor: "#0a0a0a",
          tertiaryBorderColor: "#e5e5e5",
          tertiaryColor: "#ededed",
          tertiaryTextColor: "#0a0a0a",
          textColor: "#0a0a0a",
        },
  };
}

async function writeToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(textarea);
  }
}

export function Markdown(props: { text: string; class?: string; deferMermaid?: boolean }) {
  let containerRef: HTMLDivElement | undefined;
  let disposed = false;
  let mermaidRenderTimer: number | undefined;
  let iconDisposers = new Set<() => void>();
  let iconDisposerByElement = new WeakMap<Element, () => void>();
  const dark = createDarkMode();

  const html = createMemo(() => {
    const source = props.text || "";
    const raw = marked.parse(source, { async: false }) as string;
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target", "rel"] });
  });

  const cleanupIcons = () => {
    iconDisposers.forEach((dispose) => dispose());
    iconDisposers.clear();
    iconDisposerByElement = new WeakMap<Element, () => void>();
  };

  const mountIcon = (element: Element, icon: "check" | "code" | "copy" | "diagram") => {
    const currentDispose = iconDisposerByElement.get(element);
    if (currentDispose) {
      currentDispose();
      iconDisposers.delete(currentDispose);
      iconDisposerByElement.delete(element);
    }
    element.replaceChildren();

    const dispose = render(
      () => {
        if (icon === "check") {
          return <Check size={14} strokeWidth={2} />;
        }
        if (icon === "copy") {
          return <Copy size={15} strokeWidth={1.9} />;
        }
        if (icon === "diagram") {
          return <Eye size={15} strokeWidth={1.9} />;
        }
        return <Code2 size={14} strokeWidth={2} />;
      },
      element,
    );
    iconDisposerByElement.set(element, dispose);
    iconDisposers.add(dispose);
  };

  const highlightBlock = async (pre: HTMLPreElement, language: string) => {
    const code = pre.querySelector("code");
    const source = code?.textContent || pre.textContent || "";
    if (!source.trim()) {
      return;
    }

    try {
      const highlighted = await highlightCode(source, languageFromName(language), true);
      if (disposed || !pre.isConnected) {
        return;
      }

      const template = document.createElement("template");
      template.innerHTML = DOMPurify.sanitize(highlighted);
      const highlightedCode = template.content.querySelector("code");
      if (highlightedCode && code) {
        code.innerHTML = highlightedCode.innerHTML;
        pre.classList.add("markdown-code-highlighted");
      }
    } catch {
      pre.classList.add("markdown-code-plain");
    }
  };

  const updateMermaidToggleButton = (button: HTMLButtonElement, view: "diagram" | "source") => {
    if (view === "source") {
      button.title = "View diagram";
      button.setAttribute("aria-label", "View diagram");
      mountIcon(button, "diagram");
      return;
    }
    button.title = "View source";
    button.setAttribute("aria-label", "View source");
    mountIcon(button, "code");
  };

  const setMermaidFrameView = (frame: HTMLElement, view: "diagram" | "source") => {
    const diagram = frame.querySelector<HTMLElement>(".markdown-mermaid");
    const source = frame.querySelector<HTMLElement>(".markdown-mermaid-raw");
    const toggleButton = frame.querySelector<HTMLButtonElement>(".markdown-mermaid-toggle");
    frame.dataset.mermaidView = view;
    diagram?.classList.toggle("is-hidden", view === "source");
    source?.classList.toggle("is-hidden", view === "diagram");
    if (toggleButton) {
      updateMermaidToggleButton(toggleButton, view);
    }
  };

  const createMermaidFrame = (source: string, darkMode: boolean): HTMLElement => {
    const frame = document.createElement("div");
    frame.className = "markdown-mermaid-frame";
    frame.dataset.mermaidSource = source;
    frame.dataset.mermaidTheme = darkMode ? "dark" : "light";
    frame.dataset.mermaidView = "diagram";

    const header = document.createElement("div");
    header.className = "markdown-mermaid-header";

    const title = document.createElement("div");
    title.className = "markdown-mermaid-title";

    const titleIcon = document.createElement("span");
    titleIcon.className = "markdown-mermaid-title-icon";
    titleIcon.setAttribute("aria-hidden", "true");
    mountIcon(titleIcon, "diagram");

    const titleLabel = document.createElement("span");
    titleLabel.textContent = "Mermaid";
    title.append(titleIcon, titleLabel);

    const actions = document.createElement("div");
    actions.className = "markdown-mermaid-actions";

    const toggleButton = document.createElement("button");
    toggleButton.className = "markdown-mermaid-toggle";
    toggleButton.type = "button";
    updateMermaidToggleButton(toggleButton, "diagram");

    const copyButton = document.createElement("button");
    copyButton.className = "markdown-mermaid-copy";
    copyButton.type = "button";
    copyButton.title = "Copy source";
    copyButton.setAttribute("aria-label", "Copy source");
    mountIcon(copyButton, "copy");

    actions.append(toggleButton, copyButton);
    header.append(title, actions);

    const body = document.createElement("div");
    body.className = "markdown-mermaid-body";

    const diagram = document.createElement("div");
    diagram.className = "markdown-mermaid is-loading";
    diagram.setAttribute("aria-busy", "true");
    diagram.textContent = "Rendering diagram...";

    const raw = document.createElement("pre");
    raw.className = "markdown-mermaid-raw is-hidden";
    const code = document.createElement("code");
    code.textContent = source;
    raw.appendChild(code);

    body.append(diagram, raw);
    frame.append(header, body);
    return frame;
  };

  const renderMermaidFallback = (frame: HTMLElement, source: string) => {
    frame.classList.add("markdown-mermaid-frame-error");
    frame.dataset.mermaidSource = source;
    const diagram = frame.querySelector<HTMLElement>(".markdown-mermaid");
    if (!diagram) {
      return;
    }
    diagram.className = "markdown-mermaid markdown-mermaid-error";
    diagram.removeAttribute("aria-busy");
    diagram.replaceChildren();

    const message = document.createElement("div");
    message.className = "markdown-mermaid-error-message";
    message.textContent = "Unable to render Mermaid diagram.";
    diagram.appendChild(message);
    setMermaidFrameView(frame, "source");
  };

  const renderMermaidFrame = async (
    frame: HTMLElement,
    source: string,
    darkMode: boolean,
  ) => {
    const renderId = `markdown-mermaid-${nextMermaidRenderId}`;
    const renderKey = `${renderId}-${darkMode ? "dark" : "light"}`;
    nextMermaidRenderId += 1;

    const diagram = frame.querySelector<HTMLElement>(".markdown-mermaid");
    if (!diagram) {
      return;
    }

    frame.classList.remove("markdown-mermaid-frame-error");
    frame.dataset.mermaidRenderKey = renderKey;
    frame.dataset.mermaidSource = source;
    frame.dataset.mermaidTheme = darkMode ? "dark" : "light";
    diagram.className = "markdown-mermaid is-loading";
    if (frame.dataset.mermaidView === "source") {
      diagram.classList.add("is-hidden");
    }
    diagram.setAttribute("aria-busy", "true");
    diagram.textContent = "Rendering diagram...";

    try {
      const mermaid = await getMermaid();
      if (
        disposed ||
        !frame.isConnected ||
        frame.dataset.mermaidRenderKey !== renderKey
      ) {
        return;
      }

      mermaid.initialize(mermaidConfig(darkMode));
      const { svg, bindFunctions } = await mermaid.render(renderId, source);
      if (
        disposed ||
        !frame.isConnected ||
        frame.dataset.mermaidRenderKey !== renderKey
      ) {
        return;
      }

      diagram.className = "markdown-mermaid";
      diagram.removeAttribute("aria-busy");
      diagram.innerHTML = DOMPurify.sanitize(svg);
      setMermaidFrameView(
        frame,
        frame.dataset.mermaidView === "source" ? "source" : "diagram",
      );
      bindFunctions?.(diagram);
    } catch {
      if (!disposed && frame.isConnected && frame.dataset.mermaidRenderKey === renderKey) {
        renderMermaidFallback(frame, source);
      }
    }
  };

  const renderMermaidPre = async (
    pre: HTMLPreElement,
    source: string,
    darkMode: boolean,
  ) => {
    const frame = createMermaidFrame(source, darkMode);
    pre.classList.add("is-rendering");
    pre.replaceWith(frame);
    await renderMermaidFrame(frame, source, darkMode);
  };

  const enhanceCodeBlocks = () => {
    if (!containerRef) {
      return;
    }

    containerRef.querySelectorAll<HTMLPreElement>("pre").forEach((pre) => {
      if (pre.parentElement?.classList.contains("markdown-code-frame")) {
        return;
      }

      const language = codeBlockLanguage(pre);
      if (isMermaidLanguage(language)) {
        pre.classList.add("markdown-mermaid-source");
        return;
      }

      const frame = document.createElement("div");
      frame.className = "markdown-code-frame";

      const header = document.createElement("div");
      header.className = "markdown-code-header";

      const title = document.createElement("div");
      title.className = "markdown-code-title";

      const titleIcon = document.createElement("span");
      titleIcon.className = "markdown-code-title-icon";
      titleIcon.setAttribute("aria-hidden", "true");
      mountIcon(titleIcon, "code");

      const languageLabel = document.createElement("span");
      languageLabel.className = "markdown-code-language";
      languageLabel.textContent = formatCodeLanguage(language);
      title.append(titleIcon, languageLabel);
      header.appendChild(title);

      const copyButton = document.createElement("button");
      copyButton.className = "markdown-code-copy";
      copyButton.type = "button";
      copyButton.title = "Copy code";
      copyButton.setAttribute("aria-label", "Copy code");
      mountIcon(copyButton, "copy");
      header.appendChild(copyButton);

      pre.replaceWith(frame);
      frame.append(header, pre);
      void highlightBlock(pre, language);
    });
  };

  const renderReadyMermaidCodeBlocks = (
    darkMode: boolean,
    completeMermaidBlocks: Map<string, number>,
  ) => {
    if (!containerRef) {
      return;
    }

    containerRef.querySelectorAll<HTMLPreElement>("pre").forEach((pre) => {
      const language = codeBlockLanguage(pre);
      if (!isMermaidLanguage(language)) {
        return;
      }

      const code = pre.querySelector("code");
      const source = normalizeMermaidSource(code?.textContent || pre.textContent || "");
      if (!source || !consumeMermaidBlock(completeMermaidBlocks, source)) {
        return;
      }

      void renderMermaidPre(pre, source, darkMode);
    });
  };

  const rerenderMermaidDiagrams = (darkMode: boolean) => {
    if (!containerRef) {
      return;
    }

    const theme = darkMode ? "dark" : "light";
    containerRef
      .querySelectorAll<HTMLElement>(".markdown-mermaid-frame[data-mermaid-source]")
      .forEach((frame) => {
        if (frame.dataset.mermaidTheme === theme) {
          return;
        }
        const source = frame.dataset.mermaidSource || "";
        if (source.trim()) {
          void renderMermaidFrame(frame, source, darkMode);
        }
      });
  };

  const clearMermaidRenderTimer = () => {
    if (mermaidRenderTimer !== undefined) {
      window.clearTimeout(mermaidRenderTimer);
      mermaidRenderTimer = undefined;
    }
  };

  const scheduleMermaidRender = (source: string) => {
    clearMermaidRenderTimer();
    mermaidRenderTimer = window.setTimeout(() => {
      if (!disposed && props.text === source && !props.deferMermaid) {
        renderReadyMermaidCodeBlocks(dark(), collectCompleteMermaidBlocks(source));
      }
    }, MERMAID_RENDER_DELAY_MS);
  };

  const handleCopyClick = async (event: MouseEvent) => {
    const target = event.target;
    if (!(target instanceof Element) || !containerRef) {
      return;
    }

    const mermaidToggle = target.closest<HTMLButtonElement>(".markdown-mermaid-toggle");
    if (mermaidToggle && containerRef.contains(mermaidToggle)) {
      event.preventDefault();
      event.stopPropagation();

      const frame = mermaidToggle.closest<HTMLElement>(".markdown-mermaid-frame");
      if (!frame) {
        return;
      }
      const currentView = frame.dataset.mermaidView === "source" ? "source" : "diagram";
      setMermaidFrameView(frame, currentView === "source" ? "diagram" : "source");
      return;
    }

    const mermaidCopyButton = target.closest<HTMLButtonElement>(".markdown-mermaid-copy");
    if (mermaidCopyButton && containerRef.contains(mermaidCopyButton)) {
      event.preventDefault();
      event.stopPropagation();

      const frame = mermaidCopyButton.closest<HTMLElement>(".markdown-mermaid-frame");
      const text = frame?.dataset.mermaidSource || "";
      if (!text) {
        return;
      }

      mermaidCopyButton.disabled = true;
      try {
        await writeToClipboard(text);
        mermaidCopyButton.title = "Copied";
        mermaidCopyButton.setAttribute("aria-label", "Copied");
        mermaidCopyButton.classList.add("copied");
        mountIcon(mermaidCopyButton, "check");
      } catch {
        mermaidCopyButton.title = "Copy failed";
        mermaidCopyButton.setAttribute("aria-label", "Copy failed");
        mermaidCopyButton.classList.add("failed");
      } finally {
        window.setTimeout(() => {
          if (mermaidCopyButton.isConnected) {
            mermaidCopyButton.title = "Copy source";
            mermaidCopyButton.setAttribute("aria-label", "Copy source");
            mermaidCopyButton.classList.remove("copied", "failed");
            mountIcon(mermaidCopyButton, "copy");
            mermaidCopyButton.disabled = false;
          }
        }, 1200);
      }
      return;
    }

    const button = target.closest<HTMLButtonElement>(".markdown-code-copy");
    if (!button || !containerRef.contains(button)) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    const frame = button.closest(".markdown-code-frame");
    const code = frame?.querySelector("pre code") || frame?.querySelector("pre");
    const text = code?.textContent || "";
    if (!text) {
      return;
    }

    button.disabled = true;
    try {
      await writeToClipboard(text);
      button.title = "Copied";
      button.setAttribute("aria-label", "Copied");
      button.classList.add("copied");
      mountIcon(button, "check");
    } catch {
      button.title = "Copy failed";
      button.setAttribute("aria-label", "Copy failed");
      button.classList.add("failed");
    } finally {
      window.setTimeout(() => {
        if (button.isConnected) {
          button.title = "Copy code";
          button.setAttribute("aria-label", "Copy code");
          button.classList.remove("copied", "failed");
          mountIcon(button, "copy");
          button.disabled = false;
        }
      }, 1200);
    }
  };

  createEffect(() => {
    const source = props.text || "";
    html();
    cleanupIcons();
    clearMermaidRenderTimer();
    queueMicrotask(() => {
      if (!disposed) {
        enhanceCodeBlocks();
      }
    });
    if (!untrack(() => Boolean(props.deferMermaid))) {
      scheduleMermaidRender(source);
    }
  });

  let previousDeferMermaid = Boolean(props.deferMermaid);
  createEffect(() => {
    const deferMermaid = Boolean(props.deferMermaid);
    if (deferMermaid === previousDeferMermaid) {
      return;
    }
    previousDeferMermaid = deferMermaid;
    if (deferMermaid) {
      clearMermaidRenderTimer();
      return;
    }
    scheduleMermaidRender(untrack(() => props.text || ""));
  });

  let previousDark = dark();
  createEffect(() => {
    const currentDark = dark();
    if (currentDark === previousDark) {
      return;
    }
    previousDark = currentDark;
    if (props.deferMermaid) {
      return;
    }
    queueMicrotask(() => {
      if (!disposed) {
        rerenderMermaidDiagrams(currentDark);
      }
    });
  });

  onMount(() => {
    containerRef?.addEventListener("click", handleCopyClick);
  });

  onCleanup(() => {
    disposed = true;
    clearMermaidRenderTimer();
    cleanupIcons();
    containerRef?.removeEventListener("click", handleCopyClick);
  });

  return (
    <div
      ref={containerRef}
      class={`markdown ${props.class || ""}`}
      // eslint-disable-next-line solid/no-innerhtml
      innerHTML={html()}
    />
  );
}
