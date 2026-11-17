import { createEffect, createResource, createSignal, For, onCleanup, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import { ANTIGRAVITY_CLI_AGENT_ID, type AcpAgentStatus } from "@/lib/acp";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";

import type { AgentForm } from "./agentForm";
import type { AcpAgentSettings } from "@/types";

const AGENT_DEFAULT = "";

export function AcpAgentFields(props: {
  form: AgentForm;
  readOnly: boolean;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  const settings = (): AcpAgentSettings => props.form.acp ?? { agent: ANTIGRAVITY_CLI_AGENT_ID, agent_name: null, config_options: {} };
  const [statusUrl, setStatusUrl] = createSignal("");
  createEffect(() => {
    const url = API_PATHS.acpAgent(settings().agent, settings().agent_name);
    const timer = setTimeout(() => setStatusUrl(url), 350);
    onCleanup(() => clearTimeout(timer));
  });
  const [status] = createResource(
    () => statusUrl() || false,
    (url) => apiFetch<AcpAgentStatus>(url),
  );
  const currentStatus = () => (statusUrl() === API_PATHS.acpAgent(settings().agent, settings().agent_name) && !status.loading) ? status() : undefined;

  const setOption = (id: string, value: string) => {
    const { [id]: _removed, ...rest } = settings().config_options;
    props.onUpdate("acp", { ...settings(), config_options: value === AGENT_DEFAULT ? rest : { ...rest, [id]: value } });
  };

  return (
    <div class="stack" style="gap: 16px;">
      <div class="field">
        <label for="acp-default-tools">
          <input id="acp-default-tools" type="checkbox"
            checked={settings().use_default_tools !== false} disabled={props.readOnly}
            onChange={(event) => props.onUpdate("acp", { ...settings(), use_default_tools: event.currentTarget.checked, agent_name: null })} />
          {" "}Use AGY default tools
        </label>
        <p class="hint">Turn off AGY's default file, command and web tools to use this card's prompt and K41 tools. AGY may still expose internal task and MCP resource helpers.</p>
      </div>
      <div class="field">
        <label for="acp-agent-name">AGY agent profile</label>
        <input id="acp-agent-name" class="input" value={settings().agent_name ?? ""}
          disabled={props.readOnly || settings().use_default_tools === false} maxLength={128} placeholder="AGY default"
          onInput={(event) => props.onUpdate("acp", { ...settings(), agent_name: event.currentTarget.value || null, config_options: {} })} />
        <p class="hint">Optional existing AGY profile passed to --agent. Leave blank to use the AGY default. Workspace profiles are resolved in the conversation's working directory.</p>
      </div>
      <p class="hint">Configure this card's system prompt, tools, MCP and permissions in their tabs. Card instructions and tools are sent to the agent each time a new session starts.</p>
      <Show when={status.error}>
        <p class="hint" role="alert">Could not load this agent's settings.</p>
      </Show>
      <Show when={currentStatus() && !currentStatus()?.auth.signed_in}>
        <p class="hint">
          {currentStatus()?.auth.error || "Check agy installation and sign-in on the machine running K41."}
          {" "}<a href="/settings/acp">ACP agents</a>
        </p>
      </Show>
      <For each={currentStatus()?.auth.config_options ?? []}>{(option) => (
        <div class="field">
          <label>{option.name}</label>
          <SelectControl
            value={settings().config_options[option.id] ?? AGENT_DEFAULT}
            options={[
              { value: AGENT_DEFAULT, label: "Agent default" },
              ...option.options.map((item) => ({ value: item.value, label: item.name })),
            ]}
            disabled={props.readOnly}
            ariaLabel={option.name}
            onChange={(value) => setOption(option.id, value)}
          />
        </div>
      )}</For>
    </div>
  );
}
