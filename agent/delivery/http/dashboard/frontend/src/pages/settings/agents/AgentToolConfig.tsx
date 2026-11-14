import { createSignal, For, Show } from "solid-js";
import { RotateCcw } from "lucide-solid";
import { useLocation, useNavigate } from "@solidjs/router";

import { ModelPicker } from "@/components/ModelPicker";
import type { AgentsPayload, ToolConfigField, ToolConfigSchema } from "@/types";
import type { AgentForm, ToolConfigValue } from "./agentForm";

type Props = {
  tool: string;
  form: AgentForm;
  readOnly: boolean;
  schema: ToolConfigSchema;
  effective?: Record<string, Record<string, unknown>>;
  payload: AgentsPayload;
  onUpdate: (tool: string, field: string, value: ToolConfigValue) => void;
  onReset: (tool: string, field: string) => void;
};

export function AgentToolConfig(props: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const [advanced, setAdvanced] = createSignal<boolean>();
  const hasOverride = (name: string) =>
    Object.prototype.hasOwnProperty.call(
      props.form.tool_configs[props.tool] || {},
      name,
    );
  const effectiveValue = (field: ToolConfigField) => {
    const override = props.form.tool_configs[props.tool]?.[field.name];
    return override !== undefined && override !== null && override !== ""
      ? override
      : (props.effective?.[props.tool]?.[field.name] ?? field.default);
  };
  const fieldValue = (field: ToolConfigField) =>
    hasOverride(field.name)
      ? props.form.tool_configs[props.tool][field.name]
      : field.input_type === "boolean"
        ? Boolean(effectiveValue(field))
        : "";
  const isWebTool = () =>
    props.tool === "web_search" || props.tool === "web_fetch";
  const isCredential = (field: ToolConfigField) =>
    isWebTool() &&
    field.name !== "provider" &&
    !field.name.endsWith("_connection");
  const advancedOpen = () =>
    advanced() ??
    Object.keys(props.form.tool_configs[props.tool] || {}).some(
      (name) => name !== "provider" && !name.endsWith("_connection"),
    );
  const provider = () => {
    const field = props.schema.fields.find(
      (field) => field.name === "provider",
    );
    return String(
      field ? (effectiveValue(field) ?? "auto") : "auto",
    ).toLowerCase();
  };
  const connectionKind = () => {
    const current = provider();
    if (
      props.tool === "web_search" &&
      ["google", "tavily", "brave", "bing", "firecrawl"].includes(current)
    )
      return current;
    if (props.tool === "web_fetch" && ["firecrawl", "tavily"].includes(current))
      return current;
    return props.tool === "web_fetch" ? "firecrawl" : "tavily";
  };
  const inheritedLabel = (field: ToolConfigField) => {
    const global = props.effective?.[props.tool]?.[field.name];
    if (global !== undefined && global !== null && global !== "") {
      if (field.secret || field.input_type === "password")
        return "Global (set)";
      return `Global: ${global === "__default__" ? "shared default" : String(global)}`;
    }
    return field.default === undefined ||
      field.default === null ||
      field.default === ""
      ? "Inherit"
      : `Inherit (${String(field.default)})`;
  };
  const inheritedSource = (field: ToolConfigField) => {
    const kind = field.name.split("_")[0];
    const reference =
      props.form.tool_configs[props.tool]?.[`${kind}_connection`];
    if (!reference)
      return (
        props.payload.tool_config_sources?.[props.tool]?.[field.name] ||
        "default"
      );
    const name =
      reference === "__default__"
        ? props.payload.web_connections?.defaults[kind]
        : String(reference);
    const connection = props.payload.web_connections?.connections.find(
      (entry) => entry.name.toLowerCase() === name?.toLowerCase(),
    );
    if (!connection) return "environment or service default";
    const source = connection.sources[field.name.slice(kind.length + 1)];
    return source === "connection" ? `connection:${connection.name}` : source;
  };
  const visible = (field: ToolConfigField) => {
    if (props.tool === "generate_image" && field.name === "provider")
      return false;
    if (isCredential(field) && !advancedOpen() && !hasOverride(field.name))
      return false;
    return Object.entries(field.show_when || {}).every(([name, allowed]) => {
      const dependency = props.schema.fields.find((item) => item.name === name);
      return allowed.includes(
        String(dependency ? (effectiveValue(dependency) ?? "") : ""),
      );
    });
  };
  const change = (field: ToolConfigField, value: string) => {
    if (value === "") props.onReset(props.tool, field.name);
    else props.onUpdate(props.tool, field.name, value);
  };

  return (
    <div class="agent-tool-config">
      <h4>Configuration</h4>
      <p class="hint">
        Inherit workspace settings or override them for this agent.
      </p>
      <Show when={isWebTool()}>
        <button
          class="btn btn-sm"
          type="button"
          disabled={props.readOnly}
          onClick={() =>
            navigate(
              `/settings/providers?tab=web&new=${connectionKind()}&returnTo=${encodeURIComponent(location.pathname)}`,
            )
          }
        >
          Create a separate connection
        </button>
      </Show>
      <div class="agent-tool-fields">
        <For each={props.schema.fields}>
          {(field) => (
            <Show when={visible(field)}>
              <div class="agent-tool-field">
                <div class="agent-tool-field-heading">
                  <label
                    for={`agent-tool-${props.tool}-${field.name}`}
                    title={field.description || undefined}
                  >
                    {field.label}
                    <Show when={field.required}>
                      <span class="setting-required" title="Required">
                        {" "}
                        *
                      </span>
                    </Show>
                  </label>
                  <Show when={hasOverride(field.name)}>
                    <span class="agent-tools-badge">Override</span>
                  </Show>
                </div>
                <Show
                  when={
                    props.tool === "generate_image" && field.name === "model"
                  }
                  fallback={
                    <div class="agent-tool-field-control">
                      <Show
                        when={field.input_type === "boolean"}
                        fallback={
                          <Show
                            when={field.input_type === "select"}
                            fallback={
                              <input
                                class="input"
                                id={`agent-tool-${props.tool}-${field.name}`}
                                type={
                                  field.input_type === "number"
                                    ? "number"
                                    : field.input_type === "password" ||
                                        field.secret
                                      ? "password"
                                      : "text"
                                }
                                value={String(fieldValue(field) ?? "")}
                                placeholder={inheritedLabel(field)}
                                min={field.min}
                                max={field.max}
                                step={field.step}
                                disabled={props.readOnly}
                                onInput={(event) =>
                                  change(field, event.currentTarget.value)
                                }
                              />
                            }
                          >
                            <select
                              class="input"
                              id={`agent-tool-${props.tool}-${field.name}`}
                              value={String(fieldValue(field) ?? "")}
                              disabled={props.readOnly}
                              onChange={(event) =>
                                change(field, event.currentTarget.value)
                              }
                            >
                              <option value="">{inheritedLabel(field)}</option>
                              <For each={field.options}>
                                {(option) => (
                                  <option value={option}>
                                    {option === "__default__"
                                      ? "Use shared default"
                                      : option}
                                  </option>
                                )}
                              </For>
                            </select>
                          </Show>
                        }
                      >
                        <label class="agent-tool-boolean">
                          <input
                            type="checkbox"
                            id={`agent-tool-${props.tool}-${field.name}`}
                            checked={Boolean(fieldValue(field))}
                            disabled={props.readOnly}
                            onChange={(event) =>
                              props.onUpdate(
                                props.tool,
                                field.name,
                                event.currentTarget.checked,
                              )
                            }
                          />
                          Enabled
                        </label>
                      </Show>
                      <button
                        class="btn btn-sm btn-ghost btn-icon"
                        type="button"
                        title="Reset override"
                        aria-label={`Reset ${field.label.toLowerCase()} override`}
                        disabled={props.readOnly || !hasOverride(field.name)}
                        onClick={() => props.onReset(props.tool, field.name)}
                      >
                        <RotateCcw size={14} />
                      </button>
                    </div>
                  }
                >
                  <div class="agent-tool-field-control">
                    <ModelPicker
                      catalogs={props.payload.model_catalogs}
                      providerNames={props.payload.provider_names}
                      defaultProvider={props.payload.default_provider}
                      defaultModel={props.payload.default_model}
                      provider={String(
                        props.form.tool_configs[props.tool]?.provider || "",
                      )}
                      model={String(fieldValue(field) || "")}
                      disabled={props.readOnly}
                      resolveDefault
                      modelFilter={(model) =>
                        Boolean(model.output_types?.includes("image"))
                      }
                      onChange={(provider, model) => {
                        props.onUpdate(props.tool, "provider", provider);
                        props.onUpdate(props.tool, "model", model);
                      }}
                    />
                    <button
                      class="btn btn-sm btn-ghost btn-icon"
                      type="button"
                      title="Reset model override"
                      aria-label="Reset image model override"
                      disabled={
                        props.readOnly ||
                        (!hasOverride("provider") && !hasOverride("model"))
                      }
                      onClick={() => {
                        props.onReset(props.tool, "provider");
                        props.onReset(props.tool, "model");
                      }}
                    >
                      <RotateCcw size={14} />
                    </button>
                  </div>
                </Show>
                <Show when={field.description}>
                  <p class="agent-tool-field-note">{field.description}</p>
                </Show>
                <Show when={!hasOverride(field.name)}>
                  <p class="agent-tool-field-note">
                    {isCredential(field)
                      ? `Inherited source: ${inheritedSource(field)}`
                      : inheritedLabel(field)}
                  </p>
                </Show>
              </div>
            </Show>
          )}
        </For>
      </div>
      <Show when={isWebTool()}>
        <button
          class="btn btn-sm btn-ghost agent-tool-advanced"
          type="button"
          aria-expanded={advancedOpen()}
          disabled={props.readOnly}
          onClick={() => setAdvanced(!advancedOpen())}
        >
          Advanced credential overrides
        </button>
      </Show>
      <Show when={props.tool === "web_search" && provider() === "duckduckgo"}>
        <p class="hint">DuckDuckGo operates without API credentials.</p>
      </Show>
    </div>
  );
}
