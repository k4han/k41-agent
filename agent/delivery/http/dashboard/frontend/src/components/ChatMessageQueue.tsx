import { ArrowUp, Ellipsis, Pencil, Trash2 } from "lucide-solid";
import { createEffect, createMemo, createSignal, createUniqueId, For, onCleanup, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { computeFloatingPosition, floatingMenuStyle } from "@/lib/floating";
import type { QueuedMessage } from "@/lib/useChatMessageQueue";

export interface ChatMessageQueueProps {
  messages: QueuedMessage[];
  paused: boolean;
  canSendNow: boolean;
  sendLabel: string;
  onSendNow: (message: QueuedMessage) => void;
  onRemove: (message: QueuedMessage) => void;
  onEdit: (message: QueuedMessage, text: string) => Promise<boolean>;
}

function QueuedMessageRow(props: Omit<ChatMessageQueueProps, "messages" | "paused"> & { message: QueuedMessage }) {
  const [editingRevision, setEditingRevision] = createSignal<number>();
  const [text, setText] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [menuStyle, setMenuStyle] = createSignal<Record<string, string>>({});
  const menuId = createUniqueId();
  let trigger: HTMLButtonElement | undefined;
  let editButton: HTMLButtonElement | undefined;

  const disabled = () => Boolean(props.message.saving);
  const sendLabel = () => {
    if (props.message.local && props.message.error) return "Retry queued message";
    if (props.message.priority && props.sendLabel === "Insert before the next model call") return "Marked for insertion";
    return props.sendLabel;
  };
  const canSave = () => Boolean(text().trim() || props.message.attachments.length);
  const save = async () => {
    const revision = editingRevision();
    if (revision === undefined || saving() || !canSave() || disabled()) return;
    setSaving(true);
    try {
      if (await props.onEdit({ ...props.message, revision }, text().trim())) setEditingRevision(undefined);
    } finally {
      setSaving(false);
    }
  };

  const positionMenu = () => {
    if (trigger) setMenuStyle(floatingMenuStyle(computeFloatingPosition(trigger, { align: "right", minWidth: 116 })));
  };

  createEffect(() => {
    if (!menuOpen()) return;
    const dismiss = (event: PointerEvent) => {
      if (!trigger?.contains(event.target as Node) && !editButton?.contains(event.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
    document.addEventListener("pointerdown", dismiss);
    onCleanup(() => {
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
      document.removeEventListener("pointerdown", dismiss);
    });
  });

  return (
    <li class="chat-queue-row">
      <Show when={editingRevision() !== undefined} fallback={
        <>
          <div class="chat-queue-content">
            <Show when={props.message.text}>
              <span class="chat-queue-text" title={props.message.text}>{props.message.text}</span>
            </Show>
            <Show when={props.message.attachments.length}>
              <span class="chat-queue-files" title={props.message.attachments.map((file) => file.name).join(", ")}>
                {props.message.attachments.map((file) => file.name).join(", ")}
              </span>
            </Show>
            <Show when={props.message.error}>
              <span class="chat-queue-error" title={props.message.error}>Message could not be queued</span>
            </Show>
          </div>
          <div class="chat-queue-actions">
            <button
              type="button" class="chat-queue-action" classList={{ "is-priority": props.message.priority }}
              title={sendLabel()} aria-label={sendLabel()}
              disabled={disabled() || !props.canSendNow || (props.message.local && !props.message.error) || sendLabel() === "Marked for insertion"}
              onClick={() => props.onSendNow(props.message)}
            ><ArrowUp size={16} /></button>
            <button
              type="button" class="chat-queue-action chat-queue-delete" title="Remove queued message" aria-label="Remove queued message"
              disabled={disabled()} onClick={() => props.onRemove(props.message)}
            ><Trash2 size={15} /></button>
            <button
              type="button" ref={trigger} class="chat-queue-action" title="Queued message actions" aria-label="Queued message actions"
              aria-haspopup="menu" aria-expanded={menuOpen()} aria-controls={menuOpen() ? menuId : undefined}
              disabled={disabled()}
              onClick={() => {
                if (menuOpen()) setMenuOpen(false);
                else {
                  positionMenu();
                  setMenuOpen(true);
                }
              }}
            ><Ellipsis size={17} /></button>
          </div>
          <Show when={menuOpen()}>
            <Portal>
              <div
                id={menuId} class="chat-actions-menu" style={menuStyle()} role="menu" aria-label="Queued message actions"
                onKeyDown={(event) => {
                  if (event.key !== "Escape" && event.key !== "Tab") return;
                  setMenuOpen(false);
                  trigger?.focus();
                }}
              >
                <button type="button" role="menuitem" ref={(button) => {
                  editButton = button;
                  button.focus();
                }} onClick={() => {
                  setText(props.message.text);
                  setMenuOpen(false);
                  setEditingRevision(props.message.revision);
                }}><Pencil size={15} />Edit</button>
              </div>
            </Portal>
          </Show>
        </>
      }>
        <form class="chat-queue-editor" onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}>
          <textarea
            value={text()} rows={2} aria-label="Edit queued message"
            ref={(input) => queueMicrotask(() => input.focus())}
            onInput={(event) => setText(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.isComposing || event.keyCode === 229) return;
              if (event.key === "Escape") setEditingRevision(undefined);
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void save();
              }
            }}
          />
          <div class="chat-queue-editor-actions">
            <button type="button" onClick={() => setEditingRevision(undefined)}>Cancel</button>
            <button type="submit" disabled={saving() || disabled() || !canSave()}>Save</button>
          </div>
        </form>
      </Show>
    </li>
  );
}

export function ChatMessageQueue(props: ChatMessageQueueProps) {
  const messagesById = createMemo(() => new Map(props.messages.map((message) => [message.id, message])));
  return (
    <Show when={props.messages.length}>
      <div class="chat-message-queue">
        <Show when={props.paused}><span class="chat-queue-status" role="status">Queue paused</span></Show>
        <ol aria-label="Queued messages" class="chat-queue-list">
          <For each={props.messages.map((message) => message.id)}>
            {(id) => <QueuedMessageRow
              message={messagesById().get(id)!} canSendNow={props.canSendNow} sendLabel={props.sendLabel}
              onEdit={props.onEdit} onRemove={props.onRemove} onSendNow={props.onSendNow}
            />}
          </For>
        </ol>
      </div>
    </Show>
  );
}
