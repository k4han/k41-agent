import { createSignal, For, Show } from "solid-js";
import { Plus, RefreshCw, Search, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { API_PATHS } from "@/lib/endpoints";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import type {
  AgentsPayload,
  McpInstallResponse,
  McpSearchPayload,
  McpSearchResult,
  McpServerInput,
  McpServerStatus,
  McpServersPayload,
} from "@/types";

import { McpInstallDialog } from "./McpInstallDialog";
import { McpServerDialog } from "./McpServerDialog";
import { getServerIcon } from "./mcpIcons";

export function McpTab() {
  const [servers, setServers] = createSignal<McpServerStatus[]>([]);
  const [agentNames, setAgentNames] = createSignal<string[]>([]);
  const [results, setResults] = createSignal<McpSearchResult[]>([]);
  const [query, setQuery] = createSignal("");
  const [nextCursor, setNextCursor] = createSignal("");
  const [loadError, setLoadError] = createSignal("");
  const [searching, setSearching] = createSignal(false);
  const [showCreate, setShowCreate] = createSignal(false);
  const [selectedServer, setSelectedServer] = createSignal<McpServerStatus | null>(null);
  const [installTarget, setInstallTarget] = createSignal<McpSearchResult | null>(null);
  const [deleteTargetName, setDeleteTargetName] = createSignal<string | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setLoadError("");
    try {
      const [serversPayload, agentsPayload] = await Promise.all([
        apiFetch<McpServersPayload>(API_PATHS.mcpServers),
        apiFetch<AgentsPayload>(API_PATHS.agents),
      ]);
      setServers(serversPayload.servers);
      setAgentNames(agentsPayload.agent_names || []);
      if (results().length === 0 && !nextCursor()) {
        await searchMarketplace();
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load MCP servers");
    }
  };

  const searchMarketplace = async (cursor = "") => {
    setSearching(true);
    try {
      const params = new URLSearchParams();
      if (query().trim()) params.set("q", query().trim());
      if (cursor) params.set("cursor", cursor);
      params.set("limit", "12");
      const payload = await apiFetch<McpSearchPayload>(
        `${API_PATHS.mcpSearch}?${params.toString()}`,
      );
      setResults(cursor ? [...results(), ...payload.servers] : payload.servers);
      setNextCursor(payload.next_cursor || "");
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to search MCP registry.",
        "error",
      );
    } finally {
      setSearching(false);
    }
  };

  const installServer = async (payload: {
    agent_name: string;
    registry_name: string;
    version: string;
    target_id: string;
    server_name: string;
    input_values: Record<string, string>;
    auth_method: string;
  }) => {
    try {
      const result = await postJson<McpInstallResponse>(API_PATHS.mcpInstall, payload);
      if (result.status === "installed") {
        showToast(`Installed MCP server "${result.server_name || payload.server_name}".`);
        setInstallTarget(null);
        await load();
      }
      return result;
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to install MCP server.",
        "error",
      );
      throw err;
    }
  };

  const createServer = async (payload: McpServerInput) => {
    try {
      await postJson(API_PATHS.mcpServers, payload);
      showToast(`Created MCP server "${payload.name}".`);
      setShowCreate(false);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to create MCP server.",
        "error",
      );
    }
  };

  const updateServer = async (payload: McpServerInput) => {
    try {
      await putJson(API_PATHS.mcpServer(payload.name), payload);
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

  const confirmDeleteServer = async () => {
    const name = deleteTargetName();
    if (!name) return;
    try {
      await deleteJson(API_PATHS.mcpServer(name));
      showToast(`Deleted MCP server "${name}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to delete MCP server.",
        "error",
      );
    } finally {
      setDeleteTargetName(null);
    }
  };

  const reloadServer = async (name: string) => {
    try {
      await postJson(`${API_PATHS.mcpServer(name)}/reload`);
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
      await putJson(`${API_PATHS.mcpServer(name)}/toggle`, { enabled });
      showToast(`${enabled ? "Enabled" : "Disabled"} MCP server "${name}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to toggle MCP server.",
        "error",
      );
    }
  };

  useCatalogAndLoad(load);

  return (
    <div class="stack">
      <div class="row-wrap-end">
        <button class="btn btn-primary" type="button" onClick={() => setShowCreate(true)}>
          <Plus size={14} />
          Add custom server
        </button>
      </div>

      <DataGate data={loadError() ? undefined : servers()} error={loadError()} onRetry={load}>
        {() => (
          <>
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Your MCP servers</div>
                  <div class="hint">
                    Installed servers are enabled per agent from the agent tools page.
                  </div>
                </div>
              </div>
              <DashboardTable
                columns={[
                  { header: "Name" },
                  { header: "Transport" },
                  { header: "Status" },
                  { header: "Tools" },
                  {},
                ]}
                rows={servers()}
                emptyMessage="No MCP servers configured yet. Install one from the marketplace below or add a custom server."
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
                      <div class="mcp-status-cell">
                        <button
                          type="button"
                          class={`toggle-control ${server.enabled ? "active" : ""}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            void toggleServer(server.name, !server.enabled);
                          }}
                          title={server.enabled ? "Disable server" : "Enable server"}
                        >
                          <div class="toggle-track">
                            <div class="toggle-thumb" />
                          </div>
                        </button>
                        <Show when={server.enabled} fallback={<span class="badge badge-warning">disabled</span>}>
                          <Show when={!server.error} fallback={<span class="badge badge-danger" title={server.error}>error</span>}>
                            <span class={server.loaded ? "badge badge-success" : "badge"}>
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
                          onClick={(event) => {
                            event.stopPropagation();
                            void reloadServer(server.name);
                          }}
                          title="Reload tools"
                        >
                          <RefreshCw size={13} />
                        </button>
                        <button
                          class="btn btn-sm"
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setDeleteTargetName(server.name);
                          }}
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </DashboardTable>
            </section>

            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">MCP Marketplace</div>
                  <div class="hint">Search the official MCP registry and install servers per agent.</div>
                </div>
              </div>
              <div class="panel-body stack">
                <div class="row-wrap">
                  <input
                    class="input"
                    placeholder="Search MCP servers"
                    value={query()}
                    onInput={(event) => setQuery(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void searchMarketplace();
                    }}
                  />
                  <button class="btn" type="button" onClick={() => void searchMarketplace()} disabled={searching()}>
                    <Search size={14} />
                    {searching() ? "Searching..." : "Search"}
                  </button>
                </div>
                <div class="grid-3">
                  <For each={results()} fallback={<div class="empty">No marketplace results.</div>}>
                    {(server) => (
                      <div class="panel mcp-popular-card">
                        <div class="setting-title mcp-popular-card-title">
                          {getServerIcon(server.title)}
                          {server.title}
                        </div>
                        <div class="hint mono" style={{ "font-size": "11px" }}>
                          {server.registry_name}
                        </div>
                        <div class="hint mcp-popular-card-description">
                          {server.description}
                        </div>
                        <div class="row-wrap">
                          <span class={server.verified ? "badge badge-success" : "badge badge-warning"}>
                            {server.verified ? "verified" : "unverified"}
                          </span>
                          <span class="badge">{server.version || "latest"}</span>
                        </div>
                        <div class="mcp-popular-card-footer">
                          <span class="hint">{server.auth_summary}</span>
                          <button class="btn btn-sm" type="button" onClick={() => setInstallTarget(server)}>
                            Install
                          </button>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
                <Show when={nextCursor()}>
                  <div class="row-wrap-end">
                    <button class="btn" type="button" onClick={() => void searchMarketplace(nextCursor())} disabled={searching()}>
                      Load more
                    </button>
                  </div>
                </Show>
              </div>
            </section>
          </>
        )}
      </DataGate>

      <Show when={showCreate()}>
        <McpServerDialog open={true} mode="create" onClose={() => setShowCreate(false)} onSubmit={createServer} />
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
        agentNames={agentNames()}
        onClose={() => setInstallTarget(null)}
        onSubmit={installServer}
      />

      <ConfirmDialog
        open={deleteTargetName() !== null}
        title="Delete MCP Server"
        message={<p>Are you sure you want to delete MCP server <span class="mono">{deleteTargetName()}</span>?</p>}
        confirmLabel="Delete"
        confirmVariant="danger"
        onClose={() => setDeleteTargetName(null)}
        onConfirm={() => void confirmDeleteServer()}
      />
    </div>
  );
}
