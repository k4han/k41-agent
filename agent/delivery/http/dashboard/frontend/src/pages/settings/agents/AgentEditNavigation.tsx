import "@/styles/agent-editor.css";

import { createMemo, For, Show } from "solid-js";
import {
  FileText,
  LayoutGrid,
  Plug,
  Settings2,
  ShieldCheck,
  Users,
} from "lucide-solid";
import type { AgentTab } from "./agentForm";

const tabs: { id: AgentTab; label: string; icon: typeof Settings2 }[] = [
  { id: "general", label: "General", icon: Settings2 },
  { id: "tools", label: "Tools", icon: LayoutGrid },
  { id: "mcp", label: "MCP", icon: Plug },
  { id: "delegation", label: "Delegation", icon: Users },
  { id: "permissions", label: "Permissions", icon: ShieldCheck },
  { id: "prompt", label: "System prompt", icon: FileText },
];

export function AgentEditNavigation(props: {
  activeTab?: AgentTab;
  acp?: boolean;
  disabled?: boolean;
  counts?: Partial<Record<AgentTab, number>>;
  onChange?: (tab: AgentTab) => void;
}) {
  const visibleTabs = createMemo(() =>
    tabs.filter((tab) => tab.id !== "delegation" || !props.acp),
  );
  const handleKey = (event: KeyboardEvent, current: AgentTab) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const available = visibleTabs();
    const index = available.findIndex((tab) => tab.id === current);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? available.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + available.length) %
            available.length;
    props.onChange?.(available[next].id);
    document.getElementById(`agent-editor-tab-${available[next].id}`)?.focus();
  };

  return (
    <nav
      class="agent-editor-navigation"
      role="tablist"
      aria-label="Agent configuration"
      aria-hidden={props.disabled ? "true" : undefined}
    >
      <For each={visibleTabs()}>
        {(tab) => (
          <button
            type="button"
            role="tab"
            id={`agent-editor-tab-${tab.id}`}
            class="agent-editor-tab"
            aria-selected={props.activeTab === tab.id}
            aria-controls={`agent-editor-panel-${tab.id}`}
            tabIndex={!props.disabled && props.activeTab === tab.id ? 0 : -1}
            disabled={props.disabled}
            onClick={() => props.onChange?.(tab.id)}
            onKeyDown={(event) => handleKey(event, tab.id)}
          >
            <tab.icon size={16} aria-hidden="true" />
            <span class="agent-editor-tab-label">{tab.label}</span>
            <Show when={props.counts?.[tab.id] !== undefined}>
              <span class="agent-editor-tab-count">
                {props.counts?.[tab.id]}
              </span>
            </Show>
          </button>
        )}
      </For>
    </nav>
  );
}
