import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Copy, Edit3, Eye, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Dialog } from "@/components/Dialog";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { ModelPicker } from "@/components/ModelPicker";
import { PromptVariableTextarea } from "@/components/PromptVariableTextarea";
import { SelectControl } from "@/components/SelectControl";
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
  context_trim_threshold: number;
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
  context_trim_threshold: 50000,
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
    context_trim_threshold: card.context_trim_threshold || 50000,
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
  const [activeTab, setActiveTab] = createSignal<"general" | "tools" | "prompt">("general");
  let textareaRef: HTMLTextAreaElement | undefined;
  const [initialTools, setInitialTools] = createSignal<string[]>([]);

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
    setActiveTab("general");
    setInitialTools([]);
    setModalMode("create");
  };

  const openCard = (card: AgentCard, mode: "edit" | "view") => {
    setForm(cardToForm(card));
    setCurrentName(card.name);
    setActiveTab("general");
    setInitialTools(card.tools || []);
    setModalMode(mode);
  };

  const handleInsertVariable = (varName: string) => {
    const textarea = textareaRef;
    if (!textarea) return;

    const value = form().system_prompt;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const textToInsert = `{{${varName}}}`;

    const nextValue = value.slice(0, start) + textToInsert + value.slice(end);
    updateForm("system_prompt", nextValue);

    const newCursorPos = start + textToInsert.length;

    queueMicrotask(() => {
      textarea.focus();
      textarea.setSelectionRange(newCursorPos, newCursorPos);
    });
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

  const toggleToolGroup = (tools: string[], checked: boolean) => {
    setForm((current) => {
      const values = new Set(current.tools);
      for (const tool of tools) {
        if (checked) {
          values.add(tool);
        } else {
          values.delete(tool);
        }
      }
      return { ...current, tools: Array.from(values).sort() };
    });
  };

  const toolGroups = createMemo(() => {
    const payload = data();
    const groups = (payload?.tool_groups || []).map((group) => ({
      category: group.category,
      tools: [...group.tools],
    }));
    const known = new Set(groups.flatMap((group) => group.tools));
    const extras = initialTools().filter((tool) => !known.has(tool) && !tool.startsWith("mcp__"));
    if (extras.length > 0) {
      const other = groups.find((group) => group.category === "unknown");
      if (other) {
        other.tools = uniqueSorted([...other.tools, ...extras]);
      } else {
        groups.push({ category: "unknown", tools: uniqueSorted(extras) });
      }
    }
    return groups;
  });

  const formatToolCategory = (category: string) =>
    category === "unknown"
      ? "Other"
      : category.charAt(0).toUpperCase() + category.slice(1);

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
  const mcpServerOptions = createMemo(() =>
    uniqueSorted([...(data()?.mcp_server_options || []), ...(form().mcp_servers || [])]),
  );
  const subAgentConfigOptions = createMemo(() =>
    uniqueSorted([...subAgentOptions(), ...form().sub_agents]),
  );
  const totalBuiltInTools = createMemo(() =>
    toolGroups().reduce((total, group) => total + group.tools.length, 0),
  );
  const optionCardClass = (checked: boolean) =>
    `agent-config-option ${checked ? "active" : ""} ${modalMode() === "view" ? "read-only" : ""}`;

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
              <div class="stack agent-card-dialog-content">
                {/* Tab Bar */}
                <div class="tab-bar" style="flex: 0 0 auto;">
                  <button
                    class={`btn btn-sm ${activeTab() === "general" ? "btn-primary" : ""}`}
                    type="button"
                    onClick={() => setActiveTab("general")}
                  >
                    General Config
                  </button>
                  <button
                    class={`btn btn-sm ${activeTab() === "tools" ? "btn-primary" : ""}`}
                    type="button"
                    onClick={() => setActiveTab("tools")}
                  >
                    Capabilities & Tools
                  </button>
                  <button
                    class={`btn btn-sm ${activeTab() === "prompt" ? "btn-primary" : ""}`}
                    type="button"
                    onClick={() => setActiveTab("prompt")}
                  >
                    System Prompt
                  </button>
                </div>

                {/* Tab Contents Container */}
                <div style="flex: 1 1 auto; overflow-y: auto; min-height: 0; padding-right: 4px; display: flex; flex-direction: column;">
                  {/* Tab Contents */}
                  <Show when={activeTab() === "general"}>
                    <div class="stack" style="gap: 16px; padding: 4px 2px;">
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
                          <SelectControl
                            value={form().graph_type}
                            options={payload.workflows.map((workflow) => ({ value: workflow, label: workflow }))}
                            disabled={modalMode() === "view"}
                            onChange={(value) => updateForm("graph_type", value)}
                            ariaLabel="Workflow"
                          />
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
                        <label>Context Trim Threshold</label>
                        <input
                          class="input"
                          type="number"
                          min="1"
                          value={form().context_trim_threshold}
                          disabled={modalMode() === "view"}
                          onInput={(event) => updateForm("context_trim_threshold", Number(event.currentTarget.value))}
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
                    </div>
                  </Show>

                  <Show when={activeTab() === "tools"}>
                    <div class="agent-config-tools">
                      <div class="agent-config-summary">
                        <div class="agent-config-stat">
                          <span>Built-in tools</span>
                          <strong>{form().tools.length}/{totalBuiltInTools()}</strong>
                        </div>
                        <div class="agent-config-stat">
                          <span>MCP servers</span>
                          <strong>{form().mcp_servers.length}/{mcpServerOptions().length}</strong>
                        </div>
                        <div class="agent-config-stat">
                          <span>Sub-agents</span>
                          <strong>{form().sub_agents.length}/{subAgentConfigOptions().length}</strong>
                        </div>
                      </div>

                      <section class="agent-config-section">
                        <div class="agent-config-section-header">
                          <div>
                            <div class="agent-config-eyebrow">Capabilities</div>
                            <h3>Built-in Tools</h3>
                            <p class="hint">Enable bundled tools by category for this agent card.</p>
                          </div>
                          <span class="badge badge-info">{form().tools.length} selected</span>
                        </div>
                        <div class="agent-config-section-body">
                          <For
                            each={toolGroups()}
                            fallback={<div class="agent-config-empty">No built-in tools available</div>}
                          >
                            {(group) => {
                              const checkedCount = () => group.tools.filter((tool) => form().tools.includes(tool)).length;
                              const allChecked = () =>
                                group.tools.length > 0 && checkedCount() === group.tools.length;
                              return (
                                <div class="agent-config-group">
                                  <div class="agent-config-group-header">
                                    <label class="agent-config-group-toggle">
                                      <input
                                        type="checkbox"
                                        checked={allChecked()}
                                        disabled={modalMode() === "view"}
                                        onChange={(event) => toggleToolGroup(group.tools, event.currentTarget.checked)}
                                      />
                                      <span>{formatToolCategory(group.category)}</span>
                                    </label>
                                    <span class="badge">{checkedCount()}/{group.tools.length}</span>
                                  </div>
                                  <div class="agent-config-option-grid">
                                    <For each={group.tools}>
                                      {(tool) => {
                                        const isChecked = () => form().tools.includes(tool);
                                        return (
                                          <label class={optionCardClass(isChecked())}>
                                            <input
                                              type="checkbox"
                                              checked={isChecked()}
                                              disabled={modalMode() === "view"}
                                              onChange={(event) => toggleListValue("tools", tool, event.currentTarget.checked)}
                                            />
                                            <span class="agent-config-option-text mono">{tool}</span>
                                          </label>
                                        );
                                      }}
                                    </For>
                                  </div>
                                </div>
                              );
                            }}
                          </For>
                        </div>
                      </section>

                      <Show when={mcpServerOptions().length > 0}>
                        <section class="agent-config-section">
                          <div class="agent-config-section-header">
                            <div>
                              <div class="agent-config-eyebrow">External context</div>
                              <h3>MCP Servers</h3>
                              <p class="hint">Connect this agent to configured MCP server toolsets.</p>
                            </div>
                            <span class="badge badge-info">{form().mcp_servers.length} selected</span>
                          </div>
                          <div class="agent-config-section-body">
                            <div class="agent-config-option-grid">
                              <For each={mcpServerOptions()}>
                                {(server) => {
                                  const isChecked = () => form().mcp_servers.includes(server);
                                  return (
                                    <label class={optionCardClass(isChecked())}>
                                      <input
                                        type="checkbox"
                                        checked={isChecked()}
                                        disabled={modalMode() === "view"}
                                        onChange={(event) => toggleListValue("mcp_servers", server, event.currentTarget.checked)}
                                      />
                                      <span class="agent-config-option-text mono">{server}</span>
                                    </label>
                                  );
                                }}
                              </For>
                            </div>
                          </div>
                        </section>
                      </Show>

                      <section class="agent-config-section">
                        <div class="agent-config-section-header">
                          <div>
                            <div class="agent-config-eyebrow">Delegation</div>
                            <h3>Sub-agents</h3>
                            <p class="hint">Allow this agent to route work to other agent cards.</p>
                          </div>
                          <span class="badge badge-info">{form().sub_agents.length} selected</span>
                        </div>
                        <div class="agent-config-section-body">
                          <Show
                            when={subAgentConfigOptions().length > 0}
                            fallback={<div class="agent-config-empty">No sub-agents available</div>}
                          >
                            <div class="agent-config-option-grid">
                              <For each={subAgentConfigOptions()}>
                                {(agent) => {
                                  const isChecked = () => form().sub_agents.includes(agent);
                                  return (
                                    <label class={optionCardClass(isChecked())}>
                                      <input
                                        type="checkbox"
                                        checked={isChecked()}
                                        disabled={modalMode() === "view"}
                                        onChange={(event) => toggleListValue("sub_agents", agent, event.currentTarget.checked)}
                                      />
                                      <span class="agent-config-option-text mono">{agent}</span>
                                    </label>
                                  );
                                }}
                              </For>
                            </div>
                          </Show>
                        </div>
                      </section>
                    </div>
                  </Show>

                  <Show when={activeTab() === "prompt"}>
                    <div style="display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 20px; height: 100%; padding: 4px 2px; min-height: 0; flex: 1 1 auto;">
                      <div class="field" style="display: flex; flex-direction: column; height: 100%; flex: 1 1 auto; min-height: 0;">
                        <label style="flex: 0 0 auto;">System Prompt</label>
                        <p class="hint" style="margin-bottom: 4px; flex: 0 0 auto;">Use prompt variables with double braces, for example <span class="mono">{"{{common_rules}}"}</span>. Type <span class="mono">{"{{"}</span> to see suggestions.</p>
                        <PromptVariableTextarea
                          ref={textareaRef}
                          containerClass="full-height"
                          value={form().system_prompt}
                          disabled={modalMode() === "view"}
                          variables={promptVariables()}
                          onChange={(value) => updateForm("system_prompt", value)}
                        />
                      </div>
                      <div style="display: flex; flex-direction: column; gap: 8px; border-left: 1px solid var(--border, rgba(255, 255, 255, 0.08)); padding-left: 20px; height: 100%; max-height: 480px;">
                        <label style="color: var(--muted); font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: 0.05em; flex: 0 0 auto;">Prompt Variables</label>
                        <p class="hint" style="margin-bottom: 4px; font-size: 11px; flex: 0 0 auto;">Click a variable to insert it at the cursor position.</p>
                        <div style="display: flex; flex-direction: column; gap: 6px; flex: 1; overflow-y: auto; padding-right: 4px;">
                          <For each={promptVariables()}>
                            {(variable) => (
                              <button
                                type="button"
                                class="btn btn-sm"
                                style={{
                                  "justify-content": "space-between",
                                  "text-align": "left",
                                  "font-family": "monospace",
                                  "font-size": "11px",
                                  "width": "100%",
                                  "overflow": "hidden",
                                  "text-overflow": "ellipsis",
                                  "white-space": "nowrap",
                                  "border": "1px solid var(--border, rgba(255, 255, 255, 0.08))",
                                  "border-radius": "6px",
                                  "padding": "6px 10px",
                                  "background": variable.is_system ? "color-mix(in srgb, var(--accent, #3b82f6) 5%, var(--surface-2, rgba(255, 255, 255, 0.02)))" : "var(--surface-2, rgba(255, 255, 255, 0.02))",
                                  "cursor": "pointer",
                                  "flex": "0 0 auto",
                                  "display": "flex",
                                  "align-items": "center",
                                  "gap": "6px"
                                }}
                                title={variable.value ? `${variable.name}: ${variable.value}` : variable.name}
                                disabled={modalMode() === "view"}
                                onClick={() => handleInsertVariable(variable.name)}
                              >
                                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{`{{${variable.name}}}`}</span>
                                <Show when={variable.is_system}>
                                  <span style="font-size: 9px; opacity: 0.6; text-transform: uppercase; font-weight: bold; background: rgba(255, 255, 255, 0.08); padding: 1px 4px; border-radius: 3px; border: 1px solid rgba(255, 255, 255, 0.1); flex-shrink: 0;">sys</span>
                                </Show>
                              </button>
                            )}
                          </For>
                          <Show when={promptVariables().length === 0}>
                            <span class="hint" style="text-align: center; padding: 12px 0;">No variables available</span>
                          </Show>
                        </div>
                      </div>
                    </div>
                  </Show>
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
