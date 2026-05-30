import { Bot } from "lucide-solid";
import { createMemo, type JSX } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import type { AgentCard, AgentConfig } from "@/types";

export interface AgentPickerProps {
  value: string;
  onChange: (value: string) => void;
  agents: Array<AgentConfig | AgentCard>;
  disabled?: boolean;
  class?: string;
  style?: JSX.CSSProperties | string;
  ariaLabel?: string;
}

export function AgentPicker(props: AgentPickerProps) {
  const options = createMemo(() =>
    props.agents.map((agent) => ({
      value: agent.name,
      label: agent.display_name || agent.name,
    }))
  );

  const selectedAgent = createMemo(() =>
    props.agents.find((agent) => agent.name === props.value)
  );

  return (
    <SelectControl
      class={props.class}
      style={props.style}
      value={props.value}
      options={options()}
      disabled={props.disabled}
      onChange={props.onChange}
      ariaLabel={props.ariaLabel || "Agent"}
      title={selectedAgent()?.description || "Select agent"}
      icon={<Bot size={14} />}
    />
  );
}
