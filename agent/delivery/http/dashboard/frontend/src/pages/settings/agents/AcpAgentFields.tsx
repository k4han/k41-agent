import { createResource, For, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import type { AcpAgentStatus } from "@/lib/acp";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";

import type { AgentForm } from "./agentForm";

const AGENT_DEFAULT = "";

export function AcpAgentFields(props: {
  form: AgentForm;
  readOnly: boolean;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  const settings = () => props.form.acp ?? { agent: "antigravity-acp", config_options: {} };
  const [status] = createResource(
    () => settings().agent,
    (agent) => apiFetch<AcpAgentStatus>(API_PATHS.acpAgent(agent)),
  );

  const setOption = (id: string, value: string) => {
    const { [id]: _removed, ...rest } = settings().config_options;
    props.onUpdate("acp", { ...settings(), config_options: value === AGENT_DEFAULT ? rest : { ...rest, [id]: value } });
  };

  return (
    <div class="stack" style="gap: 16px;">
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
