import { createEffect, createMemo, createSignal, For, Match, on, Show, Switch, type JSX } from "solid-js";
import { Download } from "lucide-solid";

import { Markdown } from "@/components/Markdown";
import { parseDelimitedPreview, sandboxedWorkspaceHtml, workspacePreviewKind } from "@/lib/workspacePreview";

export function WorkspaceFilePreview(props: {
  path: string;
  content: string;
  binary: boolean;
  truncated: boolean;
  previewUrl: string;
  downloadUrl: string;
  renderSource: () => JSX.Element;
}) {
  const [source, setSource] = createSignal(false);
  const [failed, setFailed] = createSignal(false);
  const kind = createMemo(() => workspacePreviewKind(props.path, props.binary));
  const hasSource = () => !props.binary && ["text", "markdown", "html", "table"].includes(kind());
  const table = createMemo(() => kind() === "table"
    ? parseDelimitedPreview(props.content, props.path.toLowerCase().endsWith(".tsv") ? "\t" : ",")
    : { rows: [], truncated: false });
  createEffect(on(() => props.path, () => { setSource(false); setFailed(false); }));
  createEffect(on(() => props.previewUrl, () => setFailed(false)));

  return (
    <>
      <div class="workspace-preview-toolbar">
        <span>{kind() === "unsupported" ? "File" : kind().toUpperCase()}</span>
        <div class="workspace-preview-actions">
          <Show when={hasSource() && kind() !== "text"}>
            <button class="btn btn-sm" type="button" aria-pressed={source()} onClick={() => setSource(!source())}>
              {source() ? "Preview" : "Source"}
            </button>
          </Show>
          <a class="btn btn-sm" href={props.downloadUrl} download="" title="Download file"><Download size={14} />Download</a>
        </div>
      </div>
      <Show when={!source() && kind() !== "text"} fallback={props.renderSource()}>
        <Show when={!failed()} fallback={<div class="empty compact">Unable to display this file. Download it to open in another application.</div>}>
          <Switch>
            <Match when={kind() === "image"}>
              <div class="workspace-media-preview"><img src={props.previewUrl} alt={props.path} onError={() => setFailed(true)} /></div>
            </Match>
            <Match when={kind() === "pdf"}>
              <iframe class="workspace-document-preview" src={props.previewUrl} title={`PDF preview: ${props.path}`} />
              <div class="hint workspace-hint">If your browser cannot display PDF files, use Download.</div>
            </Match>
            <Match when={kind() === "audio"}>
              <div class="workspace-media-preview"><audio controls preload="metadata" src={props.previewUrl} onError={() => setFailed(true)} /></div>
            </Match>
            <Match when={kind() === "video"}>
              <div class="workspace-media-preview"><video controls preload="metadata" src={props.previewUrl} onError={() => setFailed(true)} /></div>
            </Match>
            <Match when={kind() === "markdown"}>
              <div class="workspace-rendered-preview"><Markdown text={props.content} class="message-markdown" /></div>
            </Match>
            <Match when={kind() === "html"}>
              <iframe class="workspace-document-preview" title={`HTML preview: ${props.path}`} sandbox="" referrerpolicy="no-referrer" srcdoc={sandboxedWorkspaceHtml(props.content)} />
              <div class="hint workspace-hint">Scripts and external resources are disabled in HTML previews.</div>
            </Match>
            <Match when={kind() === "table"}>
              <div class="workspace-table-preview">
                <table><tbody><For each={table().rows}>{(row) => <tr><For each={row}>{(cell) => <td>{cell}</td>}</For></tr>}</For></tbody></table>
                <Show when={!table().rows.length}><div class="empty compact">Empty table.</div></Show>
              </div>
              <Show when={table().truncated}><div class="hint workspace-hint">Showing up to 200 rows and 100 columns. Download for the complete table.</div></Show>
            </Match>
            <Match when={kind() === "unsupported"}>
              <div class="empty compact">No preview is available for this file type. Download it to open in another application.</div>
            </Match>
          </Switch>
        </Show>
      </Show>
      <Show when={props.truncated && hasSource()}><div class="hint workspace-hint">File truncated. Download for the complete file.</div></Show>
    </>
  );
}
