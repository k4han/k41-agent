import { For, Show } from "solid-js";
import { RotateCcw } from "lucide-solid";

import { ModelPicker } from "@/components/ModelPicker";
import { uniqueSorted } from "@/lib/utils";
import type { AgentMcpInstall, AgentsPayload, ToolConfigField, ToolConfigSchema } from "@/types";

import type { AgentForm, ToolConfigValue } from "./agentForm";

export type AgentToolGroup = {
  category: string;
  tools: string[];
};

function formatToolCategory(category: string) {
  return category === "unknown"
    ? "Other"
    : category.charAt(0).toUpperCase() + category.slice(1);
}

function optionCardClass(checked: boolean, readOnly: boolean) {
  return `agent-config-option ${checked ? "active" : ""} ${readOnly ? "read-only" : ""}`;
}

function hasOverride(form: AgentForm, toolName: string, fieldName: string) {
  return Object.prototype.hasOwnProperty.call(form.tool_configs[toolName] || {}, fieldName);
}

function fieldValue(form: AgentForm, toolName: string, field: ToolConfigField) {
  if (hasOverride(form, toolName, field.name)) {
    return form.tool_configs[toolName][field.name];
  }
  return field.input_type === "boolean" ? false : "";
}

function schemaDefaultLabel(field: ToolConfigField) {
  if (field.default === undefined || field.default === null || field.default === "") {
    return "Inherit";
  }
  return `Inherit (${String(field.default)})`;
}

function formatDefault(
  field: ToolConfigField,
  toolName: string,
  effective: Record<string, Record<string, unknown>> | undefined,
) {
  const globalValue = effective?.[toolName]?.[field.name];
  const hasGlobal =
    globalValue !== undefined && globalValue !== null && globalValue !== "";
  if (!hasGlobal) {
    return schemaDefaultLabel(field);
  }
  if (field.secret || field.input_type === "password") {
    return "Global (set)";
  }
  return `Global: ${String(globalValue)}`;
}

function isImageOutputModel(model: { output_types?: string[] | null }) {
  return Boolean(model.output_types?.includes("image"));
}

export function AgentToolsTab(props: {
  form: AgentForm;
  readOnly: boolean;
  toolGroups: AgentToolGroup[];
  totalBuiltInTools: number;
  mcpServerOptions: string[];
  mcpInstalls: AgentMcpInstall[];
  mcpUpdating: boolean;
  subAgentOptions: string[];
  planApprovalTargetOptions: string[];
  toolConfigSchemas: Record<string, ToolConfigSchema>;
  toolConfigEffective?: Record<string, Record<string, unknown>>;
  payload: AgentsPayload;
  onToggleListValue: (
    key: "tools" | "sub_agents" | "plan_approval_targets",
    value: string,
    checked: boolean,
  ) => void;
  onToggleToolGroup: (tools: string[], checked: boolean) => void;
  onToggleMcpInstall: (serverName: string, checked: boolean) => void;
  onUpdateToolConfig: (toolName: string, fieldName: string, value: ToolConfigValue) => void;
  onResetToolConfigField: (toolName: string, fieldName: string) => void;
}) {
  const activeMcpCount = () =>
    props.mcpInstalls.filter((install) => install.agent_enabled).length;
  const installForServer = (serverName: string) =>
    props.mcpInstalls.find((install) => install.server_name === serverName);

  const isFieldVisible = (tool: string, field: ToolConfigField) => {
    if (tool === "generate_image" && field.name === "provider") {
      return false;
    }
    if (!field.show_when || Object.keys(field.show_when).length === 0) {
      return true;
    }
    for (const [depField, allowedValues] of Object.entries(field.show_when)) {
      const overrideVal = props.form.tool_configs[tool]?.[depField];
      const globalVal = props.toolConfigEffective?.[tool]?.[depField];
      const defaultVal = props.toolConfigSchemas[tool]?.fields.find((f) => f.name === depField)?.default;
      const currentVal =
        overrideVal !== undefined && overrideVal !== "" && overrideVal !== null
          ? overrideVal
          : globalVal !== undefined && globalVal !== "" && globalVal !== null
            ? globalVal
            : (defaultVal ?? "");
      if (!allowedValues.includes(String(currentVal))) {
        return false;
      }
    }
    return true;
  };

  const isDuckDuckGo = (tool: string) => {
    if (tool !== "web_search") return false;
    const overrideVal = props.form.tool_configs[tool]?.provider;
    const globalVal = props.toolConfigEffective?.[tool]?.provider;
    const defaultVal = props.toolConfigSchemas[tool]?.fields.find((f) => f.name === "provider")?.default;
    const currentVal =
      overrideVal !== undefined && overrideVal !== "" && overrideVal !== null
        ? overrideVal
        : globalVal !== undefined && globalVal !== "" && globalVal !== null
          ? globalVal
          : (defaultVal ?? "auto");
    return String(currentVal) === "duckduckgo";
  };

  return (
    <div class="agent-config-tools">
      <div class="agent-config-summary">
        <div class="agent-config-stat">
          <span>Built-in tools</span>
          <strong>{`${props.form.tools.length}/${props.totalBuiltInTools}`}</strong>
        </div>
        <div class="agent-config-stat">
          <span>MCP servers</span>
          <strong>{`${activeMcpCount()}/${props.mcpServerOptions.length}`}</strong>
        </div>
        <div class="agent-config-stat">
          <span>Sub-agents</span>
          <strong>{`${props.form.sub_agents.length}/${props.subAgentOptions.length}`}</strong>
        </div>
        <div class="agent-config-stat">
          <span>Plan targets</span>
          <strong>{`${props.form.plan_approval_targets.length}/${props.planApprovalTargetOptions.length}`}</strong>
        </div>
      </div>

      <section class="agent-config-section">
        <div class="agent-config-section-header">
          <div>
            <div class="agent-config-eyebrow">Capabilities</div>
            <h3>Built-in Tools</h3>
            <p class="hint">Enable bundled tools by category for this agent card.</p>
          </div>
          <span class="badge badge-info">{`${props.form.tools.length} selected`}</span>
        </div>
        <div class="agent-config-section-body">
          <For
            each={props.toolGroups}
            fallback={<div class="agent-config-empty">No built-in tools available</div>}
          >
            {(group) => {
              const checkedCount = () =>
                group.tools.filter((tool) => props.form.tools.includes(tool)).length;
              const allChecked = () =>
                group.tools.length > 0 && checkedCount() === group.tools.length;
              const configurableTools = () =>
                group.tools.filter(
                  (tool) => props.form.tools.includes(tool) && props.toolConfigSchemas[tool],
                );
              return (
                <div class="agent-config-group">
                  <div class="agent-config-group-header">
                    <label class="agent-config-group-toggle">
                      <input
                        type="checkbox"
                        checked={allChecked()}
                        disabled={props.readOnly}
                        onChange={(event) =>
                          props.onToggleToolGroup(group.tools, event.currentTarget.checked)
                        }
                      />
                      <span>{formatToolCategory(group.category)}</span>
                    </label>
                    <span class="badge">{`${checkedCount()}/${group.tools.length}`}</span>
                  </div>
                  <div class="agent-config-option-grid">
                    <For each={group.tools}>
                      {(tool) => {
                        const isChecked = () => props.form.tools.includes(tool);
                        return (
                          <label class={optionCardClass(isChecked(), props.readOnly)}>
                            <input
                              type="checkbox"
                              checked={isChecked()}
                              disabled={props.readOnly}
                              onChange={(event) =>
                                props.onToggleListValue("tools", tool, event.currentTarget.checked)
                              }
                            />
                            <span class="agent-config-option-text mono">{tool}</span>
                          </label>
                        );
                      }}
                    </For>
                  </div>
                  <Show when={configurableTools().length > 0}>
                    <div class="agent-config-tool-settings">
                      <For each={configurableTools()}>
                        {(tool) => {
                          const schema = () => props.toolConfigSchemas[tool];
                          return (
                            <div class="agent-config-tool-config">
                              <div class="agent-config-tool-config-header">
                                <div>
                                  <div class="agent-config-tool-config-title mono">{tool}</div>
                                  <div class="hint">Tool-specific overrides</div>
                                </div>
                              </div>
                              <div class="agent-config-tool-config-grid">
                                <For each={schema().fields}>
                                  {(field) => {
                                    const value = () => fieldValue(props.form, tool, field);
                                    return (
                                      <Show when={isFieldVisible(tool, field)}>
                                        <div class="agent-config-tool-field">
                                        <label class="agent-config-tool-field-label" for={`${tool}-${field.name}`}>
                                          <span>{field.label}</span>
                                          <Show when={field.required}>
                                            <span class="badge">Required</span>
                                          </Show>
                                          <Show when={hasOverride(props.form, tool, field.name)}>
                                            <span class="badge badge-info" title="This agent overrides the global value.">Override</span>
                                          </Show>
                                        </label>
                                        <Show
                                          when={tool === "generate_image" && field.name === "model"}
                                          fallback={
                                        <div class="agent-config-tool-field-control">
                                          <Show
                                            when={field.input_type === "boolean"}
                                            fallback={
                                              <Show
                                                when={field.input_type === "select"}
                                                fallback={
                                                  <input
                                                    id={`${tool}-${field.name}`}
                                                    class="input"
                                                    type={
                                                      field.input_type === "number"
                                                        ? "number"
                                                        : field.input_type === "password" || field.secret
                                                          ? "password"
                                                          : "text"
                                                    }
                                                    value={String(value() ?? "")}
                                                    disabled={props.readOnly}
                                                    placeholder={formatDefault(field, tool, props.toolConfigEffective)}
                                                    min={field.min}
                                                    max={field.max}
                                                    step={field.step}
                                                    onInput={(event) => {
                                                      const nextValue = event.currentTarget.value;
                                                      if (nextValue === "") {
                                                        props.onResetToolConfigField(tool, field.name);
                                                        return;
                                                      }
                                                      props.onUpdateToolConfig(tool, field.name, nextValue);
                                                    }}
                                                  />
                                                }
                                              >
                                                <select
                                                  id={`${tool}-${field.name}`}
                                                  class="input"
                                                  value={String(value() ?? "")}
                                                  disabled={props.readOnly}
                                                  onChange={(event) => {
                                                    const nextValue = event.currentTarget.value;
                                                    if (nextValue === "") {
                                                      props.onResetToolConfigField(tool, field.name);
                                                      return;
                                                    }
                                                    props.onUpdateToolConfig(tool, field.name, nextValue);
                                                  }}
                                                >
                                                  <option value="">{formatDefault(field, tool, props.toolConfigEffective)}</option>
                                                  <For each={field.options}>
                                                    {(option) => <option value={option}>{option}</option>}
                                                  </For>
                                                </select>
                                              </Show>
                                            }
                                          >
                                            <label class="agent-config-tool-boolean">
                                              <input
                                                id={`${tool}-${field.name}`}
                                                type="checkbox"
                                                checked={Boolean(value())}
                                                disabled={props.readOnly}
                                                onChange={(event) =>
                                                  props.onUpdateToolConfig(
                                                    tool,
                                                    field.name,
                                                    event.currentTarget.checked,
                                                  )
                                                }
                                              />
                                              <span>Enabled</span>
                                            </label>
                                          </Show>
                                          <button
                                            class="btn btn-sm btn-ghost"
                                            type="button"
                                            title="Reset override"
                                            disabled={props.readOnly || !hasOverride(props.form, tool, field.name)}
                                            onClick={() => props.onResetToolConfigField(tool, field.name)}
                                          >
                                            <RotateCcw size={13} />
                                            Reset
                                          </button>
                                        </div>
                                          }
                                        >
                                          <div class="agent-config-tool-model-control">
                                            <ModelPicker
                                              catalogs={props.payload.model_catalogs}
                                              providerNames={props.payload.provider_names}
                                              defaultProvider={props.payload.default_provider}
                                              defaultModel={props.payload.default_model}
                                              provider={String(props.form.tool_configs[tool]?.provider || "")}
                                              model={String(value() || "")}
                                              disabled={props.readOnly}
                                              resolveDefault
                                              modelFilter={isImageOutputModel}
                                              onChange={(provider, model) => {
                                                props.onUpdateToolConfig(tool, "provider", provider);
                                                props.onUpdateToolConfig(tool, "model", model);
                                              }}
                                            />
                                            <button
                                              class="btn btn-sm btn-ghost"
                                              type="button"
                                              title="Reset model override"
                                              disabled={
                                                props.readOnly ||
                                                (!hasOverride(props.form, tool, "provider") &&
                                                  !hasOverride(props.form, tool, "model"))
                                              }
                                              onClick={() => {
                                                props.onResetToolConfigField(tool, "provider");
                                                props.onResetToolConfigField(tool, "model");
                                              }}
                                            >
                                              <RotateCcw size={13} />
                                              Reset
                                            </button>
                                          </div>
                                        </Show>
                                        <Show when={field.description}>
                                          <div class="hint">{field.description}</div>
                                        </Show>
                                      </div>
                                    </Show>
                                  );
                                }}
                              </For>
                              <Show when={isDuckDuckGo(tool)}>
                                <div class="hint" style={{ "grid-column": "1 / -1", "margin-top": "4px" }}>
                                  DuckDuckGo operates without API credentials.
                                </div>
                              </Show>
                            </div>
                            </div>
                          );
                        }}
                      </For>
                    </div>
                  </Show>
                </div>
              );
            }}
          </For>
        </div>
      </section>

      <Show when={props.mcpServerOptions.length > 0}>
        <section class="agent-config-section">
          <div class="agent-config-section-header">
            <div>
              <div class="agent-config-eyebrow">External context</div>
              <h3>MCP Servers</h3>
              <p class="hint">Connect this agent to installed MCP server toolsets.</p>
            </div>
            <span class="badge badge-info">{`${activeMcpCount()} selected`}</span>
          </div>
          <div class="agent-config-section-body">
            <div class="agent-config-option-grid">
              <For each={props.mcpServerOptions}>
                {(server) => {
                  const install = () => installForServer(server);
                  const isChecked = () => Boolean(install()?.agent_enabled);
                  return (
                    <label class={optionCardClass(isChecked(), props.mcpUpdating)}>
                      <input
                        type="checkbox"
                        checked={isChecked()}
                        disabled={props.mcpUpdating}
                        onChange={(event) =>
                          props.onToggleMcpInstall(server, event.currentTarget.checked)
                        }
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
          <span class="badge badge-info">{`${props.form.sub_agents.length} selected`}</span>
        </div>
        <div class="agent-config-section-body">
          <Show
            when={props.subAgentOptions.length > 0}
            fallback={<div class="agent-config-empty">No sub-agents available</div>}
          >
            <div class="agent-config-option-grid">
              <For each={props.subAgentOptions}>
                {(agent) => {
                  const isChecked = () => props.form.sub_agents.includes(agent);
                  return (
                    <label class={optionCardClass(isChecked(), props.readOnly)}>
                      <input
                        type="checkbox"
                        checked={isChecked()}
                        disabled={props.readOnly}
                        onChange={(event) =>
                          props.onToggleListValue("sub_agents", agent, event.currentTarget.checked)
                        }
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

      <section class="agent-config-section">
        <div class="agent-config-section-header">
          <div>
            <div class="agent-config-eyebrow">Plan review</div>
            <h3>Approval Targets</h3>
            <p class="hint">Limit which agent cards can receive approved plans from this agent.</p>
          </div>
          <span class="badge badge-info">{`${props.form.plan_approval_targets.length} selected`}</span>
        </div>
        <div class="agent-config-section-body">
          <Show
            when={props.planApprovalTargetOptions.length > 0}
            fallback={<div class="agent-config-empty">No approval target agents available</div>}
          >
            <div class="agent-config-option-grid">
              <For each={props.planApprovalTargetOptions}>
                {(agent) => {
                  const isChecked = () => props.form.plan_approval_targets.includes(agent);
                  return (
                    <label class={optionCardClass(isChecked(), props.readOnly)}>
                      <input
                        type="checkbox"
                        checked={isChecked()}
                        disabled={props.readOnly}
                        onChange={(event) =>
                          props.onToggleListValue(
                            "plan_approval_targets",
                            agent,
                            event.currentTarget.checked,
                          )
                        }
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
  );
}

export function buildToolGroups(payload: AgentsPayload, initialTools: string[]): AgentToolGroup[] {
  const groups = (payload.tool_groups || []).map((group) => ({
    category: group.category,
    tools: [...group.tools],
  }));
  const known = new Set(groups.flatMap((group) => group.tools));
  const extras = initialTools.filter((tool) => !known.has(tool) && !tool.startsWith("mcp__"));
  if (extras.length > 0) {
    const other = groups.find((group) => group.category === "unknown");
    if (other) {
      other.tools = uniqueSorted([...other.tools, ...extras]);
    } else {
      groups.push({ category: "unknown", tools: uniqueSorted(extras) });
    }
  }
  return groups;
}
