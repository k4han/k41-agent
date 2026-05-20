import { html as diff2htmlRender } from "diff2html";
import type { Diff2HtmlConfig } from "diff2html";

function ensureGitHeader(diff: string, path: string): string {
  const trimmed = diff.trimStart();
  if (trimmed.startsWith("diff --git")) {
    return diff;
  }
  const safePath = path || "file";
  const header = `diff --git a/${safePath} b/${safePath}\n`;
  if (trimmed.startsWith("--- ")) {
    return header + diff;
  }
  return diff;
}

export function renderUnifiedDiffHtml(
  diff: string,
  path: string,
  options: { sideBySide?: boolean } = {},
): string {
  const normalized = ensureGitHeader(diff || "", path);
  const config: Diff2HtmlConfig = {
    drawFileList: false,
    matching: "lines",
    outputFormat: options.sideBySide ? "side-by-side" : "line-by-line",
    renderNothingWhenEmpty: false,
  };
  return diff2htmlRender(normalized, config);
}
