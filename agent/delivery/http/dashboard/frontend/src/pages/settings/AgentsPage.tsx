import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Copy, Edit3, Eye, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Dialog } from "@/components/Dialog";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { ModelPicker } from "@/components/ModelPicker";
import { PromptVariableTextarea } from "@/components/PromptVariableTextarea";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { truncateText, uniqueSorted } from "@/lib/utils";
import type { AgentCard, AgentsPayload, PromptVariable, PromptVariablesPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type AgentForm = {
  name: string;
  display_name: string;
  description: string;
  graph_type: string;
  provider: string;
  model: string;
  tools: string[];
  mcp_servers: string[];
  sub_agents: string[];
  hidden: boolean;
  max_context_tokens: number;
  system_prompt: string;
};

const blankForm = (workflow: string): AgentForm => ({
  name: "",
  display_name: "",
  description: "",
  graph_type: workflow,
  provider: "default",
  model: "",
  tools: [],
  mcp_servers: [],
  sub_agents: [],
  hidden: false,
  max_context_tokens: 50000,
  system_prompt: "",
});

function cardToForm(card: AgentCard): AgentForm {
  return {
    name: card.name,
    display_name: card.display_name,
    description: card.description,
    graph_type: card.graph_type || "react_agent",
    provider: card.provider || "default",
    model: card.model || "",
    tools: card.tools || [],
    mcp_servers: card.mcp_servers || [],
    sub_agents: card.sub_agents || [],
    hidden: card.hidden || false,
    max_context_tokens: card.max_context_tokens || 50000,
    system_prompt: card.system_prompt || "",
  };
}

export function AgentsPage() {
  const [data, setData] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [modalMode, setModalMode] = createSignal<"create" | "edit" | "view" | null>(null);
  const [currentName, setCurrentName] = createSignal("");
  const [form, setForm] = createSignal<AgentForm>(blankForm("react_agent"));
  const [promptVariables, setPromptVariables] = createSignal<PromptVariable[]>([]);
  const [deleteTargetName, setDeleteTargetName] = createSignal<string | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<AgentsPayload>("/dashboard-api/agents"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    }
  };

  const loadPromptVariables = async () => {
    try {
      const payload = await apiFetch<PromptVariablesPayload>("/dashboard-api/prompt-variables");
      setPromptVariables(payload.variables || []);
    } catch {
      setPromptVariables([]);
    }
  };

  const filteredCards = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return payload.cards;
    }
    return payload.cards.filter((card) =>
      [
        card.name,
        card.display_name,
        card.description,
        card.graph_type,
        card.provider,
        card.model,
        card.source,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  });

  const openCreate = () => {
    const payload = data();
    setForm(blankForm(payload?.workflows.includes("react_agent") ? "react_agent" : payload?.workflows[0] || ""));
    setCurrentName("");
    setModalMode("create");
  };

  const openCard = (card: AgentCard, mode: "edit" | "view") => {
    setForm(cardToForm(card));
    setCurrentName(card.name);
    setModalMode(mode);
  };

  const closeModal = () => setModalMode(null);

  const updateForm = <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const toggleListValue = (key: "tools" | "sub_agents" | "mcp_servers", value: string, checked: boolean) => {
    setForm((current) => {
      const values = new Set(current[key]);
      if (checked) {
        values.add(value);
      } else {
        values.delete(value);
      }
      return { ...current, [key]: Array.from(values).sort() };
    });
  };

  const saveAgent = async () => {
    const payload = form();
    if (!/^[A-Za-z0-9_-]+$/.test(payload.name)) {
      showToast("Agent name is invalid.", "error");
      return;
    }
    if (!payload.system_prompt.trim()) {
      showToast("System prompt is required.", "error");
      return;
    }

    try {
      if (modalMode() === "create") {
        await postJson("/agents/cards", payload);
        showToast("Agent created.");
      } else {
        await putJson(`/agents/cards/${encodeURIComponent(currentName())}`, payload);
        showToast("Agent updated.");
      }
      closeModal();
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save agent", "error");
    }
  };

  const cloneAgent = async (name: string) => {
    try {
      await postJson(`/agents/cards/${encodeURIComponent(name)}/clone`);
      showToast("Agent cloned.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clone agent", "error");
    }
  };

  const requestDeleteAgent = (name: string) => {
    setDeleteTargetName(name);
  };

  const confirmDeleteAgent = async () => {
    const name = deleteTargetName();
    if (!name) {
      return;
    }
    try {
      await deleteJson(`/agents/cards/${encodeURIComponent(name)}`);
      showToast("Agent deleted.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete agent", "error");
    } finally {
      setDeleteTargetName(null);
    }
  };

  const reloadAgents = async () => {
    try {
      const result = await postJson<AgentsPayload & { status: string }>("/agents/reload");
      setData(result);
      showToast("Agents reloaded.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to reload agents", "error");
    }
  };

  const subAgentOptions = createMemo(() =>
    (data()?.agent_names || []).filter((name) => name !== form().name),
  );

  onMount(() => {
    load();
    loadPromptVariables();
  });

  return (
    <SettingsLayout
      title="Agents"
      subtitle="Manage Markdown agent cards loaded by the runtime catalog."
      contentWidth="wide"
      actions={
        <button class="btn" type="button" onClick={reloadAgents}>
          <RefreshCw size={14} />
          Reload
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search agents..."
              onSearchInput={setQuery}
              actions={
                <button class="btn btn-primary" type="button" onClick={openCreate}>
                  <Plus size={14} />
                  New Agent
                </button>
              }
            />

            <section class="panel">
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>Description</th>
                      <th>Provider / Model</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={filteredCards()}
                      fallback={<EmptyTableRow colSpan={5} message="No agent cards found." />}
                    >
                      {(card) => (
                        <tr>
                          <td>
                            <Show
                              when={card.display_name}
                              fallback={<div class="mono">{card.name}</div>}
                            >
                              <div>{card.display_name}</div>
                            </Show>
                          </td>
                          <td>
                            <Show
                              when={card.description}
                              fallback={<span class="hint">-</span>}
                            >
                              {(description) => (
                                <div class="hint">{truncateText(description(), 160)}</div>
                              )}
                            </Show>
                          </td>
                          <td>
                            <div class="chips">
                              <span class="chip">{`${card.provider || "default"}/${card.model || "provider default"}`}</span>
                            </div>
                          </td>
                          <td>
                            <StatusBadge status={card.valid ? "valid" : "invalid"} />
                            <Show when={!card.valid && card.error}>
                              <div class="hint">{card.error}</div>
                            </Show>
                          </td>
                          <td>
                            <div class="row">
                              <Show when={card.valid}>
                                <button class="btn btn-sm" type="button" onClick={() => openCard(card, "view")}>
                                  <Eye size={13} />
                                  View
                                </button>
                                <Show
                                  when={card.editable}
                                  fallback={
                                    <button class="btn btn-sm" type="button" onClick={() => cloneAgent(card.name)}>
                                      <Copy size={13} />
                                      Clone
                                    </button>
                                  }
                                >
                                  <button class="btn btn-sm" type="button" onClick={() => openCard(card, "edit")}>
                                    <Edit3 size={13} />
                                    Edit
                                  </button>
                                  <button
                                    class="btn btn-sm btn-danger"
                                    type="button"
                                    onClick={() => requestDeleteAgent(card.name)}
                                  >
                                    <Trash2 size={13} />
                                    Delete
                                  </button>
                                </Show>
                              </Show>
                              <Show when={!card.valid && card.editable}>
                                <button
                                  class="btn btn-sm btn-danger"
                                  type="button"
                                  onClick={() => requestDeleteAgent(card.name)}
                                >
                                  <Trash2 size={13} />
                                  Delete
                                </button>
                              </Show>
                            </div>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>

            <Dialog
              open={modalMode() !== null}
              title={modalMode() === "create" ? "New Agent" : modalMode() === "edit" ? `Edit ${currentName()}` : `View ${currentName()}`}
              wide
              onClose={closeModal}
              footer={
                <>
                  <button class="btn" type="button" onClick={closeModal}>
                    Close
                  </button>
                  <Show when={modalMode() !== "view"}>
                    <button class="btn btn-primary" type="button" onClick={saveAgent}>
                      Save
                    </button>
                  </Show>
                </>
              }
            >
              <div class="stack">
                <div class="grid-2">
                  <div class="field">
                    <label>Name</label>
                    <input
                      class="input"
                      value={form().name}
                      disabled={modalMode() !== "create"}
                      onInput={(event) => updateForm("name", event.currentTarget.value)}
                    />
                  </div>
                  <div class="field">
                    <label>Display Name</label>
                    <input
                      class="input"
                      value={form().display_name}
                      disabled={modalMode() === "view"}
                      onInput={(event) => updateForm("display_name", event.currentTarget.value)}
                    />
                  </div>
                </div>
                <div class="field">
                  <label>Description</label>
                  <input
                    class="input"
                    value={form().description}
                    disabled={modalMode() === "view"}
                    onInput={(event) => updateForm("description", event.currentTarget.value)}
                  />
                </div>
                <div class="grid-2">
                  <div class="field">
                    <label>Workflow</label>
                    <select
                      class="select"
                      value={form().graph_type}
                      disabled={modalMode() === "view"}
                      onChange={(event) => updateForm("graph_type", event.currentTarget.value)}
                    >
                      <For each={payload.workflows}>
                        {(workflow) => <option value={workflow}>{workflow}</option>}
                      </For>
                    </select>
                  </div>
                  <div class="field">
                    <label>Provider / Model</label>
                    <ModelPicker
                      catalogs={payload.model_catalogs}
                      providerNames={payload.provider_names}
                      defaultProvider={payload.default_provider}
                      defaultModel={payload.default_model}
                      provider={form().provider}
                      model={form().model}
                      disabled={modalMode() === "view"}
                      onChange={(provider, model) => {
                        setForm((current) => ({ ...current, provider, model }));
                      }}
                    />
                  </div>
                </div>
                <div class="field">
                  <label>Max Context Tokens</label>
                  <input
                    class="input"
                    type="number"
                    min="1"
                    value={form().max_context_tokens}
                    disabled={modalMode() === "view"}
                    onInput={(event) => updateForm("max_context_tokens", Number(event.currentTarget.value))}
                  />
                </div>
                <div class="field">
                  <label class="checkbox-row">
                    <input
                      type="checkbox"
                      checked={form().hidden}
                      disabled={modalMode() === "view"}
                      onChange={(event) => updateForm("hidden", event.currentTarget.checked)}
                    />
                    <span>Hidden from chat picker</span>
                  </label>
                  <p class="hint">Hidden agents are not shown in the chat agent dropdown but can still be used internally.</p>
                </div>
                <div class="field">
                  <label>Tools</label>
                  <div class="checkbox-grid">
                    <For each={uniqueSorted([...payload.tools, ...form().tools])}>
                      {(tool) => (
                        <label class="checkbox-row">
                          <input
                            type="checkbox"
                            checked={form().tools.includes(tool)}
                            disabled={modalMode() === "view"}
                            onChange={(event) => toggleListValue("tools", tool, event.currentTarget.checked)}
                          />
                          <span class="mono">{tool}</span>
                        </label>
                      )}
                    </For>
                  </div>
                </div>
                <Show when={payload.mcp_server_options && payload.mcp_server_options.length > 0}>
                  <div class="field">
                    <label>MCP Servers</label>
                    <div class="checkbox-grid">
                      <For each={uniqueSorted([...payload.mcp_server_options!, ...(form().mcp_servers || [])])}>
                        {(server) => (
                          <label class="checkbox-row">
                            <input
                              type="checkbox"
                              checked={(form().mcp_servers || []).includes(server)}
                              disabled={modalMode() === "view"}
                              onChange={(event) => toggleListValue("mcp_servers", server, event.currentTarget.checked)}
                            />
                            <span class="mono">{server}</span>
                          </label>
                        )}
                      </For>
                    </div>
                  </div>
                </Show>
                <div class="field">
                  <label>Sub-agents</label>
                  <div class="checkbox-grid">
                    <For each={uniqueSorted([...subAgentOptions(), ...form().sub_agents])}>
                      {(agent) => (
                        <label class="checkbox-row">
                          <input
                            type="checkbox"
                            checked={form().sub_agents.includes(agent)}
                            disabled={modalMode() === "view"}
                            onChange={(event) => toggleListValue("sub_agents", agent, event.currentTarget.checked)}
                          />
                          <span class="mono">{agent}</span>
                        </label>
                      )}
                    </For>
                  </div>
                </div>
                <div class="field">
                  <label>System Prompt</label>
                  <p class="hint">Use prompt variables with double braces, for example <span class="mono">{"{{common_rules}}"}</span>. Type <span class="mono">{"{{"}</span> to see suggestions.</p>
                  <PromptVariableTextarea
                    rows={12}
                    value={form().system_prompt}
                    disabled={modalMode() === "view"}
                    variables={promptVariables()}
                    onChange={(value) => updateForm("system_prompt", value)}
                  />
                </div>
              </div>
            </Dialog>

            <ConfirmDialog
              open={deleteTargetName() !== null}
              title="Delete Agent"
              message={<p>Are you sure you want to delete agent <span class="mono">{deleteTargetName()}</span>?</p>}
              confirmLabel="Delete"
              confirmVariant="danger"
              onClose={() => setDeleteTargetName(null)}
              onConfirm={() => void confirmDeleteAgent()}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
