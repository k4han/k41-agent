import { createEffect, createMemo, createSignal, For, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import type { McpInstallResponse, McpSearchResult } from "@/types";

export function McpInstallDialog(props: {
  open: boolean;
  server: McpSearchResult | null;
  agentNames: string[];
  onClose: () => void;
  onSubmit: (payload: {
    agent_name: string;
    registry_name: string;
    version: string;
    target_id: string;
    server_name: string;
    input_values: Record<string, string>;
    auth_method: string;
  }) => Promise<McpInstallResponse> | void;
}) {
  const { showToast } = useToast();
  const [agentName, setAgentName] = createSignal("");
  const [targetId, setTargetId] = createSignal("");
  const [serverName, setServerName] = createSignal("");
  const [values, setValues] = createSignal<Record<string, string>>({});
  const [submitting, setSubmitting] = createSignal(false);
  let lastRegistryName = "";
  let lastTargetId = "";
  let lastOpen = false;

  const target = createMemo(() => {
    const server = props.server;
    if (!server) return null;
    const selected = targetId() || server.install_targets[0]?.id || "";
    return server.install_targets.find((item) => item.id === selected) || null;
  });

  createEffect(() => {
    const server = props.server;
    const isOpen = props.open;
    // Force a re-init whenever the dialog transitions from closed to open,
    // even if the user re-selects the same registry server.
    const reopened = isOpen && !lastOpen;
    lastOpen = isOpen;
    if (!isOpen || !server) {
      if (!isOpen) {
        lastRegistryName = "";
        lastTargetId = "";
      }
      return;
    }
    if (reopened || server.registry_name !== lastRegistryName) {
      lastRegistryName = server.registry_name;
      lastTargetId = server.install_targets[0]?.id || "";
      setAgentName(props.agentNames[0] || "");
      setTargetId(lastTargetId);
      setServerName(server.registry_name.split("/").pop() || server.title);
      setValues({});
      return;
    }
    if (!agentName() && props.agentNames.length > 0) {
      setAgentName(props.agentNames[0]);
    }
    if (!targetId() && server.install_targets.length > 0) {
      const first = server.install_targets[0].id;
      lastTargetId = first;
      setTargetId(first);
    }
    if (!serverName()) {
      setServerName(server.registry_name.split("/").pop() || server.title);
    }
  });

  // Reset input values whenever the user picks a different install target,
  // even within the same dialog session. Different targets declare different
  // required inputs; carrying stale values would send the wrong secrets.
  createEffect(() => {
    const current = targetId();
    if (!current) return;
    if (lastTargetId && current !== lastTargetId) {
      setValues({});
    }
    lastTargetId = current;
  });

  const handleSubmit = async () => {
    const server = props.server;
    const selectedTarget = target();
    if (!server || !selectedTarget) return;
    if (!agentName()) {
      showToast("Agent is required.", "warning");
      return;
    }
    for (const input of selectedTarget.required_inputs) {
      if (input.required && !(values()[input.key] || "").trim()) {
        showToast(`${input.label || input.key} is required.`, "warning");
        return;
      }
    }

    setSubmitting(true);
    try {
      const result = await props.onSubmit({
        agent_name: agentName(),
        registry_name: server.registry_name,
        version: server.version || "latest",
        target_id: selectedTarget.id,
        server_name: serverName(),
        input_values: values(),
        auth_method: "secret",
      });
      if (result?.status === "auth_required" && result.redirect_url) {
        window.location.href = result.redirect_url;
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      wide
      title={props.server ? `Install ${props.server.title}` : "Install MCP server"}
      onClose={props.onClose}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", width: "100%" }}>
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button
            class="btn btn-primary"
            type="button"
            onClick={handleSubmit}
            disabled={submitting() || !props.server?.install_targets.length}
          >
            {submitting() ? "Installing..." : "Install"}
          </button>
        </div>
      }
    >
      <Show when={props.server}>
        {(serverGetter) => {
          const server = serverGetter();
          return (
            <div class="stack">
              <div class="stack" style={{ gap: "6px" }}>
                <div class="hint">{server.description}</div>
                <div class="row-wrap">
                  <span class={server.verified ? "badge badge-success" : "badge badge-warning"}>
                    {server.verified ? "verified" : "unverified"}
                  </span>
                  <span class="badge">{server.version || "latest"}</span>
                  <span class="badge">{server.auth_summary}</span>
                </div>
                <Show when={server.repository_url || server.website_url}>
                  <a
                    class="mono"
                    style={{ "font-size": "12px" }}
                    href={server.repository_url || server.website_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {server.repository_url || server.website_url}
                  </a>
                </Show>
              </div>

              <div class="grid-2">
                <div class="field">
                  <label>Agent</label>
                  <select
                    class="input"
                    value={agentName()}
                    onChange={(event) => setAgentName(event.currentTarget.value)}
                  >
                    <For each={props.agentNames}>
                      {(name) => <option value={name}>{name}</option>}
                    </For>
                  </select>
                </div>
                <div class="field">
                  <label>Server name</label>
                  <input
                    class="input mono"
                    value={serverName()}
                    onInput={(event) => setServerName(event.currentTarget.value)}
                  />
                </div>
              </div>

              <div class="field">
                <label>Install target</label>
                <select
                  class="input"
                  value={targetId()}
                  onChange={(event) => setTargetId(event.currentTarget.value)}
                >
                  <For each={server.install_targets}>
                    {(item) => (
                      <option value={item.id}>
                        {item.label} ({item.transport})
                      </option>
                    )}
                  </For>
                </select>
              </div>

              <Show when={target()}>
                {(targetGetter) => {
                  const selectedTarget = targetGetter();
                  return (
                    <div class="stack">
                      <Show when={selectedTarget.command || selectedTarget.url}>
                        <div class="panel" style={{ padding: "12px" }}>
                          <div class="setting-title">Connection</div>
                          <div class="hint mono" style={{ "font-size": "12px", "margin-top": "6px" }}>
                            {selectedTarget.transport === "stdio"
                              ? [selectedTarget.command, ...selectedTarget.args].join(" ")
                              : selectedTarget.url}
                          </div>
                        </div>
                      </Show>

                      <For each={selectedTarget.required_inputs}>
                        {(input) => (
                          <div class="field">
                            <label>
                              {input.label || input.key}
                              <Show when={!input.required}>
                                <span class="hint"> (optional)</span>
                              </Show>
                            </label>
                            <input
                              class="input mono"
                              type={input.secret ? "password" : "text"}
                              placeholder={input.placeholder || input.default}
                              value={values()[input.key] ?? ""}
                              onInput={(event) =>
                                setValues({
                                  ...values(),
                                  [input.key]: event.currentTarget.value,
                                })
                              }
                            />
                            <Show when={input.description}>
                              <div class="hint">{input.description}</div>
                            </Show>
                          </div>
                        )}
                      </For>
                    </div>
                  );
                }}
              </Show>
            </div>
          );
        }}
      </Show>
    </Dialog>
  );
}
