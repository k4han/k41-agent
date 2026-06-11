import { Show } from "solid-js";

import type { AgentMcpInstall, AgentsPayload, PromptVariable } from "@/types";

import { type AgentForm, type AgentTab, type ToolConfigValue } from "./agentForm";
import { AgentGeneralTab } from "./AgentGeneralTab";
import { AgentPromptTab } from "./AgentPromptTab";
import { AgentToolsTab, type AgentToolGroup } from "./AgentToolsTab";

export function AgentEditTabs(props: {
  form: AgentForm;
  readOnly: boolean;
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
  onUpdateToolConfig: (toolName: string, fieldName: string, value: ToolConfigValue) => void;
  onResetToolConfigField: (toolName: string, fieldName: string) => void;
  onInsertVariable: (name: string) => void;
}) {
  return (
    <div class="stack" style="gap: 16px;">
      <div class="tab-bar">
        <button
          class={`btn btn-sm ${props.activeTab === "general" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => props.onTabChange("general")}
        >
          General Config
        </button>
        <button
          class={`btn btn-sm ${props.activeTab === "tools" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => props.onTabChange("tools")}
        >
          Capabilities & Tools
        </button>
        <button
          class={`btn btn-sm ${props.activeTab === "prompt" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => props.onTabChange("prompt")}
        >
          System Prompt
        </button>
      </div>

      <section class="panel" style="min-height: 360px;">
        <div class="panel-body">
          <Show when={props.activeTab === "general"}>
            <AgentGeneralTab
              form={props.form}
              readOnly={props.readOnly}
              workflows={props.payload.workflows}
              payload={props.payload}
              onUpdate={props.onUpdate}
            />
          </Show>
          <Show when={props.activeTab === "tools"}>
            <AgentToolsTab
              form={props.form}
              readOnly={props.readOnly}
              toolGroups={props.toolGroups}
              totalBuiltInTools={props.totalBuiltInTools}
              mcpServerOptions={props.mcpServerOptions}
              mcpInstalls={props.mcpInstalls}
              mcpUpdating={props.mcpUpdating}
              subAgentOptions={props.subAgentOptions}
              planApprovalTargetOptions={props.planApprovalTargetOptions}
              toolConfigSchemas={props.payload.tool_config_schemas || {}}
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
        </div>
      </section>
    </div>
  );
}
