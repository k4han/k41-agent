import { For, Show } from "solid-js";

import { PromptVariableTextarea } from "@/components/PromptVariableTextarea";
import type { PromptVariable } from "@/types";

export function AgentPromptTab(props: {
  readOnly: boolean;
  value: string;
  variables: PromptVariable[];
  onChange: (value: string) => void;
  onInsertVariable: (name: string) => void;
  textareaRef?: HTMLTextAreaElement | undefined;
}) {
  return (
    <div
      style={{
        display: "grid",
        "grid-template-columns": "minmax(0, 1fr) 280px",
        gap: "20px",
        padding: "4px 2px",
        "min-height": "0",
      }}
    >
      <div
        class="field"
        style={{
          display: "flex",
          "flex-direction": "column",
          "min-height": "420px",
        }}
      >
        <label>System Prompt</label>
        <p class="hint" style="margin-bottom: 4px;">
          Use prompt variables with double braces, for example <span class="mono">{"{{common_rules}}"}</span>.
          Type <span class="mono">{"{{"}</span> to see suggestions.
        </p>
        <PromptVariableTextarea
          ref={props.textareaRef}
          containerClass="full-height"
          value={props.value}
          disabled={props.readOnly}
          variables={props.variables}
          onChange={props.onChange}
        />
      </div>
      <div
        style={{
          display: "flex",
          "flex-direction": "column",
          gap: "8px",
          "border-left": "1px solid var(--border, rgba(255, 255, 255, 0.08))",
          "padding-left": "20px",
          "max-height": "480px",
        }}
      >
        <label
          style={{
            color: "var(--muted)",
            "font-size": "11px",
            "font-weight": "650",
            "text-transform": "uppercase",
            "letter-spacing": "0.05em",
          }}
        >
          Prompt Variables
        </label>
        <p class="hint" style="margin-bottom: 4px; font-size: 11px;">
          Click a variable to insert it at the cursor position.
        </p>
        <div
          style={{
            display: "flex",
            "flex-direction": "column",
            gap: "6px",
            flex: "1",
            "overflow-y": "auto",
            "padding-right": "4px",
          }}
        >
          <For each={props.variables}>
            {(variable) => (
              <button
                type="button"
                class="btn btn-sm"
                style={{
                  "justify-content": "space-between",
                  "text-align": "left",
                  "font-family": "monospace",
                  "font-size": "11px",
                  width: "100%",
                  overflow: "hidden",
                  "text-overflow": "ellipsis",
                  "white-space": "nowrap",
                  border: "1px solid var(--border, rgba(255, 255, 255, 0.08))",
                  "border-radius": "6px",
                  padding: "6px 10px",
                  background: variable.is_system
                    ? "color-mix(in srgb, var(--accent, #3b82f6) 5%, var(--surface-2, rgba(255, 255, 255, 0.02)))"
                    : "var(--surface-2, rgba(255, 255, 255, 0.02))",
                  cursor: "pointer",
                  "flex-shrink": "0",
                  display: "flex",
                  "align-items": "center",
                  gap: "6px",
                }}
                title={variable.value ? `${variable.name}: ${variable.value}` : variable.name}
                disabled={props.readOnly}
                onClick={() => props.onInsertVariable(variable.name)}
              >
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                  {`{{${variable.name}}}`}
                </span>
                <Show when={variable.is_system}>
                  <span
                    style={{
                      "font-size": "9px",
                      opacity: "0.6",
                      "text-transform": "uppercase",
                      "font-weight": "bold",
                      background: "rgba(255, 255, 255, 0.08)",
                      padding: "1px 4px",
                      "border-radius": "3px",
                      border: "1px solid rgba(255, 255, 255, 0.1)",
                      "flex-shrink": "0",
                    }}
                  >
                    sys
                  </span>
                </Show>
              </button>
            )}
          </For>
          <Show when={props.variables.length === 0}>
            <span class="hint" style="text-align: center; padding: 12px 0;">
              No variables available
            </span>
          </Show>
        </div>
      </div>
    </div>
  );
}
