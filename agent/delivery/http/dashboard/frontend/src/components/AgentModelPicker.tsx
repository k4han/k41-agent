import { Bot, Check, ChevronDown, Search, Sparkles, X } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import type { AgentCard, ModelCatalog } from "@/types";

export interface AgentModelPickerProps {
  agentName: string;
  agents: AgentCard[];
  onAgentChange: (name: string) => void;
  provider: string;
  model: string;
  onProviderModelChange: (provider: string, model: string) => void;
  catalogs: ModelCatalog[];
  providerNames: string[];
  defaultProvider: string;
  defaultModel: string;
  disabled?: boolean;
  class?: string;
}

export function formatShortModelName(provider: string, model: string): string {
  if (!model || model === "provider default") {
    return provider ? `${provider} default` : "default";
  }
  let name = model;
  if (name.includes("/")) {
    name = name.split("/")[1] || name;
  }
  if (name.includes("claude-3-7-sonnet")) return "Sonnet 3.7";
  if (name.includes("claude-3-5-sonnet")) return "Sonnet 3.5";
  if (name.includes("claude-3-5-haiku")) return "Haiku 3.5";
  if (name.includes("claude-3-opus")) return "Opus 3";
  if (name.includes("gpt-4o-mini")) return "GPT-4o Mini";
  if (name.includes("gpt-4o")) return "GPT-4o";
  if (name.includes("gpt-4-turbo")) return "GPT-4 Turbo";
  if (name.includes("o3-mini")) return "o3-mini";
  if (name.includes("o1-mini")) return "o1-mini";
  if (name.includes("o1-preview")) return "o1";
  if (name.includes("gemini-2.5-flash")) return "Gemini 2.5 Flash";
  if (name.includes("gemini-2.0-flash")) return "Gemini 2.0 Flash";
  if (name.includes("gemini-1.5-pro")) return "Gemini 1.5 Pro";
  if (name.includes("gemini-1.5-flash")) return "Gemini 1.5 Flash";
  if (name.length > 20) {
    return name.slice(0, 18) + "...";
  }
  return name;
}

export function AgentModelPicker(props: AgentModelPickerProps) {
  const [open, setOpen] = createSignal(false);
  const [searchQuery, setSearchQuery] = createSignal("");
  const [activePicker, setActivePicker] = createSignal<"agent" | "model">("agent");
  const [menuPos, setMenuPos] = createSignal({ edge: 8, left: 16, width: 360, maxHeight: 440, above: true });
  let rootRef: HTMLDivElement | undefined;
  let agentTriggerRef: HTMLButtonElement | undefined;
  let modelTriggerRef: HTMLButtonElement | undefined;
  let menuRef: HTMLDivElement | undefined;
  let searchInputRef: HTMLInputElement | undefined;
  const triggerRef = () => activePicker() === "agent" ? agentTriggerRef : modelTriggerRef;

  const togglePicker = (tab: "agent" | "model") => {
    if (props.disabled) return;
    const wasOpen = open() && activePicker() === tab;
    setSearchQuery("");
    setActivePicker(tab);
    setOpen(!wasOpen);
  };

  const selectAgent = (agent: AgentCard) => {
    props.onAgentChange(agent.name);
    if (agent.provider || agent.model) {
      props.onProviderModelChange(agent.provider || "default", agent.model || "");
    }
    setOpen(false);
  };

  const updateMenuPosition = () => {
    const trigger = triggerRef();
    if (!trigger || !open()) return;
    const rect = trigger.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const gap = 8;
    const width = Math.min(360, viewportWidth - 32);
    let left = rect.left;
    if (left + width > viewportWidth - 16) {
      left = Math.max(16, viewportWidth - width - 16);
    }
    const above = rect.top >= viewportHeight - rect.bottom;
    setMenuPos({
      edge: above ? viewportHeight - rect.top + gap : rect.bottom + gap,
      left,
      width,
      maxHeight: Math.max(0, (above ? rect.top : viewportHeight - rect.bottom) - gap * 2),
      above,
    });
  };

  createEffect(() => {
    if (!open()) return;
    updateMenuPosition();
    const handler = () => updateMenuPosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    onCleanup(() => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    });
  });

  const selectedAgent = createMemo(() =>
    props.agents.find((a) => a.name === props.agentName) || props.agents[0]
  );

  const currentAgentName = createMemo(() =>
    selectedAgent()?.display_name || props.agentName || "Default"
  );

  const resolvedSelection = createMemo(() => {
    const activeProvider = props.provider || selectedAgent()?.provider || "default";
    const prov = activeProvider === "default" ? props.defaultProvider : activeProvider;
    let mod = props.model || selectedAgent()?.model || "";
    if (!mod || mod === "default" || mod === "provider default") {
      const catalog = props.catalogs.find((c) => c.provider === prov);
      mod = activeProvider === "default" ? props.defaultModel : (catalog?.default_model || "");
    }
    return { provider: prov, model: mod };
  });

  const shortModelLabel = createMemo(() =>
    formatShortModelName(resolvedSelection().provider, resolvedSelection().model)
  );

  const filteredModelGroups = createMemo(() => {
    const q = searchQuery().trim().toLowerCase();
    const groups: Array<{ provider: string; models: Array<{ id: string; label: string; contextWindow?: number }> }> = [];

    props.catalogs.forEach((cat) => {
      const matched = cat.models.filter((m) => {
        if (!q) return true;
        return (
          m.id.toLowerCase().includes(q) ||
          (m.label && m.label.toLowerCase().includes(q)) ||
          cat.provider.toLowerCase().includes(q)
        );
      });
      if (matched.length > 0) {
        groups.push({
          provider: cat.provider,
          models: matched.map((m) => ({
            id: m.id,
            label: m.label || m.id,
            contextWindow: m.context_window,
          })),
        });
      }
    });
    return groups;
  });

  const hasExactMatch = createMemo(() => {
    const q = searchQuery().trim().toLowerCase();
    if (!q) return true;
    for (const group of filteredModelGroups()) {
      for (const m of group.models) {
        if (m.id.toLowerCase() === q) return true;
      }
    }
    return false;
  });

  const handlePointerDown = (event: MouseEvent) => {
    const target = event.target as Node | null;
    if (target && rootRef?.contains(target)) return;
    if (target && menuRef?.contains(target)) return;
    if (open()) {
      setOpen(false);
      setSearchQuery("");
    }
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && open()) {
      setOpen(false);
      setSearchQuery("");
      triggerRef()?.focus();
    }
  };

  onMount(() => {
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
  });

  onCleanup(() => {
    document.removeEventListener("mousedown", handlePointerDown);
    document.removeEventListener("keydown", handleKeyDown);
  });

  createEffect(() => {
    if (open() && activePicker() === "model" && searchInputRef) {
      setTimeout(() => searchInputRef?.focus(), 50);
    }
  });

  return (
    <div class={`agent-model-picker-wrapper ${props.class || ""}`} ref={rootRef} role="group" aria-label="Agent and model">
      <button
        ref={agentTriggerRef}
        class={`agent-model-badge agent-picker-trigger ${open() && activePicker() === "agent" ? "is-open" : ""}`}
        type="button"
        onClick={() => togglePicker("agent")}
        disabled={props.disabled}
        title={`Agent: ${currentAgentName()}`}
        aria-label={`Select agent: ${currentAgentName()}`}
        aria-haspopup="dialog"
        aria-expanded={open() && activePicker() === "agent"}
      >
        <span class="agent-model-badge-icon">
          <Bot size={14} />
        </span>
        <span class="agent-model-badge-agent">{currentAgentName()}</span>
        <ChevronDown size={13} class={`agent-model-badge-caret ${open() && activePicker() === "agent" ? "is-open" : ""}`} />
      </button>
      <button
        ref={modelTriggerRef}
        class={`agent-model-badge model-picker-trigger ${open() && activePicker() === "model" ? "is-open" : ""}`}
        type="button"
        onClick={() => togglePicker("model")}
        disabled={props.disabled}
        title={`Model: ${resolvedSelection().provider}/${resolvedSelection().model}`}
        aria-label={`Select model: ${resolvedSelection().provider}/${resolvedSelection().model}`}
        aria-haspopup="dialog"
        aria-expanded={open() && activePicker() === "model"}
      >
        <span class="agent-model-badge-model">{shortModelLabel()}</span>
        <ChevronDown size={13} class={`agent-model-badge-caret ${open() && activePicker() === "model" ? "is-open" : ""}`} />
      </button>

      <Show when={open()}>
        <Portal>
        <div
          ref={menuRef}
          class="agent-model-popover agent-model-popover--portal"
          role="dialog"
          aria-label={activePicker() === "agent" ? "Select Agent" : "Select Model"}
          style={{
            top: menuPos().above ? "auto" : `${menuPos().edge}px`,
            bottom: menuPos().above ? `${menuPos().edge}px` : "auto",
            left: `${menuPos().left}px`,
            width: `${menuPos().width}px`,
            "max-height": `${menuPos().maxHeight}px`,
          }}
        >
          <div class="agent-model-popover-header">
            <div class="agent-model-popover-title-wrap">
              <Sparkles size={14} class="agent-model-popover-title-icon" />
              <span class="agent-model-popover-title">{activePicker() === "agent" ? "Select Agent" : "Select Model"}</span>
            </div>
            <button
              class="agent-model-popover-close"
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>

          <div class="agent-model-popover-body">
            <Show when={activePicker() === "agent"}>
              <div class="agent-model-column agent-model-agents-col">
                <div class="agent-model-list">
                  <For each={props.agents}>
                    {(agent) => {
                      const isSelected = () =>
                        agent.name === (selectedAgent()?.name || props.agentName);
                      return (
                        <div
                          class={`agent-card-item ${isSelected() ? "selected" : ""}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => selectAgent(agent)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              selectAgent(agent);
                            }
                          }}
                        >
                          <div class="agent-card-item-left">
                            <span class="agent-card-item-icon">
                              <Bot size={15} />
                            </span>
                            <div class="agent-card-item-details">
                              <div class="agent-card-item-name">
                                {agent.display_name || agent.name}
                              </div>
                              <Show when={agent.description}>
                                <div class="agent-card-item-desc">
                                  {agent.description}
                                </div>
                              </Show>
                            </div>
                          </div>
                          <Show when={isSelected()}>
                            <span class="agent-card-item-check">
                              <Check size={14} />
                            </span>
                          </Show>
                        </div>
                      );
                    }}
                  </For>
                </div>
              </div>
            </Show>

            <Show when={activePicker() === "model"}>
              <div class="agent-model-column agent-model-models-col">
                <div class="agent-model-search-wrap">
                  <Search size={13} class="agent-model-search-icon" />
                  <input
                    ref={searchInputRef}
                    class="agent-model-search-input"
                    type="text"
                    placeholder="Search models..."
                    value={searchQuery()}
                    onInput={(e) => setSearchQuery(e.currentTarget.value)}
                  />
                  <Show when={searchQuery()}>
                    <button
                      class="agent-model-search-clear"
                      type="button"
                      onClick={() => setSearchQuery("")}
                    >
                      <X size={12} />
                    </button>
                  </Show>
                </div>

                <div class="agent-model-list">
                  <For each={filteredModelGroups()}>
                    {(group) => (
                      <div class="model-provider-group">
                        <div class="model-provider-group-title">{group.provider}</div>
                        <For each={group.models}>
                          {(modelOption) => {
                            const isSelected = () =>
                              resolvedSelection().provider === group.provider &&
                              resolvedSelection().model === modelOption.id;
                            return (
                              <div
                                class={`model-item-row ${isSelected() ? "selected" : ""}`}
                                role="button"
                                tabIndex={0}
                                onClick={() => {
                                  props.onProviderModelChange(group.provider, modelOption.id);
                                  setOpen(false);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    props.onProviderModelChange(group.provider, modelOption.id);
                                    setOpen(false);
                                  }
                                }}
                              >
                                <div class="model-item-row-info">
                                  <span class="model-item-row-name">
                                    {modelOption.label || modelOption.id}
                                  </span>
                                  <Show when={modelOption.contextWindow}>
                                    <span class="model-item-row-desc">
                                      {Math.round(modelOption.contextWindow! / 1024)}k context
                                    </span>
                                  </Show>
                                </div>
                                <Show when={isSelected()}>
                                  <span class="model-item-row-check">
                                    <Check size={14} />
                                  </span>
                                </Show>
                              </div>
                            );
                          }}
                        </For>
                      </div>
                    )}
                  </For>

                  <Show when={searchQuery().trim() && !hasExactMatch()}>
                    <div
                      class="model-item-row model-item-custom"
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        const query = searchQuery().trim();
                        const slashIdx = query.indexOf("/");
                        const p = slashIdx > 0 ? query.slice(0, slashIdx).trim() : resolvedSelection().provider;
                        const m = slashIdx > 0 ? query.slice(slashIdx + 1).trim() : query;
                        props.onProviderModelChange(p, m);
                        setOpen(false);
                      }}
                    >
                      <div class="model-item-row-info">
                        <span class="model-item-row-name">Use "{searchQuery().trim()}"</span>
                        <span class="model-item-row-desc">Custom model identifier</span>
                      </div>
                    </div>
                  </Show>
                </div>
              </div>
            </Show>
          </div>
        </div>
        </Portal>
      </Show>
    </div>
  );
}
