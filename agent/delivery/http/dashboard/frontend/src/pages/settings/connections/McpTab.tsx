import { createSignal, For, onMount, Show } from "solid-js";
import { Plus, RefreshCw, Trash2 } from "lucide-solid";

import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import type {
  McpPopularPayload,
  McpPopularServer,
  McpServerInput,
  McpServerStatus,
  McpServersPayload,
} from "@/types";

import { McpInstallDialog } from "./McpInstallDialog";
import { McpServerDialog } from "./McpServerDialog";
import { getServerIcon } from "./mcpIcons";

export function McpTab() {
  const [popular, setPopular] = createSignal<McpPopularServer[]>([]);
  const [servers, setServers] = createSignal<McpServerStatus[]>([]);
  const [loadError, setLoadError] = createSignal("");
  const [showCreate, setShowCreate] = createSignal(false);
  const [selectedServer, setSelectedServer] = createSignal<McpServerStatus | null>(null);
  const [installTarget, setInstallTarget] = createSignal<McpPopularServer | null>(
    null,
  );
  const { showToast } = useToast();

  const updateServer = async (payload: McpServerInput) => {
    try {
      await putJson(
        `/dashboard-api/mcp/servers/${encodeURIComponent(payload.name)}`,
        payload,
      );
      showToast(`Updated MCP server "${payload.name}".`);
      setSelectedServer(null);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to update MCP server.",
        "error",
      );
    }
  };

  const load = async () => {
    setLoadError("");
    try {
      const [popularPayload, serversPayload] = await Promise.all([
        apiFetch<McpPopularPayload>("/dashboard-api/mcp/popular"),
        apiFetch<McpServersPayload>("/dashboard-api/mcp/servers"),
      ]);
      setPopular(popularPayload.servers);
      setServers(serversPayload.servers);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load MCP servers");
    }
  };

  const createServer = async (payload: McpServerInput) => {
    try {
      await postJson("/dashboard-api/mcp/servers", payload);
      showToast(`Created MCP server "${payload.name}".`);
      setShowCreate(false);
      setInstallTarget(null);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to create MCP server.",
        "error",
      );
    }
  };

  const deleteServer = async (name: string) => {
    if (!window.confirm(`Delete MCP server "${name}"?`)) {
      return;
    }
    try {
      await deleteJson(`/dashboard-api/mcp/servers/${encodeURIComponent(name)}`);
      showToast(`Deleted MCP server "${name}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to delete MCP server.",
        "error",
      );
    }
  };

  const reloadServer = async (name: string) => {
    try {
      await postJson(
        `/dashboard-api/mcp/servers/${encodeURIComponent(name)}/reload`,
      );
      showToast(`Reloaded "${name}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to reload MCP server.",
        "error",
      );
    }
  };

  const toggleServer = async (name: string, enabled: boolean) => {
    try {
      await putJson(
        `/dashboard-api/mcp/servers/${encodeURIComponent(name)}/toggle`,
        { enabled },
      );
      showToast(`${enabled ? "Enabled" : "Disabled"} MCP server "${name}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to toggle MCP server.",
        "error",
      );
    }
  };

  onMount(load);

  return (
    <div class="stack">
      <div class="row-wrap" style={{ "justify-content": "flex-end" }}>
        <button
          class="btn btn-primary"
          type="button"
          onClick={() => setShowCreate(true)}
        >
          <Plus size={14} />
          Add custom server
        </button>
      </div>
      <DataGate
        data={loadError() ? undefined : popular()}
        error={loadError()}
        onRetry={load}
      >
        {() => (
          <>
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Your MCP servers</div>
                  <div class="hint">
                    Tools from these servers are exposed to agents with the
                    <code> mcp__&lt;server&gt;__&lt;tool&gt; </code>prefix.
                  </div>
                </div>
              </div>
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Transport</th>
                      <th>Status</th>
                      <th>Tools</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={servers()}
                      fallback={
                        <tr>
                          <td colSpan={5}>
                            <div class="empty">
                              No MCP servers configured yet. Install one from the
                              catalog below or add a custom server.
                            </div>
                          </td>
                        </tr>
                      }
                    >
                      {(server) => (
                        <tr
                          class="provider-table-row"
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelectedServer(server)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedServer(server);
                            }
                          }}
                        >
                          <td>
                            <div class="provider-name-cell">
                              {getServerIcon(server.name)}
                              <span class="setting-title">{server.name}</span>
                            </div>
                          </td>
                          <td>{server.transport}</td>
                          <td>
                            <div style={{ display: "flex", "align-items": "center", gap: "10px" }}>
                              <button
                                type="button"
                                class={`toggle-control ${server.enabled ? "active" : ""}`}
                                onClick={(e) => { e.stopPropagation(); toggleServer(server.name, !server.enabled); }}
                                title={server.enabled ? "Disable server" : "Enable server"}
                              >
                                <div class="toggle-track">
                                  <div class="toggle-thumb" />
                                </div>
                              </button>
                              <Show
                                when={server.enabled}
                                fallback={
                                  <span class="badge badge-warning">disabled</span>
                                }
                              >
                                <Show
                                  when={!server.error}
                                  fallback={
                                    <span
                                      class="badge badge-danger"
                                      title={server.error}
                                    >
                                      error
                                    </span>
                                  }
                                >
                                  <span
                                    class={
                                      server.loaded
                                        ? "badge badge-success"
                                        : "badge"
                                    }
                                  >
                                    {server.loaded ? "loaded" : "pending"}
                                  </span>
                                </Show>
                              </Show>
                            </div>
                          </td>
                          <td>{server.tool_count}</td>
                          <td>
                            <div class="row-wrap">
                              <button
                                class="btn btn-sm"
                                type="button"
                                onClick={(e) => { e.stopPropagation(); reloadServer(server.name); }}
                                title="Reload tools"
                              >
                                <RefreshCw size={13} />
                              </button>
                              <button
                                class="btn btn-sm"
                                type="button"
                                onClick={(e) => { e.stopPropagation(); deleteServer(server.name); }}
                                title="Delete"
                              >
                                <Trash2 size={13} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>

            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Popular MCP servers</div>
                  <div class="hint">Install with one click. You may still need to provide credentials.</div>
                </div>
              </div>
              <div class="panel-body">
                <div class="grid-3">
                  <For each={popular()}>
                    {(server) => (
                      <div class="panel" style={{ padding: "12px" }}>
                        <div class="setting-title" style={{ display: "flex", "align-items": "center", gap: "6px" }}>
                          {getServerIcon(server.name)}
                          {server.name}
                        </div>
                        <div class="hint" style={{ "margin-bottom": "8px" }}>
                          {server.description}
                        </div>
                        <div class="row-wrap" style={{ "justify-content": "space-between" }}>
                          <span class="mono" style={{ "font-size": "11px", display: "flex", "align-items": "center", gap: "6px" }}>
                            {server.transport}
                          </span>
                          <button
                            class="btn btn-sm"
                            type="button"
                            onClick={() => setInstallTarget(server)}
                          >
                            Install
                          </button>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </div>
            </section>
          </>
        )}
      </DataGate>

      <Show when={showCreate()}>
        <McpServerDialog
          open={true}
          mode="create"
          onClose={() => setShowCreate(false)}
          onSubmit={createServer}
        />
      </Show>

      <Show when={selectedServer() !== null}>
        <McpServerDialog
          open={true}
          mode="edit"
          initial={selectedServer()!}
          onClose={() => setSelectedServer(null)}
          onSubmit={updateServer}
        />
      </Show>

      <McpInstallDialog
        open={installTarget() !== null}
        server={installTarget()}
        onClose={() => setInstallTarget(null)}
        onSubmit={createServer}
      />
    </div>
  );
}
