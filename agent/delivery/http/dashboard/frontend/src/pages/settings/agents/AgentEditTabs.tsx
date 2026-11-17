import { createEffect, Show } from "solid-js";

import type { AgentMcpInstall, AgentsPayload, PromptVariable } from "@/types";

import {
  isToolSelected,
  type AgentForm,
  type AgentTab,
  type AgentToolGroup,
  type ToolConfigValue,
} from "./agentForm";
import { isAcpCard } from "@/lib/acp";
import { AgentEditNavigation } from "./AgentEditNavigation";
import { AgentGeneralTab } from "./AgentGeneralTab";
import { AgentPromptTab } from "./AgentPromptTab";
import { AgentToolsTab } from "./AgentToolsTab";

export function AgentEditTabs(props: {
  form: AgentForm;
  readOnly: boolean;
  canManageMcp: boolean;
  payload: AgentsPayload;
  promptVariables: PromptVariable[];
  activeTab: AgentTab;
  onTabChange: (tab: AgentTab) => void;
  toolGroups: AgentToolGroup[];
  totalBuiltInTools: number;
  mcpServerOptions: string[];
  mcpInstalls: AgentMcpInstall[];
  mcpUpdating: boolean;
  subAgentOptions: string[];
  planApprovalTargetOptions: string[];
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
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
  onInsertVariable: (name: string) => void;
}) {
  createEffect(() => {
    if (isAcpCard(props.form) && props.activeTab === "delegation")
      props.onTabChange("tools");
  });
  return (
    <div class="agent-editor">
      <AgentEditNavigation
        activeTab={props.activeTab}
        acp={isAcpCard(props.form)}
        onChange={props.onTabChange}
        counts={{
          tools: props.toolGroups.reduce(
            (count, group) =>
              count +
              group.tools.filter((tool) => isToolSelected(props.form, tool, props.payload))
                .length,
            0,
          ),
          mcp: props.mcpInstalls.filter((install) => install.agent_enabled)
            .length,
          delegation: props.form.sub_agents.length,
        }}
      />
      <section
        class="agent-editor-panel"
        classList={{ "agent-editor-general": props.activeTab === "general" }}
        role="tabpanel"
        id={`agent-editor-panel-${props.activeTab}`}
        aria-labelledby={`agent-editor-tab-${props.activeTab}`}
      >
        <Show when={props.activeTab === "general"}>
          <AgentGeneralTab
            form={props.form}
            readOnly={props.readOnly}
            workflows={props.payload.workflows}
            payload={props.payload}
            onUpdate={props.onUpdate}
          />
        </Show>
        <Show
          when={props.activeTab !== "general" && props.activeTab !== "prompt"}
        >
          <AgentToolsTab
            section={
              props.activeTab === "general" || props.activeTab === "prompt"
                ? "tools"
                : props.activeTab
            }
            form={props.form}
            onUpdate={props.onUpdate}
            readOnly={props.readOnly}
            canManageMcp={props.canManageMcp}
            toolGroups={props.toolGroups}
            totalBuiltInTools={props.totalBuiltInTools}
            mcpServerOptions={props.mcpServerOptions}
            mcpInstalls={props.mcpInstalls}
            mcpUpdating={props.mcpUpdating}
            subAgentOptions={props.subAgentOptions}
            planApprovalTargetOptions={props.planApprovalTargetOptions}
            toolConfigSchemas={props.payload.tool_config_schemas || {}}
            toolConfigEffective={props.payload.tool_config_effective || {}}
            payload={props.payload}
            onToggleListValue={props.onToggleListValue}
            onToggleToolGroup={props.onToggleToolGroup}
            onToggleMcpInstall={props.onToggleMcpInstall}
            onUpdateToolConfig={props.onUpdateToolConfig}
            onResetToolConfigField={props.onResetToolConfigField}
          />
        </Show>
        <Show when={props.activeTab === "prompt"}>
          <AgentPromptTab
            readOnly={props.readOnly}
            value={props.form.system_prompt}
            variables={props.promptVariables}
            onChange={(value) => props.onUpdate("system_prompt", value)}
            onInsertVariable={props.onInsertVariable}
          />
        </Show>
      </section>
    </div>
  );
}
