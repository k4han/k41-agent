import { Plus, RotateCcw, X } from "lucide-solid";
import { createMemo, createSignal, For, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useKeyboardShortcuts } from "@/components/KeyboardShortcutsProvider";
import { ShortcutKeys } from "@/components/ShortcutDialogs";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import {
  bindingFromEvent, bindingLabels, findBindingConflict, isTypingKeyEvent,
  SHORTCUT_ACTIONS, SHORTCUT_LABELS, type ShortcutAction,
} from "@/lib/keyboardShortcuts";
import { SettingsLayout } from "./SettingsLayout";

type ShortcutEdit = { action: ShortcutAction; original?: string; revision: number };

export function KeyboardShortcutsPage() {
  const shortcuts = useKeyboardShortcuts();
  const { showToast } = useToast();
  const [editor, setEditor] = createSignal<ShortcutEdit>();
  const [candidate, setCandidate] = createSignal("");
  const conflict = createMemo(() => {
    const edit = editor();
    return edit && candidate()
      ? findBindingConflict(shortcuts.bindings(), edit.action, candidate(), edit.original)
      : undefined;
  });
  const format = (binding: string) => bindingLabels(binding, shortcuts.primary).join("+");
  const allowsBareSlash = () => editor()?.action === "focus-message-input";

  const editBinding = (action: ShortcutAction, original?: string) => {
    const settings = shortcuts.settings();
    if (!settings || shortcuts.saving()) return;
    setCandidate(original ?? "");
    setEditor({ action, original, revision: settings.revision });
  };
  const closeEditor = () => {
    if (!shortcuts.saving()) setEditor(undefined);
  };
  const recordBinding = (event: KeyboardEvent) => {
    if (isTypingKeyEvent(event)) {
      if (event.key === "Escape") event.stopPropagation();
      return;
    }
    const binding = bindingFromEvent(event, shortcuts.primary, allowsBareSlash());
    if (binding && !shortcuts.saving()) {
      event.preventDefault();
      setCandidate(binding);
    }
  };
  const saveEdit = async () => {
    const edit = editor();
    const binding = candidate();
    const settings = shortcuts.settings();
    if (!edit || !settings || !binding || binding === edit.original || conflict()) return;
    const previous = shortcuts.bindings()[edit.action] ?? [];
    const next = edit.original
      ? previous.map((item) => item === edit.original ? binding : item)
      : [...previous, binding];
    const result = await shortcuts.save({ ...settings.overrides, [edit.action]: next }, edit.revision);
    if (result !== "failed" && editor() === edit) closeEditor();
  };
  const removeBinding = (action: ShortcutAction, binding: string) => {
    const settings = shortcuts.settings();
    if (!settings) return;
    const next = shortcuts.bindings()[action]?.filter((item) => item !== binding) ?? [];
    void shortcuts.save({ ...settings.overrides, [action]: next }, settings.revision);
  };
  const resetAction = (action: ShortcutAction) => {
    const settings = shortcuts.settings();
    if (!settings) return;
    const defaults = settings.defaults[action];
    const others = { ...shortcuts.bindings(), [action]: [] };
    for (const binding of defaults) {
      const assigned = findBindingConflict(others, action, binding);
      if (assigned) {
        showToast(`${format(binding)} is assigned to ${SHORTCUT_LABELS[assigned]}.`, "error");
        return;
      }
    }
    const overrides = { ...settings.overrides };
    delete overrides[action];
    void shortcuts.save(overrides, settings.revision);
  };
  const resetAll = () => {
    const settings = shortcuts.settings();
    if (settings) void shortcuts.save({}, settings.revision);
  };

  return (
    <SettingsLayout
      title="Keyboard Shortcuts"
      description="Customize key combinations for this account."
      contentWidth="narrow"
      actions={
        <button
          class="btn"
          type="button"
          disabled={shortcuts.saving() || !Object.keys(shortcuts.settings()?.overrides ?? {}).length}
          onClick={resetAll}
        >
          <RotateCcw size={14} aria-hidden="true" />
          Reset all
        </button>
      }
    >
      <DataGate data={shortcuts.settings()} error={shortcuts.loadError()} onRetry={() => void shortcuts.refresh()}>
        {(settings) => (
          <ul class="panel shortcut-list" aria-label="Keyboard shortcuts" aria-busy={shortcuts.saving()}>
            <For each={SHORTCUT_ACTIONS}>
              {(action) => (
                <li class="shortcut-row">
                  <span class="shortcut-label">{SHORTCUT_LABELS[action]}</span>
                  <div class="shortcut-row-controls">
                    <div class="shortcut-bindings">
                      <For each={shortcuts.bindings()[action]} fallback={<span class="muted">Unassigned</span>}>
                        {(binding) => (
                          <span class="shortcut-assignment">
                            <button
                              class="btn btn-ghost shortcut-edit"
                              type="button"
                              aria-label={`Edit ${SHORTCUT_LABELS[action]}: ${format(binding)}`}
                              aria-haspopup="dialog"
                              title="Edit shortcut"
                              disabled={shortcuts.saving()}
                              onClick={() => editBinding(action, binding)}
                            >
                              <ShortcutKeys binding={binding} primary={shortcuts.primary} />
                            </button>
                            <button
                              class="btn btn-ghost btn-icon shortcut-remove"
                              type="button"
                              aria-label={`Remove ${SHORTCUT_LABELS[action]}: ${format(binding)}`}
                              title="Remove shortcut"
                              disabled={shortcuts.saving()}
                              onClick={() => removeBinding(action, binding)}
                            >
                              <X size={13} aria-hidden="true" />
                            </button>
                          </span>
                        )}
                      </For>
                    </div>
                    <div class="shortcut-row-actions">
                      <button
                        class="btn btn-ghost btn-icon"
                        type="button"
                        aria-label={`Add shortcut for ${SHORTCUT_LABELS[action]}`}
                        aria-haspopup="dialog"
                        title="Add shortcut"
                        disabled={shortcuts.saving()}
                        onClick={() => editBinding(action)}
                      >
                        <Plus size={16} aria-hidden="true" />
                      </button>
                      <Show when={settings().overrides[action] !== undefined}>
                        <button
                          class="btn btn-ghost btn-icon"
                          type="button"
                          aria-label={`Reset ${SHORTCUT_LABELS[action]} to default`}
                          title="Reset to default"
                          disabled={shortcuts.saving()}
                          onClick={() => resetAction(action)}
                        >
                          <RotateCcw size={14} aria-hidden="true" />
                        </button>
                      </Show>
                    </div>
                  </div>
                </li>
              )}
            </For>
          </ul>
        )}
      </DataGate>
      <Dialog
        open={Boolean(editor())}
        title={editor()?.original ? "Edit shortcut" : "Add shortcut"}
        subtitle={editor() && SHORTCUT_LABELS[editor()!.action]}
        size="sm"
        closeOnEscape={!shortcuts.saving()}
        closeOnBackdrop={!shortcuts.saving()}
        onClose={closeEditor}
        footer={
          <div class="row-wrap shortcut-editor-actions">
            <button class="btn" type="button" disabled={shortcuts.saving()} onClick={closeEditor}>Cancel</button>
            <button
              class="btn btn-primary"
              type="button"
              disabled={!candidate() || candidate() === editor()?.original || Boolean(conflict()) || shortcuts.saving()}
              onClick={() => void saveEdit()}
            >{shortcuts.saving() ? "Saving..." : "Save"}</button>
          </div>
        }
      >
        <button
          class="input shortcut-recorder"
          type="button"
          aria-label="Record key combination"
          aria-describedby="shortcut-recorder-hint"
          autofocus
          onClick={(event) => event.currentTarget.focus()}
          onKeyDown={recordBinding}
        >
          <Show when={candidate()} fallback={<span class="muted">Press a key combination</span>}>
            {(binding) => <ShortcutKeys binding={binding()} primary={shortcuts.primary} />}
          </Show>
        </button>
        <p class="hint" id="shortcut-recorder-hint">
          {allowsBareSlash()
            ? "Press / or hold Ctrl, Cmd, Alt, or Shift and press a key."
            : "Hold Ctrl, Cmd, Alt, or Shift and press a key."}
        </p>
        <Show when={conflict()}>
          {(assigned) => <p class="shortcut-conflict" role="alert">Already assigned to {SHORTCUT_LABELS[assigned()]}.</p>}
        </Show>
      </Dialog>
    </SettingsLayout>
  );
}
