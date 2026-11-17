import "@/styles/agent-tools.css";

import {
  createEffect,
  createMemo,
  createSignal,
  For,
  Index,
  onCleanup,
  onMount,
  Show,
} from "solid-js";
import { Dynamic } from "solid-js/web";
import { A } from "@solidjs/router";
import {
  Bot,
  ChevronRight,
  Clock3,
  FileText,
  Globe,
  Image,
  Info,
  Layers,
  LayoutGrid,
  Plug,
  Search,
  SearchX,
  Settings2,
  ShieldCheck,
  Terminal,
  Users,
  X,
} from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { PermissionModePicker } from "@/components/PermissionModePicker";
import { isAcpCard } from "@/lib/acp";
import type { AgentMcpInstall, AgentsPayload, ToolConfigSchema } from "@/types";
import { AgentToolConfig } from "./AgentToolConfig";
import { acpToolName, isToolSelected } from "./agentForm";
import type {
  AgentCapabilityTab,
  AgentForm,
  AgentToolGroup,
  ToolConfigValue,
} from "./agentForm";

const categoryIcons: Record<string, typeof FileText> = {
  acp: Bot,
  file: FileText,
  shell: Terminal,
  web: Globe,
  browser: Globe,
  image: Image,
  schedule: Clock3,
  agent: Bot,
  skill: Layers,
  utility: Clock3,
};
const toolTitles: Record<string, string> = {
  web_search: "Search the web",
  web_fetch: "Read a web page",
  read: "Read a file",
  list_dir: "List a directory",
  glob: "Find files",
  grep: "Search file contents",
  edit: "Edit a file",
  write: "Write a file",
  bash: "Run a command",
  read_process_output: "Read process output",
  write_process_input: "Send process input",
  stop_process: "Stop a process",
  generate_image: "Generate an image",
  get_current_time: "Get the current time",
};
function formatCategory(category: string) {
  if (category === "acp") return "ACP";
  if (category === "unknown") return "Other";
  if (category === "file") return "Files";
  return category.charAt(0).toUpperCase() + category.slice(1);
}
function toolTitle(name: string) {
  name = acpToolName(name) ?? name;
  const title = name.replaceAll("_", " ");
  return toolTitles[name] || title.charAt(0).toUpperCase() + title.slice(1);
}

export function AgentToolsTab(props: {
  section: AgentCapabilityTab;
  form: AgentForm;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
  readOnly: boolean;
  canManageMcp: boolean;
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
  onUpdateToolConfig: (
    toolName: string,
    fieldName: string,
    value: ToolConfigValue,
  ) => void;
  onResetToolConfigField: (toolName: string, fieldName: string) => void;
}) {
  const section = () => props.section;
  const [category, setCategory] = createSignal("all");
  const [query, setQuery] = createSignal("");
  const [selectedOnly, setSelectedOnly] = createSignal(false);
  const [focusedTool, setFocusedTool] = createSignal<string>();
  const [detailsOpen, setDetailsOpen] = createSignal(false);
  const [contentWidth, setContentWidth] = createSignal(0);
  let explorerRef!: HTMLDivElement;
  let toolDetailsRef: HTMLDivElement | undefined;
  let searchRef: HTMLInputElement | undefined;
  let catalogRef: HTMLDivElement | undefined;
  const inlineDetails = () => contentWidth() >= 900;
  const availableTools = createMemo(() =>
    props.toolGroups.flatMap((group) => group.tools),
  );
  const nativeTool = (tool: string) => props.payload.acp_tools?.find((item) => item.name === acpToolName(tool));
  const isSelected = (tool: string) => isToolSelected(props.form, tool, props.payload);
  const isRequired = (tool: string) => Boolean(nativeTool(tool)?.always_enabled);
  const selectedBuiltIns = createMemo(() =>
    availableTools().filter(isSelected),
  );
  const selectedTool = createMemo(() => {
    const current = focusedTool();
    if (current && availableTools().includes(current)) return current;
    return (
      selectedBuiltIns().find((tool) => props.toolConfigSchemas[tool]) ||
      selectedBuiltIns()[0] ||
      availableTools()[0]
    );
  });
  const categoryFor = (tool: string) =>
    props.toolGroups.find((group) => group.tools.includes(tool))?.category ||
    "unknown";
  const descriptionFor = (tool: string) =>
    nativeTool(tool)?.description ||
    props.payload.tool_descriptions?.[tool]?.trim().split(/\n\s*\n/)[0] ||
    `Use ${toolTitle(tool).toLowerCase()} in this agent’s workflow.`;
  const activeMcpCount = () =>
    props.mcpInstalls.filter((install) => install.agent_enabled).length;
  const installFor = (name: string) =>
    props.mcpInstalls.find((install) => install.server_name === name);
  const activeMcpToolCount = () =>
    props.mcpServerOptions
      .filter((server) => installFor(server)?.agent_enabled)
      .reduce(
        (count, server) =>
          count +
          (props.payload.mcp_tools?.[server] || []).filter((tool) =>
            props.form.tools.includes(tool),
          ).length,
        0,
      );
  const permissionLabel = () => {
    const mode = props.form.permission_mode;
    return mode === "ask"
      ? "Ask for approval"
      : mode === "auto"
        ? "Approve for me"
        : mode === "full_access"
          ? "Full access"
          : "Inherit permissions";
  };
  const visibleGroups = createMemo(() => {
    const needle = query().trim().toLowerCase();
    return props.toolGroups
      .filter((group) => category() === "all" || category() === group.category)
      .map((group) => ({
        ...group,
        visibleTools: group.tools.filter(
          (tool) =>
            (!selectedOnly() || isSelected(tool)) &&
            (!needle ||
              `${tool} ${toolTitle(tool)} ${descriptionFor(tool)}`
                .toLowerCase()
                .includes(needle)),
        ),
      }))
      .filter((group) => group.visibleTools.length > 0);
  });
  const visibleCount = () =>
    visibleGroups().reduce(
      (count, group) => count + group.visibleTools.length,
      0,
    );
  const inspectTool = (tool: string) => {
    const changed = selectedTool() !== tool;
    setFocusedTool(tool);
    if (!inlineDetails()) setDetailsOpen(true);
    else if (changed && toolDetailsRef) toolDetailsRef.scrollTop = 0;
  };
  createEffect(() => {
    category();
    query();
    selectedOnly();
    if (catalogRef) catalogRef.scrollTop = 0;
  });
  createEffect(() => {
    if (inlineDetails()) setDetailsOpen(false);
    if (
      category() !== "all" &&
      !props.toolGroups.some((group) => group.category === category())
    )
      setCategory("all");
  });
  onMount(() => {
    setContentWidth(explorerRef.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) =>
      setContentWidth(entries[0].contentRect.width),
    );
    observer.observe(explorerRef);
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        event.key === "/" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !detailsOpen() &&
        section() === "tools" &&
        !target.closest("input, textarea, select, [contenteditable=true]")
      ) {
        event.preventDefault();
        event.stopPropagation();
        searchRef?.focus();
      }
    };
    document.addEventListener("keydown", handleKey, true);
    onCleanup(() => {
      observer.disconnect();
      document.removeEventListener("keydown", handleKey, true);
    });
  });

  const ToolDetails = () => (
    <Show
      when={selectedTool()}
      keyed
      fallback={
        <div class="agent-tools-empty">
          <Info size={22} />
          <h4>Choose a tool</h4>
          <p>Select a tool to see its details and configuration.</p>
        </div>
      }
    >
      {(tool) => (
        <div class="agent-tool-details">
          <span class="agent-tools-symbol agent-tools-symbol-lg">
            <Dynamic
              component={categoryIcons[categoryFor(tool)] || LayoutGrid}
              size={21}
            />
          </span>
          <h3>{toolTitle(tool)}</h3>
          <div class="agent-tools-tool-name mono">{acpToolName(tool) ?? tool}</div>
          <p class="agent-tool-description">{descriptionFor(tool)}</p>
          <div class="agent-tools-tags">
            <span class="agent-tools-badge">{acpToolName(tool) === undefined ? "Built-in" : "Native"}</span>
            <span class="agent-tools-badge">
              {formatCategory(categoryFor(tool))}
            </span>
          </div>
          <label class="agent-tool-enable">
            <span>
              {isRequired(tool) ? "Always enabled by AGY" : isSelected(tool)
                ? "Enabled for this agent"
                : "Enable for this agent"}
            </span>
            <input
              class="agent-tools-switch"
              type="checkbox"
              role="switch"
              aria-label={`Enable ${tool} for this agent`}
              checked={isSelected(tool)}
              disabled={props.readOnly || isRequired(tool)}
              onChange={(event) =>
                props.onToggleListValue(
                  "tools",
                  tool,
                  event.currentTarget.checked,
                )
              }
            />
          </label>
          <Show
            when={isSelected(tool)}
            fallback={
              <p class="agent-tool-notice">
                Enable this tool to configure it for this agent.
              </p>
            }
          >
            <Show
              when={acpToolName(tool) === undefined}
              fallback={
                <p class="agent-tool-notice">
                  This native tool runs in AGY and follows the agent's permission mode.
                </p>
              }
            >
              <Show
                when={props.toolConfigSchemas[tool]?.fields.length}
                fallback={
                  <p class="agent-tool-notice">
                    This tool uses workspace defaults. No agent-specific
                    configuration is needed.
                  </p>
                }
              >
                <AgentToolConfig
                  tool={tool}
                  form={props.form}
                  readOnly={props.readOnly}
                  schema={props.toolConfigSchemas[tool]}
                  effective={props.toolConfigEffective}
                  payload={props.payload}
                  onUpdate={props.onUpdateToolConfig}
                  onReset={props.onResetToolConfigField}
                />
              </Show>
            </Show>
          </Show>
          <p class="agent-tool-permission-note">
            <ShieldCheck size={14} />
            Selected tools follow the agent’s permission mode.
          </p>
        </div>
      )}
    </Show>
  );
  const AgentChoices = (choiceProps: {
    options: string[];
    field: "sub_agents" | "plan_approval_targets";
    label: string;
  }) => (
    <div class="agent-tools-agent-list">
      <For
        each={choiceProps.options}
        fallback={
          <div class="agent-tools-empty">
            <Users size={22} />
            <p>No {choiceProps.label} available.</p>
          </div>
        }
      >
        {(name) => {
          const card = () =>
            props.payload.cards.find((card) => card.name === name);
          return (
            <label class="agent-tools-agent-option">
              <span class="agent-tools-symbol">
                <Bot size={17} />
              </span>
              <span class="agent-tools-agent-copy">
                <strong>{card()?.display_name || name}</strong>
                <span class="mono">{name}</span>
                <Show when={card()?.description}>
                  <small>{card()?.description}</small>
                </Show>
              </span>
              <input
                type="checkbox"
                aria-label={`Select ${name} as ${choiceProps.field === "sub_agents" ? "sub-agent" : "approval target"}`}
                checked={props.form[choiceProps.field].includes(name)}
                disabled={props.readOnly}
                onChange={(event) =>
                  props.onToggleListValue(
                    choiceProps.field,
                    name,
                    event.currentTarget.checked,
                  )
                }
              />
            </label>
          );
        }}
      </For>
    </div>
  );

  return (
    <div
      ref={explorerRef}
      class="agent-tools"
      classList={{
        "agent-tools-wide": inlineDetails(),
        "agent-tools-compact": contentWidth() < 600,
        "agent-tools-explorer": section() === "tools",
      }}
    >
      <Show when={section() === "tools"}>
        <div class="agent-tools-heading">
          <div>
            <h3>Built-in tools</h3>
            <p>Choose tools for this agent. Select a tool to configure it.</p>
          </div>
          <span class="agent-tools-badge">
            {selectedBuiltIns().length}/{props.totalBuiltInTools} selected
          </span>
        </div>
      </Show>
      <div
        class="agent-tools-layout"
        classList={{ "agent-tools-section-view": section() !== "tools" }}
      >
        <Show when={section() === "tools"}>
          <nav class="agent-tools-navigation" aria-label="Tool categories">
            <div class="agent-tools-eyebrow">Categories</div>
            <div class="agent-tools-category-nav">
              <button
                type="button"
                class="agent-tools-nav-item"
                classList={{ active: category() === "all" }}
                aria-current={category() === "all" ? "true" : undefined}
                onClick={() => setCategory("all")}
              >
                <LayoutGrid size={16} />
                <span>All tools</span>
                <span class="agent-tools-nav-count">
                  {props.totalBuiltInTools}
                </span>
              </button>
              <For each={props.toolGroups}>
                {(group) => (
                  <button
                    type="button"
                    class="agent-tools-nav-item"
                    classList={{ active: category() === group.category }}
                    aria-current={
                      category() === group.category ? "true" : undefined
                    }
                    onClick={() => setCategory(group.category)}
                  >
                    <Dynamic
                      component={categoryIcons[group.category] || LayoutGrid}
                      size={16}
                    />
                    <span>{formatCategory(group.category)}</span>
                    <span class="agent-tools-nav-count">
                      {group.tools.length}
                    </span>
                  </button>
                )}
              </For>
            </div>
          </nav>
        </Show>
        <div class="agent-tools-main">
          <Show when={section() === "tools"}>
            <div class="agent-tools-search">
              <Search size={16} />
              <input
                ref={searchRef}
                class="input"
                type="search"
                placeholder="Search tools…"
                aria-label="Search tools by name or description"
                value={query()}
                onInput={(event) => setQuery(event.currentTarget.value)}
              />
              <kbd class="kbd">/</kbd>
            </div>
            <div class="agent-tools-filters">
              <button
                type="button"
                classList={{ active: !selectedOnly() }}
                aria-pressed={!selectedOnly()}
                onClick={() => setSelectedOnly(false)}
              >
                All tools
              </button>
              <button
                type="button"
                classList={{ active: selectedOnly() }}
                aria-pressed={selectedOnly()}
                onClick={() => setSelectedOnly(true)}
              >
                Selected {selectedBuiltIns().length}
              </button>
              <span role="status">{visibleCount()} shown</span>
            </div>
            <div
              class="agent-tools-catalog"
              ref={catalogRef}
              role="region"
              aria-label="Built-in tool catalog"
              tabIndex={0}
            >
              <For
                each={visibleGroups()}
                fallback={
                  <div class="agent-tools-empty">
                    <SearchX size={24} />
                    <h4>
                      {availableTools().length
                        ? "No tools found"
                        : "No built-in tools available"}
                    </h4>
                    <p>
                      {query()
                        ? "Try another name or a broader search."
                        : selectedOnly()
                          ? "Select tools from the catalog to see them here."
                          : "Choose another category to see more tools."}
                    </p>
                    <Show
                      when={query() || selectedOnly() || category() !== "all"}
                    >
                      <button
                        class="btn btn-sm"
                        type="button"
                        onClick={() => {
                          setQuery("");
                          setSelectedOnly(false);
                          setCategory("all");
                        }}
                      >
                        Clear filters
                      </button>
                    </Show>
                  </div>
                }
              >
                {(group) => {
                  const checkedCount = () =>
                    group.tools.filter(isSelected).length;
                  const selectableTools = () => group.tools.filter((tool) => !isRequired(tool));
                  const allChecked = () =>
                    selectableTools().length > 0 && selectableTools().every(isSelected);
                  return (
                    <section
                      class="agent-tools-group"
                      aria-label={`${formatCategory(group.category)} tools`}
                    >
                      <div class="agent-tools-group-heading">
                        <label title="Select or clear this entire category, including tools hidden by search">
                          <input
                            type="checkbox"
                            checked={allChecked()}
                            ref={(input) =>
                              createEffect(() => {
                                input.indeterminate =
                                  selectableTools().some(isSelected) && !allChecked();
                              })
                            }
                            disabled={props.readOnly || selectableTools().length === 0}
                            aria-label={`Select all ${formatCategory(group.category).toLowerCase()} tools`}
                            onChange={(event) =>
                              props.onToggleToolGroup(
                                group.tools,
                                event.currentTarget.checked,
                              )
                            }
                          />
                          <span>{formatCategory(group.category)}</span>
                        </label>
                        <span>
                          {checkedCount()}/{group.tools.length} selected
                        </span>
                      </div>
                      <div class="agent-tools-rows">
                        <For each={group.visibleTools}>
                          {(tool) => (
                            <div
                              class="agent-tools-row"
                              classList={{ focused: selectedTool() === tool }}
                            >
                              <label class="agent-tools-row-check">
                                <input
                                  type="checkbox"
                                  aria-label={`Enable ${tool}`}
                                  checked={isSelected(tool)}
                                  disabled={props.readOnly || isRequired(tool)}
                                  onChange={(event) =>
                                    props.onToggleListValue(
                                      "tools",
                                      tool,
                                      event.currentTarget.checked,
                                    )
                                  }
                                />
                              </label>
                              <button
                                class="agent-tools-row-main"
                                type="button"
                                aria-label={`Details for ${tool}`}
                                onClick={() => inspectTool(tool)}
                              >
                                <span class="agent-tools-symbol">
                                  <Dynamic
                                    component={
                                      categoryIcons[group.category] ||
                                      LayoutGrid
                                    }
                                    size={16}
                                  />
                                </span>
                                <span class="agent-tools-row-copy">
                                  <strong>{toolTitle(tool)}</strong>
                                  <span class="agent-tools-tool-name mono">
                                    {acpToolName(tool) ?? tool}
                                  </span>
                                  <span class="agent-tools-row-description">
                                    {descriptionFor(tool)}
                                  </span>
                                </span>
                              </button>
                              <button
                                class="btn btn-ghost btn-icon agent-tools-configure"
                                type="button"
                                aria-label={`Configure ${tool}`}
                                title="View details and configuration"
                                onClick={() => inspectTool(tool)}
                              >
                                <Show
                                  when={
                                    props.toolConfigSchemas[tool]?.fields.length
                                  }
                                  fallback={<ChevronRight size={15} />}
                                >
                                  <Settings2 size={15} />
                                </Show>
                              </button>
                            </div>
                          )}
                        </For>
                      </div>
                    </section>
                  );
                }}
              </For>
            </div>
            <p class="agent-tools-catalog-note">
              <Info size={14} />
              Choose a tool to see its details and configuration.
            </p>
          </Show>
          <Show when={section() === "mcp"}>
            <div class="agent-tools-subheading">
              <h3>Connect external tools</h3>
              <p>
                Enable a server, then choose which of its tools this agent can
                use.
              </p>
            </div>
            <Show when={!props.canManageMcp}>
              <p class="agent-tool-notice">
                Save this agent before enabling MCP servers.
              </p>
            </Show>
            <div class="agent-tools-panels" aria-busy={props.mcpUpdating}>
              <For
                each={props.mcpServerOptions}
                fallback={
                  <div class="agent-tools-empty">
                    <Plug size={24} />
                    <h4>No MCP servers available</h4>
                    <p>
                      Add a server in Connections to make its tools available
                      here.
                    </p>
                    <A class="btn btn-sm" href="/settings/connections?tab=mcp">
                      Manage MCP servers
                    </A>
                  </div>
                }
              >
                {(server) => {
                  const install = () => installFor(server);
                  const enabled = () => Boolean(install()?.agent_enabled);
                  return (
                    <section class="agent-tools-server">
                      <div class="agent-tools-server-heading">
                        <span class="agent-tools-symbol">
                          <Plug size={18} />
                        </span>
                        <div>
                          <h4>
                            {install()?.title || server}
                            <span
                              class="agent-tools-badge"
                              classList={{ enabled: enabled() }}
                            >
                              {enabled() ? "Enabled" : "Disabled"}
                            </span>
                          </h4>
                          <p>{install()?.description || server}</p>
                        </div>
                        <input
                          type="checkbox"
                          class="agent-tools-switch"
                          role="switch"
                          aria-label={`Enable ${server} MCP server`}
                          checked={enabled()}
                          disabled={
                            props.readOnly ||
                            props.mcpUpdating ||
                            !props.canManageMcp
                          }
                          onChange={(event) => {
                            props.onToggleMcpInstall(
                              server,
                              event.currentTarget.checked,
                            );
                            event.currentTarget.checked = enabled();
                          }}
                        />
                      </div>
                      <Show when={enabled()}>
                        <div class="agent-tools-server-list">
                          <For
                            each={props.payload.mcp_tools?.[server] || []}
                            fallback={
                              <p class="agent-tool-notice">
                                No tools loaded. Check the server connection and
                                reopen this agent to refresh its tools.
                              </p>
                            }
                          >
                            {(tool) => (
                              <label class="agent-tools-server-tool">
                                <input
                                  type="checkbox"
                                  checked={props.form.tools.includes(tool)}
                                  aria-label={`Enable ${tool}`}
                                  disabled={props.readOnly || props.mcpUpdating}
                                  onChange={(event) =>
                                    props.onToggleListValue(
                                      "tools",
                                      tool,
                                      event.currentTarget.checked,
                                    )
                                  }
                                />
                                <span class="mono">{tool}</span>
                              </label>
                            )}
                          </For>
                        </div>
                      </Show>
                    </section>
                  );
                }}
              </For>
            </div>
            <Show when={props.mcpServerOptions.length}>
              <p class="agent-tools-catalog-note">
                <Info size={14} />
                Server changes apply immediately. Save the agent to apply tool
                selections.
              </p>
            </Show>
          </Show>
          <Show when={section() === "delegation" && !isAcpCard(props.form)}>
            <div class="agent-tools-subheading">
              <h3>Bring in the right agents</h3>
              <p>Choose collaborators and decide who can review a plan.</p>
            </div>
            <div class="agent-tools-panels">
              <section class="agent-config-section">
                <div class="agent-config-section-header">
                  <div>
                    <h3>Sub-agents</h3>
                    <p class="hint">
                      Delegation is enabled automatically when at least one
                      selected sub-agent is available.
                    </p>
                  </div>
                  <span class="agent-tools-badge">
                    {props.form.sub_agents.length}
                  </span>
                </div>
                <div class="agent-config-section-body">
                  <AgentChoices
                    options={props.subAgentOptions}
                    field="sub_agents"
                    label="sub-agents"
                  />
                </div>
              </section>
              <section class="agent-config-section">
                <div class="agent-config-section-header">
                  <div>
                    <h3>Plan approval targets</h3>
                    <p class="hint">
                      Agents that can review a plan before execution.
                    </p>
                  </div>
                  <span class="agent-tools-badge">
                    {props.form.plan_approval_targets.length}
                  </span>
                </div>
                <div class="agent-config-section-body">
                  <AgentChoices
                    options={props.planApprovalTargetOptions}
                    field="plan_approval_targets"
                    label="approval target agents"
                  />
                </div>
              </section>
            </div>
          </Show>
          <Show when={section() === "permissions"}>
            <div class="agent-tools-subheading">
              <h3>Set the boundaries</h3>
              <p>Control how this agent gets approval to use its tools.</p>
            </div>
            <div class="agent-tools-panels">
              <fieldset
                class="agent-tools-permissions"
                disabled={props.readOnly}
              >
                <legend>Permission mode</legend>
                <p class="hint">
                  Choose a mode for this agent, or inherit the global settings.
                </p>
                <PermissionModePicker
                  value={props.form.permission_mode}
                  disabled={props.readOnly}
                  acp={isAcpCard(props.form)}
                  includeInherit
                  onChange={(value) =>
                    props.onUpdate("permission_mode", value ?? null)
                  }
                />
              </fieldset>
              <fieldset
                class="agent-tools-permissions"
                disabled={props.readOnly}
              >
                <legend>Permission exceptions</legend>
                <p class="hint">
                  Deny always wins. Otherwise the last matching rule applies.
                  Full access ignores Ask exceptions. Inherit uses global rules;
                  an empty override uses the mode defaults.
                </p>
                <label class="agent-tools-override">
                  <input
                    type="checkbox"
                    aria-label="Override global permissions"
                    checked={props.form.tool_permissions !== null}
                    onChange={(event) =>
                      props.onUpdate(
                        "tool_permissions",
                        event.currentTarget.checked ? [] : null,
                      )
                    }
                  />
                  Override global permissions
                </label>
                <Index each={props.form.tool_permissions || []}>
                  {(rule, index) => {
                    const update = (
                      key: "action" | "resource" | "effect",
                      value: string,
                    ) =>
                      props.onUpdate(
                        "tool_permissions",
                        (props.form.tool_permissions || []).map((item, at) =>
                          at === index ? { ...item, [key]: value } : item,
                        ),
                      );
                    return (
                      <div class="agent-tools-permission-rule">
                        <label>
                          Action
                          <input
                            class="input"
                            value={rule().action ?? "*"}
                            onInput={(event) =>
                              update("action", event.currentTarget.value)
                            }
                          />
                        </label>
                        <label class="agent-tools-rule-resource">
                          Resource pattern
                          <input
                            class="input"
                            value={rule().resource ?? "*"}
                            onInput={(event) =>
                              update("resource", event.currentTarget.value)
                            }
                          />
                        </label>
                        <label>
                          Decision
                          <select
                            class="input"
                            value={rule().effect}
                            onChange={(event) =>
                              update("effect", event.currentTarget.value)
                            }
                          >
                            <option value="allow">Allow</option>
                            <option value="ask">Ask</option>
                            <option value="deny">Deny</option>
                          </select>
                        </label>
                        <button
                          class="btn btn-sm btn-ghost btn-icon"
                          type="button"
                          aria-label={`Remove permission rule ${index + 1}`}
                          onClick={() =>
                            props.onUpdate(
                              "tool_permissions",
                              (props.form.tool_permissions || []).filter(
                                (_, at) => at !== index,
                              ),
                            )
                          }
                        >
                          <X size={15} />
                        </button>
                      </div>
                    );
                  }}
                </Index>
                <Show when={props.form.tool_permissions !== null}>
                  <Show when={props.form.tool_permissions?.length === 0}>
                    <p class="agent-tool-notice">
                      No exceptions. This override uses the selected mode’s
                      defaults.
                    </p>
                  </Show>
                  <button
                    class="btn btn-sm"
                    type="button"
                    onClick={() =>
                      props.onUpdate("tool_permissions", [
                        ...(props.form.tool_permissions || []),
                        { action: "*", resource: "*", effect: "ask" },
                      ])
                    }
                  >
                    Add permission rule
                  </button>
                </Show>
              </fieldset>
            </div>
          </Show>
        </div>
        <Show when={section() === "tools" && inlineDetails()}>
          <aside
            class="agent-tools-inspector"
            aria-label="Tool details"
          >
            <div class="agent-tools-inspector-heading">
              <span>Tool details</span>
              <Settings2 size={14} />
            </div>
            <div class="agent-tools-inspector-body" ref={toolDetailsRef}>
              <ToolDetails />
            </div>
          </aside>
        </Show>
      </div>
      <footer class="agent-tools-summary">
        <span>
          <LayoutGrid size={14} />
          <strong>
            {selectedBuiltIns().length}/{props.totalBuiltInTools}
          </strong>{" "}
          built-in tools
        </span>
        <span>
          <Plug size={14} />
          <strong>{activeMcpCount()}</strong> MCP servers ·{" "}
          {activeMcpToolCount()} tools
        </span>
        <Show when={!isAcpCard(props.form)}>
          <span>
            <Users size={14} />
            <strong>{props.form.sub_agents.length}</strong> sub-agents
          </span>
          <span>
            <ShieldCheck size={14} />
            <strong>{props.form.plan_approval_targets.length}</strong> plan
            targets
          </span>
        </Show>
        <span class="agent-tools-summary-mode">
          <ShieldCheck size={14} />
          {permissionLabel()}
        </span>
      </footer>
      <Dialog
        open={detailsOpen() && !inlineDetails()}
        title="Tool details & configuration"
        class="agent-tools-details-dialog"
        bodyClass="agent-tools-details-dialog-body"
        size="sm"
        onClose={() => setDetailsOpen(false)}
      >
        <ToolDetails />
      </Dialog>
    </div>
  );
}
