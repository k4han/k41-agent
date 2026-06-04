import { For, Show } from "solid-js";

import { uniqueSorted } from "@/lib/utils";
import type { AgentsPayload } from "@/types";

import type { AgentForm } from "./agentForm";

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

export function AgentToolsTab(props: {
  form: AgentForm;
  readOnly: boolean;
  toolGroups: AgentToolGroup[];
  totalBuiltInTools: number;
  mcpServerOptions: string[];
  subAgentOptions: string[];
  planApprovalTargetOptions: string[];
  onToggleListValue: (
    key: "tools" | "sub_agents" | "mcp_servers" | "plan_approval_targets",
    value: string,
    checked: boolean,
  ) => void;
  onToggleToolGroup: (tools: string[], checked: boolean) => void;
}) {
  return (
    <div class="agent-config-tools">
      <div class="agent-config-summary">
        <div class="agent-config-stat">
          <span>Built-in tools</span>
          <strong>{`${props.form.tools.length}/${props.totalBuiltInTools}`}</strong>
        </div>
        <div class="agent-config-stat">
          <span>MCP servers</span>
          <strong>{`${props.form.mcp_servers.length}/${props.mcpServerOptions.length}`}</strong>
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
              <p class="hint">Connect this agent to configured MCP server toolsets.</p>
            </div>
            <span class="badge badge-info">{`${props.form.mcp_servers.length} selected`}</span>
          </div>
          <div class="agent-config-section-body">
            <div class="agent-config-option-grid">
              <For each={props.mcpServerOptions}>
                {(server) => {
                  const isChecked = () => props.form.mcp_servers.includes(server);
                  return (
                    <label class={optionCardClass(isChecked(), props.readOnly)}>
                      <input
                        type="checkbox"
                        checked={isChecked()}
                        disabled={props.readOnly}
                        onChange={(event) =>
                          props.onToggleListValue("mcp_servers", server, event.currentTarget.checked)
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
