import { ArrowDownToLine, FileText, RefreshCw } from "lucide-solid";
import { For, Show } from "solid-js";

import { useLogViewer } from "@/lib/useLogViewer";
import { SettingsLayout } from "./SettingsLayout";

export function LogsPage() {
  let viewport: HTMLDivElement | undefined;
  const logs = useLogViewer(() => viewport);

  return (
    <SettingsLayout title="Logs" contentWidth="wide">
      <div class="stack settings-page-stack logs-page">
        <p class="settings-page-description">View recent output or scroll up to load older logs, including rotated files.</p>
        <section class="panel settings-section-card">
          <div class="panel-header split logs-toolbar">
            <label class="row-wrap" for="log-source">
              <FileText size={16} /> Log source
              <select id="log-source" class="input" value={logs.source()} onChange={event => logs.selectSource(event.currentTarget.value)}>
                <For each={logs.sources()}>{source => <option value={source.id}>{source.label}</option>}</For>
              </select>
            </label>
            <button class="btn btn-secondary btn-sm" type="button" onClick={logs.latest}>
              <ArrowDownToLine size={14} /> Latest logs
            </button>
          </div>
          <Show when={logs.error()}>
            <div class="panel-body logs-error" role="alert">
              <span>{logs.error()}</span>
              <button class="btn btn-secondary btn-sm" type="button" disabled={logs.loading()} onClick={logs.retry}>
                <RefreshCw size={14} /> Retry
              </button>
            </div>
          </Show>
          <div class="logs-status hint" role="status" aria-live="polite">
            {logs.loading() ? "Loading logs…" : !logs.loaded() ? "" : logs.hasOlder() ? "Scroll up for older logs" : "Start of retained logs"}
            <Show when={logs.hasOlder()}>
              <button class="btn btn-secondary btn-sm" type="button" disabled={logs.loading() || !!logs.error()} onClick={logs.older}>Load older logs</button>
            </Show>
            <Show when={logs.newLogs()}>
              <button class="btn btn-secondary btn-sm" type="button" onClick={logs.latest}>New logs available · Jump to latest</button>
            </Show>
          </div>
          <div class="logs-viewport" ref={viewport} onScroll={logs.onScroll} tabIndex={0} aria-label="Log output">
            <Show when={logs.chunks().length} fallback={
              <div class="logs-empty hint">{logs.error() ? "Log output is unavailable." : logs.loaded() ? "No log output yet. Waiting for new logs…" : "Loading logs…"}</div>
            }>
              <pre class="logs-output"><For each={logs.chunks()}>{chunk => <span data-log-chunk={chunk.id}>{chunk.page.text}</span>}</For></pre>
            </Show>
          </div>
          <div class="logs-footer hint">Updates every 2 seconds. Files rotate at 10 MiB, with up to 5 backups per source.</div>
        </section>
      </div>
    </SettingsLayout>
  );
}
