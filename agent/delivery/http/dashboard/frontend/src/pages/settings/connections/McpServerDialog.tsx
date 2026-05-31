import { createSignal, For, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";
import type {
  McpServerInput,
  McpTestResult,
  McpTransport,
  McpToolInfo,
} from "@/types";

import { getServerIcon, getToolIcon } from "./mcpIcons";

type DialogMode = "create" | "edit";

export function McpServerDialog(props: {
  open: boolean;
  mode: DialogMode;
  initial?: Partial<McpServerInput> & {
    name?: string;
    tools?: McpToolInfo[];
    error?: string;
    loaded?: boolean;
  };
  onClose: () => void;
  onSubmit: (payload: McpServerInput) => Promise<void> | void;
}) {
  const { showToast } = useToast();
  const [activeTab, setActiveTab] = createSignal<"tools" | "config">(
    props.mode === "edit" && props.initial?.loaded ? "tools" : "config",
  );
  const [name, setName] = createSignal(props.initial?.name ?? "");
  const [transport, setTransport] = createSignal<McpTransport>(
    props.initial?.transport ?? "stdio",
  );
  const [command, setCommand] = createSignal(props.initial?.command ?? "");
  const [argsText, setArgsText] = createSignal(
    (props.initial?.args ?? []).join("\n"),
  );
  const [envText, setEnvText] = createSignal(
    Object.entries(props.initial?.env ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
  );
  const [url, setUrl] = createSignal(props.initial?.url ?? "");
  const [headersText, setHeadersText] = createSignal(
    Object.entries(props.initial?.headers ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
  );
  const [enabled, setEnabled] = createSignal(props.initial?.enabled ?? true);

  const [testing, setTesting] = createSignal(false);
  const [testResult, setTestResult] = createSignal<McpTestResult | null>(null);
  const [submitting, setSubmitting] = createSignal(false);

  const parseList = (text: string): string[] =>
    text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

  const parseKeyValueMap = (text: string): Record<string, string> => {
    const result: Record<string, string> = {};
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const idx = trimmed.indexOf("=");
      if (idx <= 0) continue;
      const key = trimmed.slice(0, idx).trim();
      const value = trimmed.slice(idx + 1).trim();
      if (key) {
        result[key] = value;
      }
    }
    return result;
  };

  const buildPayload = (): McpServerInput => ({
    name: name().trim(),
    transport: transport(),
    command: command().trim(),
    args: parseList(argsText()),
    env: parseKeyValueMap(envText()),
    url: url().trim(),
    headers: parseKeyValueMap(headersText()),
    enabled: enabled(),
  });

  const runTest = async () => {
    const payload = buildPayload();
    if (!payload.name) {
      showToast("Server name is required to test.", "warning");
      return;
    }
    if (payload.transport === "stdio" && !payload.command) {
      showToast("Command is required for stdio transport.", "warning");
      return;
    }
    if (payload.transport === "streamable_http" && !payload.url) {
      showToast("URL is required for HTTP transport.", "warning");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await postJson<McpTestResult>(
        "/dashboard-api/mcp/servers/test",
        payload,
      );
      setTestResult(result);
      if (result.ok) {
        showToast(`Connected. Found ${result.tools.length} tool(s).`);
      } else {
        showToast(result.error || "Connection failed.", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Test failed.", "error");
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async () => {
    const payload = buildPayload();
    if (!payload.name) {
      showToast("Server name is required.", "warning");
      return;
    }
    setSubmitting(true);
    try {
      await props.onSubmit(payload);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      wide
      title={props.mode === "create" ? "Add MCP server" : `${props.initial?.name} Details`}
      onClose={props.onClose}
      footer={
        <Show
          when={props.mode === "create" || activeTab() === "config"}
          fallback={
            <div class="row-wrap" style={{ "justify-content": "flex-end", width: "100%" }}>
              <button class="btn" type="button" onClick={props.onClose}>
                Close
              </button>
            </div>
          }
        >
          <div class="row-wrap" style={{ "justify-content": "space-between", width: "100%" }}>
            <button
              class="btn"
              type="button"
              onClick={runTest}
              disabled={testing()}
            >
              {testing() ? "Testing..." : "Test connection"}
            </button>
            <div class="row-wrap">
              <button class="btn" type="button" onClick={props.onClose}>
                Cancel
              </button>
              <button
                class="btn btn-primary"
                type="button"
                onClick={handleSubmit}
                disabled={submitting()}
              >
                {submitting() ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </Show>
      }
    >
      <div class="stack">
        <Show when={props.mode === "edit"}>
          <div class="row-wrap" style={{ "margin-bottom": "20px", "border-bottom": "1px solid var(--border-color, #e2e8f0)", "padding-bottom": "8px", gap: "8px" }}>
            <button
              class={`btn btn-sm ${activeTab() === "tools" ? "btn-primary" : ""}`}
              type="button"
              onClick={() => setActiveTab("tools")}
            >
              Tools ({props.initial?.tools?.length ?? 0})
            </button>
            <button
              class={`btn btn-sm ${activeTab() === "config" ? "btn-primary" : ""}`}
              type="button"
              onClick={() => setActiveTab("config")}
            >
              Configuration
            </button>
          </div>
        </Show>

        <Show
          when={props.mode === "create" || activeTab() === "config"}
          fallback={
            <div class="stack" style={{ gap: "12px" }}>
              <Show when={props.initial?.error}>
                <div class="panel" style={{ padding: "12px", "background-color": "rgba(239, 68, 68, 0.08)", "border": "1px solid rgb(239, 68, 68)", "border-radius": "6px", "margin-bottom": "8px" }}>
                  <div style={{ "font-weight": "bold", "color": "rgb(239, 68, 68)", "margin-bottom": "4px", "font-size": "13px" }}>Server Connection Error</div>
                  <div class="mono" style={{ "font-size": "12px", "color": "rgb(239, 68, 68)", "word-break": "break-all" }}>{props.initial?.error}</div>
                </div>
              </Show>

              <Show when={props.initial?.tools && props.initial.tools.length > 0} fallback={
                <div class="empty" style={{ padding: "32px 0", "text-align": "center" }}>
                  No tools exposed by this server. Try reloading the server tools or verifying its configuration.
                </div>
              }>
                <div style={{ display: "flex", "align-items": "center", gap: "10px", "margin-bottom": "8px", "padding": "12px", "background-color": "rgba(0, 0, 0, 0.02)", "border": "1px solid #e2e8f0", "border-radius": "8px" }}>
                  {getServerIcon(props.initial?.name ?? "")}
                  <div>
                    <div style={{ "font-weight": "bold", "font-size": "14px" }}>{props.initial?.name}</div>
                    <div class="hint" style={{ "font-size": "11px", "margin-top": "2px" }}>Exposed tools can be used by selecting them in Agent settings.</div>
                  </div>
                </div>
                <div style={{ display: "flex", "flex-direction": "column", gap: "10px" }}>
                  <For each={props.initial?.tools}>
                    {(tool) => (
                      <div class="panel" style={{ padding: "12px", display: "flex", "flex-direction": "column", gap: "6px" }}>
                        <div style={{ display: "flex", "align-items": "center", "justify-content": "space-between", "flex-wrap": "wrap", gap: "8px" }}>
                          <div style={{ display: "flex", "align-items": "center", gap: "8px" }}>
                            {getToolIcon(tool.name, props.initial?.name ?? "")}
                            <span class="mono" style={{ "font-weight": "bold", "font-size": "13px", color: "var(--color-primary-light, #0076ff)" }}>
                              {tool.name}
                            </span>
                          </div>
                          <span class="hint mono" style={{ "font-size": "11px", opacity: 0.8 }}>
                            {tool.prefixed_name}
                          </span>
                        </div>
                        <Show when={tool.description}>
                          <div class="hint" style={{ "font-size": "12px", "margin-top": "2px" }}>
                            {tool.description}
                          </div>
                        </Show>
                      </div>
                    )}
                  </For>
                </div>
              </Show>
              <div style={{ "margin-top": "16px", display: "flex", "justify-content": "flex-end" }}>
                <button
                  class="btn btn-primary"
                  type="button"
                  onClick={() => setActiveTab("config")}
                >
                  Configure Server
                </button>
              </div>
            </div>
          }
        >
          <div class="grid-2">
            <div class="field">
              <label>Name</label>
              <input
                class="input mono"
                placeholder="my-server"
                value={name()}
                disabled={props.mode === "edit"}
                onInput={(event) => setName(event.currentTarget.value)}
              />
              <div class="hint">Letters, numbers, underscores, hyphens.</div>
            </div>
            <div class="field">
              <label>Transport</label>
              <SelectControl
                value={transport()}
                options={[
                  { value: "stdio", label: "stdio (command)" },
                  { value: "streamable_http", label: "HTTP (URL)" },
                ]}
                onChange={(value) => setTransport(value as McpTransport)}
                ariaLabel="Transport"
              />
            </div>
          </div>

          <Show when={transport() === "stdio"}>
            <div class="field">
              <label>Command</label>
              <input
                class="input mono"
                placeholder="npx"
                value={command()}
                onInput={(event) => setCommand(event.currentTarget.value)}
              />
            </div>
            <div class="field">
              <label>Arguments (one per line)</label>
              <textarea
                class="textarea mono"
                rows={4}
                placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/abs/path"}
                value={argsText()}
                onInput={(event) => setArgsText(event.currentTarget.value)}
              />
            </div>
            <div class="field">
              <label>Environment variables (KEY=VALUE per line)</label>
              <textarea
                class="textarea mono"
                rows={4}
                placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx"
                value={envText()}
                onInput={(event) => setEnvText(event.currentTarget.value)}
              />
            </div>
          </Show>

          <Show when={transport() === "streamable_http"}>
            <div class="field">
              <label>URL</label>
              <input
                class="input mono"
                placeholder="https://example.com/mcp"
                value={url()}
                onInput={(event) => setUrl(event.currentTarget.value)}
              />
            </div>
            <div class="field">
              <label>Headers (KEY=VALUE per line)</label>
              <textarea
                class="textarea mono"
                rows={4}
                placeholder="Authorization=Bearer xxx"
                value={headersText()}
                onInput={(event) => setHeadersText(event.currentTarget.value)}
              />
            </div>
          </Show>

          <div class="field">
            <label>
              <input
                type="checkbox"
                checked={enabled()}
                onChange={(event) => setEnabled(event.currentTarget.checked)}
              />{" "}
              Enabled
            </label>
          </div>

          <Show when={testResult()}>
            {(resultGetter) => {
              const result = resultGetter();
              return (
                <section class="panel">
                  <div class="panel-header">
                    <div class="panel-title">Test result</div>
                    <span
                      class={result.ok ? "badge badge-success" : "badge badge-danger"}
                    >
                      {result.ok ? "ok" : "error"}
                    </span>
                  </div>
                  <div class="panel-body stack">
                    <Show when={!result.ok}>
                      <div class="hint mono">{result.error}</div>
                    </Show>
                    <Show when={result.ok && result.tools.length > 0}>
                      <div class="hint">Tools exposed:</div>
                      <ul class="stack">
                        <For each={result.tools}>
                          {(tool) => (
                            <li class="mono" style={{ "font-size": "12px" }}>
                              <strong>{tool.name}</strong>
                              <Show when={tool.description}>
                                <span class="hint"> — {tool.description}</span>
                              </Show>
                            </li>
                          )}
                        </For>
                      </ul>
                    </Show>
                  </div>
                </section>
              );
            }}
          </Show>
        </Show>
      </div>
    </Dialog>
  );
}
