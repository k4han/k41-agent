import DOMPurify from "dompurify";
import { marked, type Token, type TokensList } from "marked";

export interface MarkdownMermaidBlock {
  source: string;
  complete: boolean;
}

export interface MarkdownBlock {
  html: string;
  mermaidBlocks: readonly MarkdownMermaidBlock[];
}

export interface MarkdownBlockUpdate {
  start: number;
  blocks: readonly MarkdownBlock[];
  length: number;
}

interface CachedBlock extends MarkdownBlock {
  raw: string;
  type: string;
  hasHtml: boolean;
}

function sanitize(html: string): string {
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "rel"],
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["style"],
  });
}

function isClosedFence(raw: string): boolean {
  const opening = raw.match(/^ {0,3}(`{3,}|~{3,})[^\n]*\n/);
  if (!opening) return false;
  const fence = opening[1];
  const closing = new RegExp(`(?:^|\\n) {0,3}${fence[0]}{${fence.length},}[ \\t]*\\n?$`);
  return closing.test(raw.slice(opening[0].length));
}

function inspectBlock(token: Token) {
  let hasHtml = false;
  const mermaidBlocks: MarkdownMermaidBlock[] = [];
  marked.walkTokens([token], (child) => {
    if (child.type === "html") hasHtml = true;
    if (child.type !== "code") return;
    const language = (child.lang || "").trim().split(/\s+/)[0].toLowerCase();
    if (language !== "mermaid" && language !== "mmd") return;
    mermaidBlocks.push({
      source: child.text.trim(),
      complete: isClosedFence(child.raw),
    });
  });
  return { hasHtml, mermaidBlocks };
}

/** Re-lex the last open block and return only the affected suffix. */
export function createMarkdownBlocks() {
  let previous: CachedBlock[] = [];
  let previousSource = "";
  let previousLinks = "";
  let restartIndex = 0;
  let restartOffset = 0;
  let canAppend = false;

  return (input: string): MarkdownBlockUpdate => {
    // Token raw offsets refer to Marked's normalized line endings.
    const source = input.includes("\r") ? input.replace(/\r\n?/g, "\n") : input;
    if (source === previousSource) return { start: previous.length, blocks: [], length: previous.length };
    const append = canAppend && source.startsWith(previousSource);
    let start = append ? restartIndex : 0;
    let offset = append ? restartOffset : 0;
    let tokens = marked.lexer(source.slice(offset));
    // A new definition can resolve references anywhere in the document.
    if (append && Object.keys(tokens.links).length > 0) {
      start = 0;
      offset = 0;
      tokens = marked.lexer(source);
    }
    let inspection = tokens.map((token, index) => {
      const cached = previous[start + index];
      return cached?.raw === token.raw && cached.type === token.type ? cached : inspectBlock(token);
    });
    if (start > 0 && inspection.some((block) => block.hasHtml)) {
      start = 0;
      offset = 0;
      tokens = marked.lexer(source);
      inspection = tokens.map(inspectBlock);
    }
    previousSource = source;
    // Raw HTML may open a tag in one block and close it in another. Keep
    // sanitizing the complete document in that case to preserve its tree.
    if (inspection.some((block) => block.hasHtml)) {
      const mermaidBlocks = inspection.flatMap((block) => block.mermaidBlocks);
      previous = [{ raw: source, type: "html", html: sanitize(marked.parser(tokens)), hasHtml: true, mermaidBlocks }];
      previousLinks = "";
      canAppend = false;
      return { start: 0, blocks: previous, length: 1 };
    }

    const links = JSON.stringify(tokens.links);
    const reusable = links === previousLinks;
    const next = tokens.map((token, index) => {
      const cached = reusable ? previous[start + index] : undefined;
      if (cached && cached.raw === token.raw && cached.type === token.type) return cached;
      const block = Object.assign([token], { links: tokens.links }) as TokensList;
      const { hasHtml, mermaidBlocks } = inspection[index];
      return { raw: token.raw, type: token.type, html: sanitize(marked.parser(block)), hasHtml, mermaidBlocks };
    });
    canAppend = Object.keys(tokens.links).length === 0 && !marked.defaults.pedantic && !marked.defaults.extensions;
    restartIndex = start;
    restartOffset = offset;
    for (let index = 0; index < tokens.length; index += 1) {
      const token = tokens[index];
      if (!source.startsWith(token.raw, offset)) canAppend = false;
      offset += token.raw.length;
      // Only commit blocks separated by a blank line. Consecutive paragraphs
      // can be regrouped by table/setext lookahead. Lists and indented code
      // can also absorb later content across blank lines, so keep them open.
      const stableType = ["paragraph", "heading", "hr", "table"].includes(token.type)
        || (token.type === "code" && isClosedFence(token.raw));
      const followingSpace = tokens[index + 1]?.type === "space" ? tokens[index + 1].raw : "";
      if (stableType && /\n[ \t]*\n[ \t]*$/.test(token.raw + followingSpace)) {
        restartIndex = start + index + 1 + (followingSpace ? 1 : 0);
        restartOffset = offset + followingSpace.length;
      }
    }
    if (offset !== source.length) canAppend = false;
    previous.length = start;
    for (const block of next) previous.push(block);
    previousLinks = links;
    return { start, blocks: next, length: previous.length };
  };
}
