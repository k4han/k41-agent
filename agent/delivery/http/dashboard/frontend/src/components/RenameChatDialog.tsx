import { Pencil } from "lucide-solid";
import { createEffect, createSignal, createUniqueId, on, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { patchJson } from "@/lib/api";
import { threadApiPath, type ThreadSummary } from "@/lib/chatThreads";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";

export function RenameChatDialog(props: {
  target: { thread_id: string; title?: string } | null;
  onClose: () => void;
}) {
  const [title, setTitle] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const inputId = createUniqueId();
  const { showToast } = useToast();
  createEffect(on(() => props.target, (target) => {
    setTitle(target?.title || target?.thread_id || "");
    setError("");
  }));
  const close = () => { if (!saving()) props.onClose(); };
  const save = async (event: SubmitEvent) => {
    event.preventDefault();
    const target = props.target;
    const name = title().trim();
    if (!target || !name || saving()) return;
    if (name === (target.title || target.thread_id)) { close(); return; }
    setSaving(true);
    setError("");
    try {
      const updated = await patchJson<ThreadSummary>(threadApiPath(target.thread_id), { title: name });
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, {
        detail: { threadId: target.thread_id, title: updated.title || name },
      }));
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
      showToast("Conversation renamed.", "success");
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed. Please try again.");
    } finally { setSaving(false); }
  };

  return <Dialog open={props.target !== null} title="Rename conversation" icon={<Pencil size={17} />} size="sm" onClose={close}>
    <form class="form-field-group-body" onSubmit={(event) => void save(event)}>
      <div class="form-field">
        <label class="form-field-label" for={inputId}>Conversation name</label>
        <input id={inputId} class="input" value={title()} maxLength={255} required disabled={saving()}
          onInput={(event) => setTitle(event.currentTarget.value)} />
      </div>
      <Show when={error()}><div class="thread-banner thread-banner-error" role="alert">{error()}</div></Show>
      <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "10px" }}>
        <button class="btn" type="button" onClick={close} disabled={saving()}>Cancel</button>
        <button class="btn btn-primary" type="submit" disabled={saving() || !title().trim()}>{saving() ? "Saving..." : "Save"}</button>
      </div>
    </form>
  </Dialog>;
}
