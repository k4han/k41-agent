import { createMemo, Show } from "solid-js";
import { ModelPicker } from "@/components/ModelPicker";
import { SelectControl } from "@/components/SelectControl";
import type { AgentsPayload } from "@/types";
import { resolveModelAndProvider } from "@/lib/modelSelection";

import type { AgentForm } from "./agentForm";

export function AgentGeneralTab(props: {
  form: AgentForm;
  readOnly: boolean;
  workflows: AgentsPayload["workflows"];
  payload: AgentsPayload;
  onUpdate: <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => void;
}) {
  const modelOption = createMemo(() => {
    const { provider, model } = resolveModelAndProvider(
      props.form.provider, props.form.model,
      props.payload.default_provider, props.payload.default_model, props.payload.model_catalogs,
    );
    const catalog = props.payload.model_catalogs.find((item) => item.provider === provider);
    return catalog?.models.find((item) => item.id === model);
  });
  const effortLevels = createMemo(() => modelOption()?.reasoning_effort_levels);
  const effortOptions = createMemo(() => {
    const defaultEffort = modelOption()?.reasoning_effort_default;
    const options = [
      { value: "", label: defaultEffort ? `Auto (${defaultEffort})` : "Auto (provider default)" },
      ...(effortLevels() || []).map((level) => ({ value: level, label: level })),
    ];
    const configured = props.form.reasoning_effort;
    if (configured && !options.some((option) => option.value === configured)) {
      options.push({ value: configured, label: `${configured} (configured)` });
    }
    return options;
  });

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
              if (provider !== props.form.provider || model !== props.form.model) {
                props.onUpdate("reasoning_effort", null);
              }
              props.onUpdate("provider", provider);
              props.onUpdate("model", model);
            }}
          />
          <div class="field" style="margin-top: 12px;">
            <label>Reasoning Effort</label>
            <Show
              when={effortLevels() != null}
              fallback={
                <input
                  class="input"
                  aria-label="Agent reasoning effort"
                  value={props.form.reasoning_effort || ""}
                  disabled={props.readOnly}
                  placeholder="Provider default"
                  pattern="[a-z][a-z0-9_-]{0,63}"
                  onInput={(event) => props.onUpdate("reasoning_effort", event.currentTarget.value || null)}
                />
              }
            >
              <SelectControl
                value={props.form.reasoning_effort || ""}
                options={effortOptions()}
                disabled={props.readOnly}
                ariaLabel="Agent reasoning effort"
                onChange={(value) => props.onUpdate("reasoning_effort", value || null)}
              />
            </Show>
            <p class="hint">
              {effortLevels()?.length === 0
                ? "This model has no configurable effort. Choose Auto to clear a saved override."
                : "Default reasoning effort for this agent. Auto uses the model's default."}
            </p>
          </div>
        </div>
      </div>
      <div class="field">
        <label>Context Compact Threshold (%)</label>
        <input
          class="input"
          type="number"
          min="1"
          max="100"
          step="1"
          value={props.form.context_compact_threshold}
          disabled={props.readOnly}
          onInput={(event) =>
            props.onUpdate("context_compact_threshold", Number(event.currentTarget.value))
          }
        />
        <p class="hint">Automatically summarize older context before each model call when this percentage of the model context window is reached. Default: 75%.</p>
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
