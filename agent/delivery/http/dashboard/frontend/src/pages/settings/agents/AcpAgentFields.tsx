import { createResource, For, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import type { AcpConfigOption } from "@/lib/acp";
import { apiFetch } from "@/lib/api";

import type { AgentForm } from "./agentForm";

type AgentStatus = { display_name: string; auth: { signed_in: boolean; config_options: AcpConfigOption[] } };

const AGENT_DEFAULT = "";

export function AcpAgentFields(props: {
  form: AgentForm;
  readOnly: boolean;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  const settings = () => props.form.acp ?? { agent: "antigravity-acp", permission_mode: "ask" as const, config_options: {} };
  const [status] = createResource(
    () => settings().agent,
    (agent) => apiFetch<AgentStatus>(`/dashboard-api/acp/agents/${encodeURIComponent(agent)}`),
  );

  const setOption = (id: string, value: string) => {
    const { [id]: _removed, ...rest } = settings().config_options;
    props.onUpdate("acp", { ...settings(), config_options: value === AGENT_DEFAULT ? rest : { ...rest, [id]: value } });
  };

  return (
    <div class="stack" style="gap: 16px;">
      <div class="field">
        <label>Tool approvals</label>
        <SelectControl
          value={settings().permission_mode}
          options={[
            { value: "ask", label: "Ask on the dashboard (deny elsewhere)" },
            { value: "auto_allow", label: "Allow everything the agent asks for" },
          ]}
          disabled={props.readOnly}
          ariaLabel="Tool approvals"
          onChange={(value) => props.onUpdate("acp", { ...settings(), permission_mode: value as "ask" | "auto_allow" })}
        />
        <Show when={settings().permission_mode === "auto_allow"}>
          <p class="hint" role="alert">
            The agent can then run any command on this machine with K41's access, including when a file or web page it reads tells it to. Only use this for work you would run unattended.
          </p>
        </Show>
      </div>
      <Show when={status.error}>
        <p class="hint" role="alert">Could not load this agent's settings.</p>
      </Show>
      <Show when={status() && !status()?.auth.signed_in}>
        <p class="hint">
          Install and sign in under <a href="/settings/acp">ACP agents</a> to choose a model.
        </p>
      </Show>
      <For each={status()?.auth.config_options ?? []}>{(option) => (
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
