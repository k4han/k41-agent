import { Download, Loader2 } from "lucide-solid";
import { createSignal, createUniqueId, onCleanup, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { apiFetch } from "@/lib/api";
import { createChatExport, downloadChatExport, type ChatExportFormat, type ChatExportTarget } from "@/lib/chatExport";
import { threadApiPath, type ThreadMessagesPayload } from "@/lib/chatThreads";

export function ExportChatDialog(props: { target: ChatExportTarget | null; onClose: () => void }) {
  const [format, setFormat] = createSignal<ChatExportFormat>("markdown");
  const [includeTools, setIncludeTools] = createSignal(false);
  const [exporting, setExporting] = createSignal(false);
  const [error, setError] = createSignal("");
  const formatId = createUniqueId();
  const { showToast } = useToast();
  let controller: AbortController | undefined;
  onCleanup(() => controller?.abort());

  const close = () => {
    controller?.abort();
    controller = undefined;
    setExporting(false);
    setError("");
    props.onClose();
  };

  const download = async () => {
    const target = props.target;
    if (!target || exporting()) return;
    const request = new AbortController();
    controller = request;
    setExporting(true);
    setError("");
    try {
      const payload = await apiFetch<ThreadMessagesPayload>(threadApiPath(target.thread_id, target.checkpoint_id), {
        signal: request.signal,
      });
      if (request.signal.aborted) return;
      downloadChatExport(createChatExport({ ...payload, title: target.title || payload.title }, format(), includeTools()));
      showToast("Conversation exported.", "success");
      close();
    } catch (err) {
      if (!request.signal.aborted) setError(err instanceof Error ? err.message : "Export failed. Please try again.");
    } finally {
      if (controller === request) {
        controller = undefined;
        setExporting(false);
      }
    }
  };

  return (
    <Dialog
      open={props.target !== null}
      title="Export conversation"
      subtitle="Download saved messages from the selected conversation branch."
      icon={<Download size={17} />}
      size="sm"
      onClose={close}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "10px", width: "100%" }}>
          <button class="btn" type="button" onClick={close}>Cancel</button>
          <button class="btn btn-primary" type="button" onClick={() => void download()} disabled={exporting()}>
            <Show when={exporting()} fallback={<Download size={14} />}>
              <Loader2 size={14} class="spin-icon" />
            </Show>
            {exporting() ? "Exporting..." : "Download"}
          </button>
        </div>
      }
    >
      <div class="form-field-group-body">
        <p class="muted" style={{ margin: "0", "overflow-wrap": "anywhere" }}>{props.target?.title || props.target?.thread_id}</p>
        <div class="form-field">
          <label class="form-field-label" for={formatId}>File format</label>
          <select id={formatId} class="input" value={format()} disabled={exporting()}
            onChange={(event) => setFormat(event.currentTarget.value as ChatExportFormat)}>
            <option value="markdown">Markdown (.md)</option>
            <option value="text">Plain text (.txt)</option>
            <option value="json">JSON (.json)</option>
          </select>
        </div>
        <Show when={format() !== "json"}>
          <label class="row-wrap">
            <input type="checkbox" checked={includeTools()} disabled={exporting()}
              onChange={(event) => setIncludeTools(event.currentTarget.checked)} />
            Include tool activity
          </label>
        </Show>
        <p class="hint" style={{ margin: "0" }}>
          {format() === "json"
            ? "JSON includes all saved messages, tool activity, and available attachment data."
            : "Includes attachment names and available text content. Use JSON for embedded attachment data."}
          {" "}Responses in progress are included once saved.
        </p>
        <Show when={error()}><div class="thread-banner thread-banner-error" role="alert">{error()}</div></Show>
      </div>
    </Dialog>
  );
}
