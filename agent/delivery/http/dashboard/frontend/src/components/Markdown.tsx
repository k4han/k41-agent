import DOMPurify from "dompurify";
import { Check, Code2, Copy } from "lucide-solid";
import { marked } from "marked";
import { createEffect, createMemo, onCleanup, onMount } from "solid-js";
import { render } from "solid-js/web";

import { highlightCode, languageFromName } from "@/lib/codeHighlight";

marked.setOptions({
  gfm: true,
  breaks: true,
});

function codeBlockLanguage(pre: HTMLPreElement): string {
  const code = pre.querySelector("code");
  const languageClass = Array.from(code?.classList || []).find((className) =>
    className.startsWith("language-"),
  );
  return languageClass ? languageClass.slice("language-".length) : "";
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

export function Markdown(props: { text: string; class?: string }) {
  let containerRef: HTMLDivElement | undefined;
  let disposed = false;
  let iconDisposers = new Set<() => void>();
  let iconDisposerByElement = new WeakMap<Element, () => void>();

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

  const mountIcon = (element: Element, icon: "check" | "code" | "copy") => {
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

  const enhanceCodeBlocks = () => {
    if (!containerRef) {
      return;
    }

    containerRef.querySelectorAll<HTMLPreElement>("pre").forEach((pre) => {
      if (pre.parentElement?.classList.contains("markdown-code-frame")) {
        return;
      }

      const frame = document.createElement("div");
      frame.className = "markdown-code-frame";

      const header = document.createElement("div");
      header.className = "markdown-code-header";

      const language = codeBlockLanguage(pre);
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

  const handleCopyClick = async (event: MouseEvent) => {
    const target = event.target;
    if (!(target instanceof Element) || !containerRef) {
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
    html();
    cleanupIcons();
    queueMicrotask(() => {
      if (!disposed) {
        enhanceCodeBlocks();
      }
    });
  });

  onMount(() => {
    containerRef?.addEventListener("click", handleCopyClick);
  });

  onCleanup(() => {
    disposed = true;
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
