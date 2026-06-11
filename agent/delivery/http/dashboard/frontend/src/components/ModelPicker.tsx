import { Star } from "lucide-solid";
import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";

import { classNames } from "@/lib/utils";
import type { ModelCatalog, ModelOption } from "@/types";

const favoritesStorageKey = "k41.dashboard.modelFavorites";
const keySeparator = "\u001f";

type ModelFavorite = {
  provider: string;
  model: string;
};

type ModelChoice = {
  provider: string;
  model: string;
  label: string;
  description: string;
  key: string;
  custom?: boolean;
};

type ModelGroup = {
  provider: string;
  choices: ModelChoice[];
};

type ModelPickerProps = {
  catalogs: ModelCatalog[];
  providerNames: string[];
  defaultProvider: string;
  defaultModel: string;
  provider: string;
  model: string;
  disabled?: boolean;
  dropdownPlacement?: "top" | "bottom";
  onChange: (provider: string, model: string) => void;
  resolveDefault?: boolean;
  modelFilter?: (model: ModelOption, provider: string) => boolean;
};

function favoriteKey(provider: string, model: string): string {
  return `${provider}${keySeparator}${model}`;
}

function modelLabel(model: string): string {
  return model || "provider default";
}

function selectionLabel(provider: string, model: string, resolveDefault?: boolean, defaultModel?: string): string {
  if (resolveDefault && (!model || model === "provider default")) {
    return `${provider}/${defaultModel || "default model"}`;
  }
  return `${provider || "default"}/${modelLabel(model)}`;
}

function resolveModelAndProvider(
  provider: string,
  model: string,
  defaultProvider: string,
  defaultModel: string,
  catalogs: ModelCatalog[]
): { provider: string; model: string } {
  let resolvedProvider = provider;
  if (!provider || provider === "default") {
    resolvedProvider = defaultProvider || "";
  }

  let resolvedModel = model;
  if (!model || model === "provider default") {
    if (!provider || provider === "default") {
      resolvedModel = defaultModel || "";
    } else {
      const catalog = catalogs.find((item) => item.provider === resolvedProvider);
      resolvedModel = catalog?.default_model || "";
    }
  }

  return { provider: resolvedProvider, model: resolvedModel };
}

function readFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(favoritesStorageKey);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as ModelFavorite[];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((item) => item && item.provider && item.model)
      .map((item) => favoriteKey(item.provider, item.model));
  } catch {
    return [];
  }
}

function writeFavorites(keys: string[]): void {
  const favorites = keys
    .map((key) => {
      const [provider, model] = key.split(keySeparator);
      return provider && model ? { provider, model } : null;
    })
    .filter(Boolean) as ModelFavorite[];
  try {
    window.localStorage.setItem(favoritesStorageKey, JSON.stringify(favorites));
  } catch {
    return;
  }
}

function orderedProviders(
  providerNames: string[],
  catalogs: ModelCatalog[],
  defaultProvider: string,
  selectedProvider: string,
  resolveDefault?: boolean,
): string[] {
  const providers = new Set<string>();
  if (!resolveDefault) {
    providers.add("default");
  }
  if (defaultProvider) {
    providers.add(defaultProvider);
  }
  providerNames.forEach((provider) => providers.add(provider));
  catalogs.forEach((catalog) => providers.add(catalog.provider));
  if (selectedProvider && selectedProvider !== "default") {
    providers.add(selectedProvider);
  }
  return Array.from(providers).sort((left, right) => {
    if (left === "default") {
      return -1;
    }
    if (right === "default") {
      return 1;
    }
    return left.localeCompare(right);
  });
}

function parseModelInput(value: string, fallbackProvider: string): Pick<ModelChoice, "provider" | "model"> {
  const trimmed = value.trim();
  if (!trimmed) {
    return { provider: fallbackProvider || "default", model: "" };
  }
  const slashIndex = trimmed.indexOf("/");
  if (slashIndex === -1) {
    return { provider: fallbackProvider || "default", model: trimmed };
  }
  const provider = trimmed.slice(0, slashIndex).trim() || "default";
  const model = trimmed.slice(slashIndex + 1).trim();
  return { provider, model: model === "provider default" ? "" : model };
}

export function ModelPicker(props: ModelPickerProps) {
  const [open, setOpen] = createSignal(false);
  const [query, setQuery] = createSignal("");
  const [favorites, setFavorites] = createSignal<string[]>([]);
  let rootRef: HTMLDivElement | undefined;
  let inputRef: HTMLInputElement | undefined;

  const selectedProvider = createMemo(() => {
    if (props.resolveDefault) {
      const { provider } = resolveModelAndProvider(props.provider, props.model, props.defaultProvider, props.defaultModel, props.catalogs);
      return provider;
    }
    return props.provider || "default";
  });

  const selectedModel = createMemo(() => {
    if (props.resolveDefault) {
      const { model } = resolveModelAndProvider(props.provider, props.model, props.defaultProvider, props.defaultModel, props.catalogs);
      return model;
    }
    return props.model || "";
  });

  const selectedLabel = createMemo(() => {
    if (props.resolveDefault) {
      const { provider, model } = resolveModelAndProvider(props.provider, props.model, props.defaultProvider, props.defaultModel, props.catalogs);
      return `${provider}/${model || "default model"}`;
    }
    return selectionLabel(selectedProvider(), props.model || "");
  });

  const selectedFavorite = createMemo(() =>
    selectedModel() ? favorites().includes(favoriteKey(selectedProvider(), selectedModel())) : false,
  );

  const isFavorite = (choice: Pick<ModelChoice, "provider" | "model">) =>
    choice.model ? favorites().includes(favoriteKey(choice.provider, choice.model)) : false;

  const toggleFavorite = (choice: Pick<ModelChoice, "provider" | "model">) => {
    if (!choice.model) {
      return;
    }
    const key = favoriteKey(choice.provider, choice.model);
    setFavorites((current) => {
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key].sort();
      writeFavorites(next);
      return next;
    });
  };

  const baseGroups = createMemo<ModelGroup[]>(() => {
    const providers = orderedProviders(
      props.providerNames,
      props.catalogs,
      props.defaultProvider,
      selectedProvider(),
      props.resolveDefault,
    );
    return providers
      .map((provider) => {
        const catalogProvider = provider === "default" ? props.defaultProvider : provider;
        const catalog = props.catalogs.find((item) => item.provider === catalogProvider);
        const models = new Set<string>();
        if (provider !== "default") {
          catalog?.models
            .filter((model) => !props.modelFilter || props.modelFilter(model, catalogProvider))
            .forEach((model) => models.add(model.id));
          const defaultOption = catalog?.models.find((model) => model.id === catalog.default_model);
          if (
            catalog?.default_model &&
            defaultOption &&
            (!props.modelFilter || props.modelFilter(defaultOption, catalogProvider))
          ) {
            models.add(catalog.default_model);
          }
          if (provider === selectedProvider() && selectedModel()) {
            const selectedOption = catalog?.models.find((model) => model.id === selectedModel());
            if (
              selectedOption &&
              (!props.modelFilter || props.modelFilter(selectedOption, catalogProvider))
            ) {
              models.add(selectedModel());
            }
          }
        }
        const defaultModelName = catalog?.default_model || "";
        const choices: ModelChoice[] = [];
        if (provider === "default") {
          choices.push({
            provider,
            model: "",
            label: selectionLabel(provider, "", props.resolveDefault, props.defaultModel),
            description: props.defaultProvider
              ? `uses ${props.defaultProvider}/${props.defaultModel}`
              : "system default model",
            key: favoriteKey(provider, ""),
          });
        }
        choices.push(
          ...Array.from(models)
            .sort((left, right) => {
              const leftFavorite = favorites().includes(favoriteKey(provider, left));
              const rightFavorite = favorites().includes(favoriteKey(provider, right));
              if (leftFavorite !== rightFavorite) {
                return leftFavorite ? -1 : 1;
              }
              return left.localeCompare(right);
            })
            .map((model) => ({
              provider,
              model,
              label: selectionLabel(provider, model, props.resolveDefault, defaultModelName),
              description: catalog?.models.find((item) => item.id === model)?.source || "configured model",
              key: favoriteKey(provider, model),
            })),
        );
        return { provider, choices };
      })
      .filter((group) => group.choices.length > 0);
  });

  const visibleGroups = createMemo<ModelGroup[]>(() => {
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return baseGroups();
    }

    const filtered = baseGroups()
      .map((group) => ({
        provider: group.provider,
        choices: group.choices.filter((choice) =>
          [choice.label, choice.provider, choice.model, choice.description]
            .join(" ")
            .toLowerCase()
            .includes(needle),
        ),
      }))
      .filter((group) => group.choices.length > 0);

    const parsed = parseModelInput(query(), selectedProvider());
    const customLabel = selectionLabel(parsed.provider, parsed.model);
    const exists = baseGroups().some((group) =>
      group.choices.some((choice) => choice.provider === parsed.provider && choice.model === parsed.model),
    );
    if (parsed.model && !exists) {
      const customChoice: ModelChoice = {
        provider: parsed.provider,
        model: parsed.model,
        label: customLabel,
        description: "custom model",
        key: favoriteKey(parsed.provider, parsed.model),
        custom: true,
      };
      const targetGroup = filtered.find((group) => group.provider === parsed.provider);
      if (targetGroup) {
        targetGroup.choices = [customChoice, ...targetGroup.choices];
      } else {
        filtered.unshift({ provider: parsed.provider, choices: [customChoice] });
      }
    }

    return filtered;
  });

  const selectChoice = (choice: Pick<ModelChoice, "provider" | "model">) => {
    props.onChange(choice.provider, choice.model);
    setOpen(false);
    setQuery("");
    inputRef?.blur();
  };

  const commitQuery = () => {
    const value = query().trim();
    if (!value) {
      return;
    }
    const exact = baseGroups()
      .flatMap((group) => group.choices)
      .find((choice) => choice.label.toLowerCase() === value.toLowerCase());
    selectChoice(exact || parseModelInput(value, selectedProvider()));
  };

  onMount(() => {
    setFavorites(readFavorites());
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef && !rootRef.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    const handleStorage = (event: StorageEvent) => {
      if (event.key === favoritesStorageKey) {
        setFavorites(readFavorites());
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("storage", handleStorage);
    onCleanup(() => {
      document.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("storage", handleStorage);
    });
  });

  return (
    <div
      class={classNames(
        "model-picker",
        props.dropdownPlacement === "top" && "model-picker-up",
      )}
      ref={rootRef}
    >
      <div class="model-picker-control">
        <input
          class="input model-picker-input"
          ref={inputRef}
          value={open() ? query() : selectedLabel()}
          disabled={props.disabled}
          placeholder="provider/model"
          autocomplete="off"
          onFocus={() => {
            setOpen(true);
            setQuery("");
          }}
          onInput={(event) => {
            setQuery(event.currentTarget.value);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitQuery();
            }
            if (event.key === "Escape") {
              setOpen(false);
              setQuery("");
            }
          }}
        />
        <button
          class={classNames("model-picker-star", selectedFavorite() && "active")}
          type="button"
          disabled={props.disabled || !selectedModel()}
          title={selectedFavorite() ? "Remove favorite model" : "Add favorite model"}
          aria-pressed={selectedFavorite()}
          onClick={() => toggleFavorite({ provider: selectedProvider(), model: selectedModel() })}
        >
          <Star size={15} fill={selectedFavorite() ? "currentColor" : "none"} />
        </button>
      </div>

      <Show when={open() && !props.disabled}>
        <div class="model-picker-dropdown">
          <For each={visibleGroups()} fallback={<div class="model-picker-empty">No models found.</div>}>
            {(group) => (
              <div class="model-picker-group">
                <div class="model-picker-group-title">{group.provider}</div>
                <For each={group.choices}>
                  {(choice) => (
                    <div
                      class={classNames(
                        "model-picker-option",
                        choice.provider === selectedProvider() && choice.model === selectedModel() && "active",
                      )}
                    >
                      <button
                        class="model-picker-option-main"
                        type="button"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => selectChoice(choice)}
                      >
                        <span class="mono">{choice.label}</span>
                      </button>
                      <button
                        class={classNames("model-picker-option-star", isFavorite(choice) && "active")}
                        type="button"
                        disabled={!choice.model}
                        title={isFavorite(choice) ? "Remove favorite model" : "Add favorite model"}
                        aria-pressed={isFavorite(choice)}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleFavorite(choice);
                        }}
                      >
                        <Star size={14} fill={isFavorite(choice) ? "currentColor" : "none"} />
                      </button>
                    </div>
                  )}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
