import { A } from "@solidjs/router";
import { Search } from "lucide-solid";
import { createMemo, createSignal, createUniqueId, For, on, createEffect, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { ErrorPanel, LoadingPanel } from "@/components/State";
import {
  bindingLabels,
  isTypingKeyEvent,
  SHORTCUT_ACTIONS,
  SHORTCUT_LABELS,
  SHORTCUT_SETTINGS_PATH,
  type PrimaryModifier,
  type ShortcutOverrides,
} from "@/lib/keyboardShortcuts";

export function ShortcutKeys(props: { binding: string; primary: PrimaryModifier }) {
  const keys = createMemo(() => bindingLabels(props.binding, props.primary));
  return (
    <span class="shortcut-keys">
      <span class="sr-only">{keys().join("+")}</span>
      <For each={keys()}>{(key) => <kbd aria-hidden="true">{key}</kbd>}</For>
    </span>
  );
}

export type PaletteItem = {
  label: string;
  binding?: string;
  run: () => void;
};

export function CommandPalette(props: {
  open: boolean;
  items: PaletteItem[];
  primary: PrimaryModifier;
  onClose: () => void;
  onSelect: (item: PaletteItem) => void;
}) {
  const [query, setQuery] = createSignal("");
  const [index, setIndex] = createSignal(0);
  const listId = createUniqueId();
  const optionId = (itemIndex: number) => `${listId}-${itemIndex}`;
  const matches = createMemo(() => {
    const term = query().trim().toLowerCase();
    return props.items.filter((item) => item.label.toLowerCase().includes(term));
  });
  const activeIndex = () => Math.min(index(), matches().length - 1);

  createEffect(on(() => props.open, (open) => {
    if (open) {
      setQuery("");
      setIndex(0);
    }
  }));

  const onKeyDown = (event: KeyboardEvent) => {
    if (isTypingKeyEvent(event)) {
      if (event.key === "Escape") event.stopPropagation();
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !matches().length) return;

    if (event.key === "Enter") {
      event.preventDefault();
      props.onSelect(matches()[activeIndex()]);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = (activeIndex() + (event.key === "ArrowDown" ? 1 : -1) + matches().length) % matches().length;
      setIndex(next);
      document.getElementById(optionId(next))?.scrollIntoView({ block: "nearest" });
    }
  };

  return (
    <Dialog open={props.open} title="Command palette" class="shortcut-palette-dialog" bodyClass="shortcut-palette" onClose={props.onClose}>
      <div class="shortcut-palette-search">
        <Search size={15} class="shortcut-palette-search-icon" aria-hidden="true" />
        <input
          class="input shortcut-palette-input"
          type="search"
          role="combobox"
          aria-label="Search commands or pages"
          aria-autocomplete="list"
          aria-controls={listId}
          aria-expanded="true"
          aria-activedescendant={activeIndex() >= 0 ? optionId(activeIndex()) : undefined}
          placeholder="Search commands or pages"
          autocomplete="off"
          autofocus
          value={query()}
          onInput={(event) => {
            setQuery(event.currentTarget.value);
            setIndex(0);
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      <ul id={listId} class="shortcut-palette-list" role="listbox" aria-label="Commands and pages">
        <For each={matches()}>
          {(item, itemIndex) => (
            <li
              id={optionId(itemIndex())}
              role="option"
              aria-selected={activeIndex() === itemIndex()}
              onPointerMove={() => setIndex(itemIndex())}
              onClick={() => props.onSelect(item)}
            >
              <span class="shortcut-palette-label">{item.label}</span>
              <Show when={item.binding}>{(binding) => <ShortcutKeys binding={binding()} primary={props.primary} />}</Show>
            </li>
          )}
        </For>
      </ul>
      <Show when={!matches().length}>
        <p class="muted shortcut-palette-empty" role="status">No matching commands.</p>
      </Show>
      <div class="shortcut-palette-footer" aria-hidden="true">
        <span class="shortcut-palette-hint"><kbd>↑</kbd><kbd>↓</kbd> Navigate</span>
        <span class="shortcut-palette-hint"><kbd>↵</kbd> Select</span>
        <span class="shortcut-palette-hint"><kbd>esc</kbd> Close</span>
      </div>
    </Dialog>
  );
}

export function ShortcutHelpDialog(props: {
  open: boolean;
  ready: boolean;
  bindings: ShortcutOverrides;
  primary: PrimaryModifier;
  error: string;
  onRetry: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={props.open}
      title="Keyboard shortcuts"
      onClose={props.onClose}
      footer={<A href={SHORTCUT_SETTINGS_PATH} class="btn" onClick={props.onClose}>Customize shortcuts</A>}
    >
      <Show when={props.ready} fallback={
        <Show when={props.error} fallback={<LoadingPanel />}>
          <ErrorPanel message={props.error} onRetry={props.onRetry} />
        </Show>
      }>
        <ul class="shortcut-list">
          <For each={SHORTCUT_ACTIONS}>
            {(action) => (
              <li class="shortcut-row">
                <span>{SHORTCUT_LABELS[action]}</span>
                <div class="shortcut-bindings">
                  <For each={props.bindings[action]} fallback={<span class="muted">Unassigned</span>}>
                    {(binding) => <ShortcutKeys binding={binding} primary={props.primary} />}
                  </For>
                </div>
              </li>
            )}
          </For>
        </ul>
      </Show>
    </Dialog>
  );
}
