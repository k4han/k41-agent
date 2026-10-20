import { Bot, Check, ChevronDown, ChevronRight, Search, Sparkles, Star, X } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { resolveModelAndProvider } from "@/lib/modelSelection";
import { MOBILE_MEDIA_QUERY } from "@/lib/uiConstants";
import { createMediaQuery } from "@/lib/useMediaQuery";
import { parseFavoriteKey, useModelFavorites } from "@/lib/useModelFavorites";
import type { AgentCard, ModelCatalog, ModelOption } from "@/types";

type PickerModel = ModelOption & { provider: string };
type ModelGroup = { provider: string; label: string; models: PickerModel[] };

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
  reasoningEffort: string;
  reasoningEffortLevels: string[];
  reasoningEffortDefault?: string | null;
  onReasoningEffortChange: (effort: string) => void;
  disabled?: boolean;
  class?: string;
}

export function formatShortModelName(provider: string, model: string): string {
  if (provider === "@pool") return model;
  if (!model || model === "provider default") {
    return provider ? `${provider} default` : "default";
  }
  let name = model;
  if (name.includes("/")) {
    name = name.split("/").at(-1) || name;
  }
  if (name.includes("claude-3-7-sonnet")) return "Sonnet 3.7";
  if (name.includes("claude-3-5-sonnet")) return "Sonnet 3.5";
  if (name.includes("claude-3-5-haiku")) return "Haiku 3.5";
  if (name.includes("claude-3-opus")) return "Opus 3";
  if (name.includes("gpt-4o-mini")) return "GPT-4o Mini";
  if (name.includes("gpt-4o")) return "GPT-4o";
  if (name.includes("gpt-4-turbo")) return "GPT-4 Turbo";
  if (/^gpt-\d/i.test(name)) {
    return name.replace(/^gpt/i, "GPT").replace(/-(sol|astra|luna)\b/g,
      (_, variant: string) => ` ${variant[0].toUpperCase()}${variant.slice(1)}`);
  }
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
  const isMobileViewport = createMediaQuery(MOBILE_MEDIA_QUERY);
  const { favorites, isFavorite, toggleFavorite } = useModelFavorites();
  const [open, setOpen] = createSignal(false);
  const [searchQuery, setSearchQuery] = createSignal("");
  const [activePicker, setActivePicker] = createSignal<"agent" | "model">("agent");
  const [previewModel, setPreviewModel] = createSignal<{ provider: string; id: string }>();
  const [menuPos, setMenuPos] = createSignal({ edge: 8, left: 16, width: 360, maxHeight: 440, above: true });
  const [effortPos, setEffortPos] = createSignal({ top: 0, left: 0, width: 160 });
  let rootRef: HTMLDivElement | undefined;
  let agentTriggerRef: HTMLButtonElement | undefined;
  let modelTriggerRef: HTMLButtonElement | undefined;
  let menuRef: HTMLDivElement | undefined;
  let effortMenuRef: HTMLDivElement | undefined;
  let searchInputRef: HTMLInputElement | undefined;
  const triggerRef = () => activePicker() === "agent" ? agentTriggerRef : modelTriggerRef;

  const togglePicker = (tab: "agent" | "model") => {
    if (props.disabled) return;
    const wasOpen = open() && activePicker() === tab;
    setSearchQuery("");
    setPreviewModel(undefined);
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
    const width = Math.min(activePicker() === "model" ? 320 : 360, viewportWidth - 32);
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
    if (props.disabled) setOpen(false);
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
    return resolveModelAndProvider(activeProvider, props.model || selectedAgent()?.model || "",
      props.defaultProvider, props.defaultModel, props.catalogs);
  });

  const shortModelLabel = createMemo(() =>
    formatShortModelName(resolvedSelection().provider, resolvedSelection().model)
  );
  const effortLabel = (value: string) => value ? value.charAt(0).toUpperCase() + value.slice(1) : "Auto";
  const selectedEffortLabel = createMemo(() => props.reasoningEffortLevels.length ? effortLabel(props.reasoningEffort) : "");
  const effortTarget = createMemo(() => {
    const preview = previewModel();
    const current = resolvedSelection();
    const provider = preview?.provider || current.provider;
    const id = preview?.id || current.model;
    const option = props.catalogs.find((catalog) => catalog.provider === provider)?.models.find((model) => model.id === id);
    const isSelected = provider === current.provider && id === current.model;
    return {
      provider, id, isSelected,
      levels: isSelected ? props.reasoningEffortLevels : option?.reasoning_effort_levels || [],
      defaultEffort: isSelected ? props.reasoningEffortDefault : option?.reasoning_effort_default,
      effort: isSelected ? props.reasoningEffort : option?.reasoning_effort_default || "",
    };
  });
  const effortOpen = createMemo(() => open() && activePicker() === "model" && !!previewModel() && effortTarget().levels.length > 0);
  const updateEffortPosition = () => {
    if (!effortOpen() || !menuRef) return;
    const target = effortTarget();
    const row = Array.from(menuRef.querySelectorAll<HTMLElement>(".model-item-row"))
      .find((item) => item.dataset.provider === target.provider && item.dataset.modelId === target.id);
    if (!row) return;
    const anchor = row.getBoundingClientRect();
    const parent = menuRef.getBoundingClientRect();
    const width = Math.min(160, window.innerWidth - 32);
    const gap = 6;
    let left = parent.right + gap;
    if (left + width > window.innerWidth - 16) {
      left = parent.left - width - gap;
      if (left < 16) left = window.innerWidth - width - 16;
    }
    const height = effortMenuRef?.offsetHeight || (target.levels.length + 1) * 40 + 40;
    const top = Math.max(8, Math.min(anchor.top, window.innerHeight - height - 8));
    setEffortPos({ top, left, width });
  };
  createEffect(() => {
    if (!effortOpen()) return;
    updateEffortPosition();
    const handler = () => updateEffortPosition();
    const observer = new ResizeObserver(handler);
    if (menuRef) observer.observe(menuRef);
    if (effortMenuRef) observer.observe(effortMenuRef);
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    onCleanup(() => {
      observer.disconnect();
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    });
  });
  const selectModel = (provider: string, id: string) => {
    props.onProviderModelChange(provider, id);
    if (provider === "@pool") props.onReasoningEffortChange("auto");
    setOpen(false);
    modelTriggerRef?.focus();
  };
  const selectEffort = (effort: string) => {
    const target = effortTarget();
    if (!target.isSelected) props.onProviderModelChange(target.provider, target.id);
    // Apply effort after model-change effects have reset the previous override.
    queueMicrotask(() => props.onReasoningEffortChange(effort));
    setOpen(false);
    modelTriggerRef?.focus();
  };
  const focusEffortOption = () => {
    queueMicrotask(() => {
      const selected = effortMenuRef?.querySelector<HTMLButtonElement>(".model-effort-option[aria-checked='true']");
      (selected || effortMenuRef?.querySelector<HTMLButtonElement>(".model-effort-option"))?.focus();
    });
  };

  const filteredModelGroups = createMemo<ModelGroup[]>(() => {
    const q = searchQuery().trim().toLowerCase();
    const matches = (model: PickerModel) => !q ||
      model.id.toLowerCase().includes(q) || (model.label || "").toLowerCase().includes(q) ||
      model.provider.toLowerCase().includes(q);
    const groups: ModelGroup[] = [];

    props.catalogs.forEach((cat) => {
      const matched = cat.models
        .map((model) => ({ ...model, provider: cat.provider }))
        .filter((model) => !isFavorite({ provider: model.provider, model: model.id }) && matches(model));
      if (matched.length > 0) {
        groups.push({
          provider: cat.provider,
          label: cat.provider === "@pool" ? "Model Pools" : cat.provider,
          models: matched,
        });
      }
    });
    groups.sort(
      (left, right) => Number(right.provider === "@pool") - Number(left.provider === "@pool"),
    );
    const favoriteModels = favorites().flatMap((key): PickerModel[] => {
      const favorite = parseFavoriteKey(key);
      if (!favorite) return [];
      const option = props.catalogs.find((catalog) => catalog.provider === favorite.provider)
        ?.models.find((model) => model.id === favorite.model);
      const model = {
        id: favorite.model, label: favorite.model, source: "favorite model", ...option,
        provider: favorite.provider,
      };
      return matches(model) ? [model] : [];
    });
    if (favoriteModels.length) {
      groups.unshift({ provider: "@favorites", label: "Favorites", models: favoriteModels });
    }
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
    if (target && effortMenuRef?.contains(target)) return;
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
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
  });

  onCleanup(() => {
    document.removeEventListener("pointerdown", handlePointerDown);
    document.removeEventListener("keydown", handleKeyDown);
  });

  createEffect(() => {
    // Skip auto-focus on mobile so opening the picker does not pop up the on-screen keyboard.
    if (isMobileViewport()) return;
    if (open() && activePicker() === "model" && searchInputRef) {
      queueMicrotask(() => searchInputRef?.focus());
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
        title={`Model: ${resolvedSelection().provider}/${resolvedSelection().model}${selectedEffortLabel() ? ` · ${selectedEffortLabel()}` : ""}`}
        aria-label={`Select model: ${resolvedSelection().provider}/${resolvedSelection().model}${selectedEffortLabel() ? `, effort: ${selectedEffortLabel()}` : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open() && activePicker() === "model"}
      >
        <span class="agent-model-badge-model">{shortModelLabel()}</span>
        <Show when={selectedEffortLabel()}>
          <span class="agent-model-badge-effort">{selectedEffortLabel()}</span>
        </Show>
        <ChevronDown size={13} class={`agent-model-badge-caret ${open() && activePicker() === "model" ? "is-open" : ""}`} />
      </button>

      <Show when={open()}>
        <Portal>
        <div
          ref={menuRef}
          class={`agent-model-popover agent-model-popover--portal ${activePicker() === "model" ? "agent-model-popover--with-effort" : ""}`}
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
              <span class="agent-model-popover-title">{activePicker() === "agent" ? "Select Agent" : "Model"}</span>
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
                    onInput={(e) => {
                      setSearchQuery(e.currentTarget.value);
                      const group = filteredModelGroups()[0];
                      setPreviewModel(searchQuery().trim() && group ? { provider: group.models[0].provider, id: group.models[0].id } : undefined);
                    }}
                  />
                  <Show when={searchQuery()}>
                    <button
                      class="agent-model-search-clear"
                      type="button"
                      aria-label="Clear model search"
                      onClick={() => {
                        setSearchQuery("");
                        setPreviewModel(undefined);
                        searchInputRef?.focus();
                      }}
                    >
                      <X size={12} />
                    </button>
                  </Show>
                </div>

                <div class="agent-model-list">
                  <For each={filteredModelGroups()}>
                    {(group) => (
                      <div class="model-provider-group">
                        <div class="model-provider-group-title">{group.label}</div>
                        <For each={group.models}>
                          {(modelOption) => {
                            const favorite = { provider: modelOption.provider, model: modelOption.id };
                            const isModelFavorite = () => isFavorite(favorite);
                            const isSelected = () =>
                              resolvedSelection().provider === modelOption.provider &&
                              resolvedSelection().model === modelOption.id;
                            return (
                              <div
                                class={`model-item-row ${isSelected() ? "selected" : ""} ${effortTarget().provider === modelOption.provider && effortTarget().id === modelOption.id ? "is-previewed" : ""}`}
                                role="group"
                                data-provider={modelOption.provider}
                                data-model-id={modelOption.id}
                                aria-label={modelOption.label || modelOption.id}
                                onMouseEnter={() => setPreviewModel({ provider: modelOption.provider, id: modelOption.id })}
                                onClick={() => selectModel(modelOption.provider, modelOption.id)}
                              >
                                <button
                                  type="button"
                                  class="model-item-select"
                                  aria-pressed={isSelected()}
                                  onFocus={() => setPreviewModel({ provider: modelOption.provider, id: modelOption.id })}
                                  title={`${modelOption.provider}/${modelOption.id}${modelOption.context_window ? ` · ${Math.round(modelOption.context_window / 1024)}k context` : ""}`}
                                  onKeyDown={(e) => {
                                    if (e.key === "ArrowRight") {
                                      e.preventDefault();
                                      setPreviewModel({ provider: modelOption.provider, id: modelOption.id });
                                      focusEffortOption();
                                    }
                                  }}
                                >
                                  <div class="model-item-row-info">
                                    <span class="model-item-row-name">
                                      {modelOption.label || modelOption.id}
                                    </span>
                                    <Show when={modelOption.reasoning_effort_levels?.length}>
                                      <span class="model-item-row-effort">
                                        {effortLabel(isSelected() ? props.reasoningEffort : modelOption.reasoning_effort_default || "")}
                                      </span>
                                    </Show>
                                  </div>
                                  <Show when={isSelected()}>
                                    <span class="model-item-row-check">
                                      <Check size={14} />
                                    </span>
                                  </Show>
                                </button>
                                <button
                                  type="button"
                                  class={`model-item-favorite ${isModelFavorite() ? "active" : ""}`}
                                  aria-label={`${isModelFavorite() ? "Remove" : "Add"} favorite model: ${modelOption.provider}/${modelOption.id}`}
                                  aria-pressed={isModelFavorite()}
                                  title={isModelFavorite() ? "Remove favorite model" : "Add favorite model"}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setPreviewModel(undefined);
                                    toggleFavorite(favorite);
                                  }}
                                >
                                  <Star size={14} fill={isModelFavorite() ? "currentColor" : "none"} />
                                </button>
                                <Show when={modelOption.reasoning_effort_levels?.length}>
                                  <button
                                    type="button"
                                    class="model-item-effort-trigger"
                                    aria-label={`Choose effort for ${modelOption.label || modelOption.id}`}
                                    aria-haspopup="menu"
                                    onFocus={() => setPreviewModel({ provider: modelOption.provider, id: modelOption.id })}
                                    aria-expanded={effortOpen() && effortTarget().provider === modelOption.provider && effortTarget().id === modelOption.id}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setPreviewModel({ provider: modelOption.provider, id: modelOption.id });
                                      focusEffortOption();
                                    }}
                                    onKeyDown={(event) => event.stopPropagation()}
                                  ><ChevronRight size={13} /></button>
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
                        selectModel(p, m);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          event.currentTarget.click();
                        }
                      }}
                    >
                      <div class="model-item-row-info">
                        <span class="model-item-row-name">Use "{searchQuery().trim()}"</span>
                        <span class="model-item-row-desc">Custom model identifier</span>
                      </div>
                    </div>
                  </Show>
                  <Show when={filteredModelGroups().length === 0 && !searchQuery().trim()}>
                    <p class="model-effort-unavailable">No models available.</p>
                  </Show>
                </div>
              </div>
            </Show>
          </div>
        </div>
        <Show when={effortOpen()}>
          <div ref={effortMenuRef} class="model-effort-panel" role="group" aria-label="Reasoning effort"
            style={{ top: `${effortPos().top}px`, left: `${effortPos().left}px`, width: `${effortPos().width}px` }}>
            <div class="model-effort-heading">Effort</div>
            <div class="model-effort-options" role="menu" aria-label="Reasoning effort"
              onKeyDown={(event) => {
                const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
                const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
                } else if (event.key === "ArrowLeft") {
                  event.preventDefault();
                  menuRef?.querySelector<HTMLButtonElement>(".model-item-row.is-previewed .model-item-select")?.focus();
                }
              }}>
              <Show when={effortTarget().levels.length} fallback={
                <p class="model-effort-unavailable">No configurable effort for this model.</p>
              }>
                <For each={["", ...effortTarget().levels]}>
                  {(level) => (
                    <button type="button" class="model-effort-option" role="menuitemradio"
                      aria-checked={effortTarget().effort === level}
                      title={!level && effortTarget().defaultEffort ? `Model default: ${effortLabel(effortTarget().defaultEffort!)}` : undefined}
                      onClick={() => selectEffort(level)}>
                      <span>{effortLabel(level)}</span>
                      <Show when={effortTarget().effort === level}><Check size={13} /></Show>
                    </button>
                  )}
                </For>
              </Show>
            </div>
          </div>
        </Show>
        </Portal>
      </Show>
    </div>
  );
}
