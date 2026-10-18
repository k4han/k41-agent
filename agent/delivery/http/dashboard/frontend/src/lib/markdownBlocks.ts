import DOMPurify from "dompurify";
import { marked, type TokensList } from "marked";

function sanitize(html: string): string {
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "rel"],
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["style"],
  });
}

/** Reuse parsed and sanitized blocks while the trailing Markdown grows. */
export function createMarkdownBlocks() {
  let previous: { raw: string; type: string; html: string }[] = [];
  let previousLinks = "";

  return (source: string): string[] => {
    const tokens = marked.lexer(source);
    let hasHtml = false;
    marked.walkTokens(tokens, (token) => {
      if (token.type === "html") hasHtml = true;
    });
    // Raw HTML may open a tag in one block and close it in another. Keep
    // sanitizing the complete document in that case to preserve its tree.
    if (hasHtml) {
      previous = [];
      previousLinks = "";
      return [sanitize(marked.parser(tokens))];
    }

    const links = JSON.stringify(tokens.links);
    const reusable = links === previousLinks;
    const next = tokens.map((token, index) => {
      const cached = reusable ? previous[index] : undefined;
      if (cached && cached.raw === token.raw && cached.type === token.type) return cached;
      // Reference definitions are shared across all top-level blocks,
      // including definitions that arrive after a streamed paragraph.
      const block = Object.assign([token], { links: tokens.links }) as TokensList;
      return { raw: token.raw, type: token.type, html: sanitize(marked.parser(block)) };
    });
    previous = next;
    previousLinks = links;
    return next.map((block) => block.html);
  };
}
