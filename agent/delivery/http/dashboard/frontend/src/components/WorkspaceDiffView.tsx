import { createMemo } from "solid-js";
import { renderUnifiedDiffHtml } from "@/lib/diffView";

export default function WorkspaceDiffView(props: { diff: string; path: string }) {
  const html = createMemo(() => renderUnifiedDiffHtml(props.diff, props.path, { sideBySide: true }));
  return <div class="workspace-diff2html" innerHTML={html()} />;
}
