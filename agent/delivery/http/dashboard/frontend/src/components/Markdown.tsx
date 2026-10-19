import DOMPurify from "dompurify";
import { Check, Code2, Copy, Eye } from "lucide-solid";
import { marked } from "marked";
import {
  batch, createEffect, createMemo, createSignal, For, onCleanup, onMount, untrack,
  type Accessor, type Setter,
} from "solid-js";
import { render } from "solid-js/web";

import { highlightCode, languageFromName } from "@/lib/codeHighlight";
import { rewriteGeneratedImagePaths } from "@/lib/generatedImages";
import { createMarkdownBlocks, type MarkdownBlock, type MarkdownMermaidBlock } from "@/lib/markdownBlocks";
import { createDarkMode } from "@/lib/theme";

marked.setOptions({
  gfm: true,
  breaks: true,
});

type MermaidApi = typeof import("mermaid").default;
type PeleApi = typeof import("pele/lazy");

interface MarkdownBlockState {
  content: Accessor<MarkdownBlock>;
  setContent: Setter<MarkdownBlock>;
}

interface MarkdownNode {
  node: Node;
}

interface MarkdownDomBlock {
  anchor: Text;
  nodes: MarkdownNode[];
  sources: Set<HTMLPreElement>;
  frames: Set<HTMLElement>;
  mermaidBlocks: readonly MarkdownMermaidBlock[];
}

const DIAGRAM_FONT_FAMILY =
  'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

let mermaidPromise: Promise<MermaidApi> | null = null;
let pelePromise: Promise<PeleApi> | null = null;
let nextMermaidRenderId = 1;

function getPele(): Promise<PeleApi> {
  if (!pelePromise) {
    pelePromise = import("pele/lazy").catch((error) => {
      pelePromise = null;
      throw error;
    });
  }
  return pelePromise;
}

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
  const fontFamily = DIAGRAM_FONT_FAMILY;

  return {
    htmlLabels: false,
    startOnLoad: false,
    suppressErrorRendering: true,
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

export function Markdown(props: {
  text: string;
  class?: string;
  deferMermaid?: boolean;
  deferHighlight?: boolean;
  threadId?: string | null;
}) {
  let containerRef: HTMLDivElement | undefined;
  let disposed = false;
  let mermaidRenderFrame: number | undefined;
  let iconDisposers = new Set<() => void>();
  let iconDisposerByElement = new WeakMap<Element, () => void>();
  const sourceMarkup = new WeakMap<Node, string>();
  const nodeSlots = new WeakMap<Node, MarkdownNode>();
  const highlightInFlight = new WeakMap<HTMLPreElement, symbol>();
  const pendingHighlights = new Set<HTMLPreElement>();
  const pendingMermaidFrames = new Set<HTMLElement>();
  const mermaidFrames = new Set<HTMLElement>();
  const mermaidOwners = new WeakMap<Element, MarkdownDomBlock>();
  const dirtyMermaidBlocks = new Set<MarkdownDomBlock>();
  const dark = createDarkMode();
  const renderBlocks = createMarkdownBlocks();
  let blockStates: (MarkdownBlockState | undefined)[] = [];
  const [blockRows, setBlockRows] = createSignal<MarkdownBlockState[]>([]);

  const blockUpdate = createMemo(() => {
    const source = rewriteGeneratedImagePaths(props.text || "", props.threadId);
    return renderBlocks(source);
  });

  const cleanupIcons = () => {
    iconDisposers.forEach((dispose) => dispose());
    iconDisposers.clear();
    iconDisposerByElement = new WeakMap<Element, () => void>();
  };

  const cleanupNode = (node: Node) => {
    nodeSlots.delete(node);
    if (!(node instanceof Element)) return;
    const elements = [node, ...node.querySelectorAll("button, pre, .markdown-code-title-icon, .markdown-mermaid-title-icon, .markdown-mermaid-frame")];
    for (const element of elements) {
      const dispose = iconDisposerByElement.get(element);
      if (dispose) {
        dispose();
        iconDisposers.delete(dispose);
        iconDisposerByElement.delete(element);
      }
      if (element instanceof HTMLPreElement) {
        pendingHighlights.delete(element);
        highlightInFlight.delete(element);
      }
      const owner = mermaidOwners.get(element);
      if (owner) {
        if (element instanceof HTMLPreElement) owner.sources.delete(element);
        else if (element instanceof HTMLElement) {
          owner.frames.delete(element);
          pendingMermaidFrames.delete(element);
          mermaidFrames.delete(element);
        }
        mermaidOwners.delete(element);
      }
    }
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

  // Preserve completed blocks and their controls as the trailing Markdown grows.
  // Update compatible frames in place and invalidate asynchronous work when
  // their source changes, retaining copy controls and the selected Mermaid view.
  const updateFramedCode = (frame: HTMLElement, nextPre: HTMLPreElement, markup: string): boolean => {
    if (!frame.classList.contains("markdown-code-frame")) {
      return false;
    }
    const currentPre = frame.querySelector("pre");
    if (!currentPre) {
      return false;
    }
    const oldText = currentPre.querySelector("code")?.textContent ?? currentPre.textContent ?? "";
    const newText = nextPre.querySelector("code")?.textContent ?? nextPre.textContent ?? "";
    const language = codeBlockLanguage(nextPre);
    if (isMermaidLanguage(language)) {
      return false;
    }
    if (oldText === newText && codeBlockLanguage(currentPre) === language) {
      sourceMarkup.set(frame, markup);
      return true;
    }
    const currentCode = currentPre.querySelector("code");
    const nextCode = nextPre.querySelector("code");
    highlightInFlight.delete(currentPre);
    if (currentCode && nextCode) {
      currentCode.textContent = nextCode.textContent;
      currentCode.className = nextCode.className;
    } else {
      currentPre.textContent = nextPre.textContent;
    }
    currentPre.classList.remove("markdown-code-highlighted", "markdown-code-plain");
    pendingHighlights.add(currentPre);
    const label = frame.querySelector(".markdown-code-language");
    if (label) {
      label.textContent = formatCodeLanguage(language);
    }
    sourceMarkup.set(frame, markup);
    if (!untrack(() => Boolean(props.deferHighlight))) {
      void highlightBlock(currentPre, language);
    }
    return true;
  };

  const updateFramedMermaid = (frame: HTMLElement, nextPre: HTMLPreElement, markup: string): boolean => {
    if (!frame.classList.contains("markdown-mermaid-frame")) {
      return false;
    }
    if (!isMermaidLanguage(codeBlockLanguage(nextPre))) {
      return false;
    }
    const newSource = normalizeMermaidSource(nextPre.querySelector("code")?.textContent ?? nextPre.textContent ?? "");
    if (normalizeMermaidSource(newSource) === normalizeMermaidSource(frame.dataset.mermaidSource ?? "")) {
      sourceMarkup.set(frame, markup);
      return true;
    }
    frame.dataset.mermaidSource = newSource;
    delete frame.dataset.mermaidRenderKey;
    delete frame.dataset.diagramRenderer;
    pendingMermaidFrames.add(frame);
    const rawCode = frame.querySelector(".markdown-mermaid-raw code");
    if (rawCode) {
      rawCode.textContent = newSource;
    }
    const diagram = frame.querySelector<HTMLElement>(".markdown-mermaid");
    if (diagram) {
      diagram.classList.remove("markdown-mermaid-error");
      diagram.classList.add("is-loading");
      diagram.setAttribute("aria-busy", "true");
      diagram.textContent = "Rendering diagram...";
    }
    frame.classList.remove("markdown-mermaid-frame-error");
    sourceMarkup.set(frame, markup);
    return true;
  };

  const mermaidSourceFor = (element: Element): string => {
    if (element instanceof HTMLPreElement) {
      const code = element.querySelector("code");
      return normalizeMermaidSource(code?.textContent || element.textContent || "");
    }
    return normalizeMermaidSource((element as HTMLElement).dataset.mermaidSource || "");
  };

  const collectBlockMermaidElements = (block: MarkdownDomBlock): HTMLElement[] => {
    const elements: HTMLElement[] = [];
    for (const { node } of block.nodes) {
      if (!(node instanceof Element)) continue;
      if (
        node instanceof HTMLElement
        && (node.classList.contains("markdown-mermaid-frame")
          || (node instanceof HTMLPreElement && node.classList.contains("markdown-mermaid-source")))
      ) {
        elements.push(node);
      }
      elements.push(...node.querySelectorAll<HTMLElement>(".markdown-mermaid-frame, pre.markdown-mermaid-source"));
    }
    return elements;
  };

  // A growing list or blockquote replaces its whole top-level node. Detach the
  // rendered diagrams first so they can be restored in place instead of
  // re-rendering and losing their selected view on every streamed update.
  const detachMermaidFrames = (root: Node): HTMLElement[] => {
    if (!(root instanceof Element) || root.classList.contains("markdown-mermaid-frame")) {
      return [];
    }
    const frames = Array.from(root.querySelectorAll<HTMLElement>(".markdown-mermaid-frame"));
    for (const frame of frames) frame.remove();
    return frames;
  };

  const restoreMermaidFrames = (root: Node, frames: HTMLElement[], block: MarkdownDomBlock) => {
    if (frames.length === 0) return;
    if (!(root instanceof Element)) {
      for (const frame of frames) {
        cleanupNode(frame);
        frame.remove();
      }
      return;
    }

    const bySource = new Map<string, HTMLElement[]>();
    for (const frame of frames) {
      const source = normalizeMermaidSource(frame.dataset.mermaidSource || "");
      const queue = bySource.get(source);
      if (queue) queue.push(frame);
      else bySource.set(source, [frame]);
    }

    const pres: HTMLPreElement[] = [];
    if (root instanceof HTMLPreElement && root.classList.contains("markdown-mermaid-source")) {
      pres.push(root);
    }
    pres.push(...root.querySelectorAll<HTMLPreElement>("pre.markdown-mermaid-source"));
    for (const pre of pres) {
      const source = mermaidSourceFor(pre);
      const frame = bySource.get(source)?.shift();
      if (!frame) continue;
      block.sources.delete(pre);
      mermaidOwners.delete(pre);
      preserveMarkup(pre, frame);
      pre.replaceWith(frame);
      block.frames.add(frame);
      mermaidFrames.add(frame);
      mermaidOwners.set(frame, block);
    }

    for (const queue of bySource.values()) {
      for (const frame of queue) {
        cleanupNode(frame);
        frame.remove();
      }
    }
  };

  const updateHtml = (block: MarkdownDomBlock, html: string) => {
    if (!containerRef) return;
    const template = document.createElement("template");
    template.innerHTML = html;
    const nextNodes = Array.from(template.content.childNodes, (node) => ({
      node,
      markup: node instanceof Element ? node.outerHTML : node.textContent || "",
    }));
    const addedNodes: Node[] = [];
    const preservedFrames: { root: Node; frames: HTMLElement[] }[] = [];
    for (let index = 0; index < nextNodes.length; index += 1) {
      const { node, markup } = nextNodes[index];
      const current = block.nodes[index]?.node;
      if (current && sourceMarkup.get(current) === markup) continue;
      const next = node.cloneNode(true);
      if (
        current instanceof HTMLElement &&
        next instanceof Element &&
        next.tagName === "PRE"
      ) {
        if (updateFramedCode(current, next as HTMLPreElement, markup)) continue;
        if (updateFramedMermaid(current, next as HTMLPreElement, markup)) continue;
      }
      sourceMarkup.set(next, markup);
      if (current) {
        const frames = detachMermaidFrames(current);
        cleanupNode(current);
        current.parentNode?.replaceChild(next, current);
        if (frames.length > 0) preservedFrames.push({ root: next, frames });
      } else block.anchor.before(next);
      const slot = { node: next };
      block.nodes[index] = slot;
      nodeSlots.set(next, slot);
      addedNodes.push(next);
    }
    while (block.nodes.length > nextNodes.length) {
      const removed = block.nodes.pop()!.node;
      cleanupNode(removed);
      removed.parentNode?.removeChild(removed);
    }
    enhanceCodeBlocks(block, addedNodes);
    for (const { root, frames } of preservedFrames) restoreMermaidFrames(root, frames, block);
  };

  const preserveMarkup = (source: Node, target: Node) => {
    const markup = sourceMarkup.get(source);
    if (markup !== undefined) sourceMarkup.set(target, markup);
    const slot = nodeSlots.get(source);
    if (slot) {
      slot.node = target;
      nodeSlots.delete(source);
      nodeSlots.set(target, slot);
    }
  };

  const highlightBlock = async (pre: HTMLPreElement, language: string) => {
    if (highlightInFlight.has(pre)) {
      return;
    }
    const code = pre.querySelector("code");
    const source = code?.textContent || pre.textContent || "";
    if (!source.trim()) {
      return;
    }

    const request = Symbol();
    highlightInFlight.set(pre, request);
    const isCurrent = () => !disposed && pre.isConnected && highlightInFlight.get(pre) === request;
    try {
      const highlighted = await highlightCode(source, languageFromName(language), true);
      if (!isCurrent()) {
        return;
      }

      const template = document.createElement("template");
      template.innerHTML = DOMPurify.sanitize(highlighted);
      const highlightedCode = template.content.querySelector("code");
      if (highlightedCode && code) {
        code.innerHTML = highlightedCode.innerHTML;
        pre.classList.add("markdown-code-highlighted");
        pendingHighlights.delete(pre);
      }
    } catch {
      if (isCurrent()) {
        pre.classList.add("markdown-code-plain");
        pendingHighlights.delete(pre);
      }
    } finally {
      if (highlightInFlight.get(pre) === request) {
        highlightInFlight.delete(pre);
      }
    }
  };

  // Highlight framed code blocks that were skipped while deferHighlight was on
  // (e.g. during streaming, to avoid re-running Shiki on every batched update).
  const highlightPendingBlocks = () => {
    for (const pre of pendingHighlights) void highlightBlock(pre, codeBlockLanguage(pre));
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
    delete frame.dataset.diagramRenderer;
    diagram.className = "markdown-mermaid is-loading";
    if (frame.dataset.mermaidView === "source") {
      diagram.classList.add("is-hidden");
    }
    diagram.setAttribute("aria-busy", "true");
    diagram.textContent = "Rendering diagram...";

    const isCurrentRender = () =>
      !disposed && frame.isConnected && frame.dataset.mermaidRenderKey === renderKey;

    try {
      let result: { svg: string; bindFunctions?: (element: Element) => void };
      let renderer: "pele" | "mermaid" = "pele";
      try {
        const pele = await getPele();
        if (!isCurrentRender()) return;
        result = await pele.renderAsync(source, {
          fontFamily: DIAGRAM_FONT_FAMILY,
          idPrefix: renderId,
          links: false,
          images: false,
          maxEdges: 500,
        });
      } catch {
        if (!isCurrentRender()) return;
        const mermaid = await getMermaid();
        if (!isCurrentRender()) return;
        mermaid.initialize(mermaidConfig(darkMode));
        // Keep Mermaid's temporary SVGs measurable without affecting the chat layout.
        const renderTarget = document.createElement("div");
        renderTarget.setAttribute("aria-hidden", "true");
        renderTarget.style.cssText =
          "position: fixed; inset: 0; visibility: hidden; pointer-events: none; overflow: hidden;";
        document.body.appendChild(renderTarget);
        try {
          result = await mermaid.render(renderId, source, renderTarget);
        } finally {
          renderTarget.remove();
        }
        renderer = "mermaid";
      }
      if (!isCurrentRender()) return;

      frame.dataset.diagramRenderer = renderer;
      diagram.className = "markdown-mermaid";
      diagram.removeAttribute("aria-busy");
      diagram.innerHTML = DOMPurify.sanitize(result.svg);
      setMermaidFrameView(
        frame,
        frame.dataset.mermaidView === "source" ? "source" : "diagram",
      );
      result.bindFunctions?.(diagram);
    } catch {
      if (isCurrentRender()) {
        renderMermaidFallback(frame, source);
      }
    }
  };

  const renderMermaidPre = async (
    pre: HTMLPreElement,
    source: string,
    darkMode: boolean,
    block: MarkdownDomBlock,
  ) => {
    const frame = createMermaidFrame(source, darkMode);
    block.sources.delete(pre);
    mermaidOwners.delete(pre);
    block.frames.add(frame);
    mermaidFrames.add(frame);
    mermaidOwners.set(frame, block);
    pre.classList.add("is-rendering");
    preserveMarkup(pre, frame);
    pre.replaceWith(frame);
    await renderMermaidFrame(frame, source, darkMode);
  };

  const enhanceCodeBlocks = (block: MarkdownDomBlock, nodes: Node[]) => {
    const pres: HTMLPreElement[] = [];
    for (const node of nodes) {
      if (node instanceof HTMLPreElement) pres.push(node);
      else if (node instanceof Element) pres.push(...node.querySelectorAll<HTMLPreElement>("pre"));
    }
    pres.forEach((pre) => {
      if (pre.closest(".markdown-code-frame, .markdown-mermaid-frame, .markdown-mermaid-raw")) {
        return;
      }

      const language = codeBlockLanguage(pre);
      if (isMermaidLanguage(language)) {
        pre.classList.add("markdown-mermaid-source");
        block.sources.add(pre);
        mermaidOwners.set(pre, block);
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

      preserveMarkup(pre, frame);
      pre.replaceWith(frame);
      frame.append(header, pre);
      pendingHighlights.add(pre);
      if (!props.deferHighlight) {
        void highlightBlock(pre, language);
      }
    });
  };

  const renderReadyMermaidCodeBlocks = (darkMode: boolean) => {
    for (const block of dirtyMermaidBlocks) {
      const elements = collectBlockMermaidElements(block);
      const expected = block.mermaidBlocks;
      let expectedIndex = 0;
      for (const element of elements) {
        if (expectedIndex >= expected.length) break;
        const expectedBlock = expected[expectedIndex];
        const source = mermaidSourceFor(element);
        if (source !== expectedBlock.source) continue;
        expectedIndex += 1;
        if (!expectedBlock.complete || !source) continue;
        if (element instanceof HTMLPreElement) {
          if (block.sources.has(element)) {
            void renderMermaidPre(element, source, darkMode, block);
          }
        } else if (pendingMermaidFrames.has(element)) {
          pendingMermaidFrames.delete(element);
          void renderMermaidFrame(element, source, darkMode);
        }
      }
    }
    dirtyMermaidBlocks.clear();
  };

  const rerenderMermaidDiagrams = (darkMode: boolean) => {
    const theme = darkMode ? "dark" : "light";
    mermaidFrames.forEach((frame) => {
      if (pendingMermaidFrames.has(frame) || frame.dataset.mermaidTheme === theme) return;
      if (frame.dataset.diagramRenderer === "pele") {
        frame.dataset.mermaidTheme = theme;
        return;
      }
      const source = frame.dataset.mermaidSource || "";
      if (source.trim()) void renderMermaidFrame(frame, source, darkMode);
    });
  };

  const clearMermaidRenderFrame = () => {
    if (mermaidRenderFrame !== undefined) {
      window.cancelAnimationFrame(mermaidRenderFrame);
      mermaidRenderFrame = undefined;
    }
  };

  const scheduleMermaidRender = () => {
    // Later prose must not postpone a completed diagram or request another render.
    if (dirtyMermaidBlocks.size === 0 || mermaidRenderFrame !== undefined) return;
    mermaidRenderFrame = window.requestAnimationFrame(() => {
      mermaidRenderFrame = undefined;
      if (!disposed && !props.deferMermaid) {
        renderReadyMermaidCodeBlocks(dark());
      }
    });
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

  const mountMarkdownBlock = (state: MarkdownBlockState) => {
    // Keep Solid's insertion point before the manually managed block content.
    const start = document.createTextNode("");
    const block: MarkdownDomBlock = {
      anchor: document.createTextNode(""),
      nodes: [],
      sources: new Set(),
      frames: new Set(),
      mermaidBlocks: [],
    };
    let previousHtml: string | undefined;
    createEffect(() => {
      const content = state.content();
      untrack(() => {
        block.mermaidBlocks = content.mermaidBlocks;
        if (content.html !== previousHtml) {
          updateHtml(block, content.html);
          previousHtml = content.html;
        }
        if (block.sources.size > 0 || Array.from(block.frames).some((frame) => pendingMermaidFrames.has(frame))) {
          dirtyMermaidBlocks.add(block);
          if (!props.deferMermaid) scheduleMermaidRender();
        }
      });
    });
    onCleanup(() => {
      dirtyMermaidBlocks.delete(block);
      for (const { node } of block.nodes) {
        cleanupNode(node);
        node.parentNode?.removeChild(node);
      }
    });
    return [start, block.anchor];
  };

  createEffect(() => {
    const update = blockUpdate();
    untrack(() => batch(() => {
      let rowsChanged = blockStates.length !== update.length;
      const next = rowsChanged ? blockStates.slice(0, update.length) : blockStates;
      next.length = update.length;
      update.blocks.forEach((content, index) => {
        const position = update.start + index;
        const current = next[position];
        // Whitespace and reference definitions have no DOM to mount.
        if (!content.html) {
          if (current) {
            next[position] = undefined;
            rowsChanged = true;
          }
        } else if (current) current.setContent(content);
        else {
          const [value, setValue] = createSignal(content);
          next[position] = { content: value, setContent: setValue };
          rowsChanged = true;
        }
      });
      blockStates = next;
      if (rowsChanged) setBlockRows(next.filter((state): state is MarkdownBlockState => state !== undefined));
    }));
  });

  let previousDeferMermaid = Boolean(props.deferMermaid);
  createEffect(() => {
    const deferMermaid = Boolean(props.deferMermaid);
    if (deferMermaid === previousDeferMermaid) {
      return;
    }
    previousDeferMermaid = deferMermaid;
    if (deferMermaid) {
      clearMermaidRenderFrame();
      return;
    }
    scheduleMermaidRender();
  });

  let previousDeferHighlight = Boolean(props.deferHighlight);
  createEffect(() => {
    const deferHighlight = Boolean(props.deferHighlight);
    if (deferHighlight === previousDeferHighlight) {
      return;
    }
    previousDeferHighlight = deferHighlight;
    if (deferHighlight) {
      return;
    }
    queueMicrotask(() => {
      if (!disposed) {
        highlightPendingBlocks();
      }
    });
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
    clearMermaidRenderFrame();
    cleanupIcons();
    pendingHighlights.clear();
    pendingMermaidFrames.clear();
    mermaidFrames.clear();
    dirtyMermaidBlocks.clear();
    containerRef?.removeEventListener("click", handleCopyClick);
  });

  return (
    <div
      ref={containerRef}
      class={`markdown ${props.class || ""}`}
    >
      <For each={blockRows()}>{mountMarkdownBlock}</For>
    </div>
  );
}
