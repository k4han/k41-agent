import { ModelPicker } from "@/components/ModelPicker";
import { SelectControl } from "@/components/SelectControl";
import type { AgentsPayload } from "@/types";

import type { AgentForm } from "./agentForm";

export function AgentGeneralTab(props: {
  form: AgentForm;
  readOnly: boolean;
  workflows: AgentsPayload["workflows"];
  payload: AgentsPayload;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  return (
    <div class="stack" style="gap: 16px; padding: 4px 2px;">
      <div class="grid-2">
        <div class="field">
          <label>Name</label>
          <input
            class="input"
            value={props.form.name}
            disabled={props.readOnly}
            placeholder="my-agent"
            onInput={(event) => props.onUpdate("name", event.currentTarget.value)}
          />
        </div>
        <div class="field">
          <label>Display Name</label>
          <input
            class="input"
            value={props.form.display_name}
            disabled={props.readOnly}
            placeholder="My Agent"
            onInput={(event) => props.onUpdate("display_name", event.currentTarget.value)}
          />
        </div>
      </div>
      <div class="field">
        <label>Description</label>
        <input
          class="input"
          value={props.form.description}
          disabled={props.readOnly}
          placeholder="Short description shown in the agent picker"
          onInput={(event) => props.onUpdate("description", event.currentTarget.value)}
        />
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Workflow</label>
          <SelectControl
            value={props.form.graph_type}
            options={props.workflows.map((workflow) => ({ value: workflow, label: workflow }))}
            disabled={props.readOnly}
            onChange={(value) => props.onUpdate("graph_type", value)}
            ariaLabel="Workflow"
          />
        </div>
        <div class="field">
          <label>Provider / Model</label>
          <ModelPicker
            catalogs={props.payload.model_catalogs}
            providerNames={props.payload.provider_names}
            defaultProvider={props.payload.default_provider}
            defaultModel={props.payload.default_model}
            provider={props.form.provider}
            model={props.form.model}
            disabled={props.readOnly}
            onChange={(provider, model) => {
              props.onUpdate("provider", provider);
              props.onUpdate("model", model);
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
          value={props.form.context_trim_threshold}
          disabled={props.readOnly}
          onInput={(event) =>
            props.onUpdate("context_trim_threshold", Number(event.currentTarget.value))
          }
        />
        <p class="hint">Approximate token count before older messages are trimmed from the context window.</p>
      </div>
      <div class="field">
        <label class="checkbox-row">
          <input
            type="checkbox"
            checked={props.form.hidden}
            disabled={props.readOnly}
            onChange={(event) => props.onUpdate("hidden", event.currentTarget.checked)}
          />
          <span>Hidden from chat picker</span>
        </label>
        <p class="hint">Hidden agents are not shown in the chat agent dropdown but can still be used internally.</p>
      </div>
    </div>
  );
}
