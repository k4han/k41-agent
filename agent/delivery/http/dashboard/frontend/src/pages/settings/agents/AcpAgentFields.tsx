import { createEffect, createResource, createSignal, For, onCleanup, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import type { AcpAgentStatus } from "@/lib/acp";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";

import { normalizeAcpSettings, type AgentForm } from "./agentForm";
import type { AcpAgentSettings } from "@/types";

const AGENT_DEFAULT = "";

export function AcpAgentFields(props: {
  form: AgentForm;
  readOnly: boolean;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  const settings = (): AcpAgentSettings => normalizeAcpSettings(props.form.acp);
  const [statusUrl, setStatusUrl] = createSignal("");
  createEffect(() => {
    const url = API_PATHS.acpAgent(settings().agent);
    const timer = setTimeout(() => setStatusUrl(url), 350);
    onCleanup(() => clearTimeout(timer));
  });
  const [status] = createResource(
    () => statusUrl() || false,
    (url) => apiFetch<AcpAgentStatus>(url),
  );
  const currentStatus = () => (statusUrl() === API_PATHS.acpAgent(settings().agent) && !status.loading) ? status() : undefined;

  const setOption = (id: string, value: string) => {
    const { [id]: _removed, ...rest } = settings().config_options;
    props.onUpdate("acp", { ...settings(), config_options: value === AGENT_DEFAULT ? rest : { ...rest, [id]: value } });
  };

  return (
    <div class="stack" style="gap: 16px;">
      <p class="hint">Configure this card's system prompt, tools, MCP and permissions in their tabs. Native AGY tools are grouped under ACP in Tools. Card instructions and selected tools are sent each time a new session starts.</p>
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
