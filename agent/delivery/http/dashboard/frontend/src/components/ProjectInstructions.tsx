import { createEffect, createSignal, on, onCleanup, Show } from "solid-js";
import { Markdown } from "@/components/Markdown";
import { apiFetch, putJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";

type Instructions = { path: string; exists: boolean; content: string; version: string | null };

export function ProjectInstructions(props: { projectId: number }) {
  const [document, setDocument] = createSignal<Instructions | null>(null);
  const [draft, setDraft] = createSignal("");
  const [editing, setEditing] = createSignal(false);
  const [source, setSource] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [saved, setSaved] = createSignal(false);
  let revision = 0;
  const dirty = () => editing() && document() !== null && (!document()!.exists || draft() !== document()!.content);
  useUnsavedChanges(dirty, () => setEditing(false));
  const url = () => `${API_PATHS.projects}/${props.projectId}/instructions`;

  const load = async () => {
    if (dirty() && !window.confirm("Discard unsaved changes and reload AGENTS.md?")) return;
    const version = ++revision;
    setLoading(true);
    setError("");
    setSaved(false);
    try {
      const result = await apiFetch<Instructions>(url());
      if (version !== revision) return;
      setDocument(result);
      setDraft(result.content);
      setEditing(false);
    } catch (error) {
      if (version === revision) setError(error instanceof Error ? error.message : "Failed to read AGENTS.md.");
    } finally { if (version === revision) setLoading(false); }
  };
  const save = async () => {
    if (!document() || saving()) return;
    const version = revision;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const result = await putJson<Instructions>(url(), { content: draft(), expected_version: document()!.version });
      if (version !== revision) return;
      setDocument(result);
      setDraft(result.content);
      setEditing(false);
      setSaved(true);
    } catch (error) {
      if (version === revision) setError(error instanceof Error ? error.message : "Failed to save AGENTS.md.");
    } finally { if (version === revision) setSaving(false); }
  };
  createEffect(on(() => props.projectId, () => { void load(); }));
  onCleanup(() => { revision += 1; });

  return <section class="panel project-resource-panel" aria-label="Project instructions" aria-busy={loading() || saving()}>
    <div class="project-resource-toolbar">
      <h2>AGENTS.md</h2>
      <div class="row-wrap">
        <Show when={document()?.exists && !editing()}>
          <button class="btn" aria-pressed={source()} onClick={() => setSource(!source())}>{source() ? "Preview" : "Source"}</button>
        </Show>
        <button class="btn" disabled={loading() || saving()} onClick={() => void load()}>Reload instructions</button>
        <Show when={document() && !editing()}>
          <button class="btn btn-primary" disabled={loading()} onClick={() => { setEditing(true); setSaved(false); }}>
            {document()!.exists ? "Edit instructions" : "Create AGENTS.md"}
          </button>
        </Show>
      </div>
    </div>
    <Show when={error()}><div class="project-error" role="alert">{error()}<Show when={editing()}> Your draft is kept while this editor remains open.</Show> Use Reload instructions to read the current file.</div></Show>
    <Show when={loading()}><p role="status">Loading instructions...</p></Show>
    <Show when={saved()}><p role="status">Instructions saved.</p></Show>
    <Show when={document()}>{(current) => <Show when={editing()} fallback={
      <Show when={current().exists} fallback={<p class="empty compact">This project has no AGENTS.md in its root directory.</p>}>
        <Show when={current().content} fallback={<p class="empty compact">AGENTS.md is empty.</p>}>
          <Show when={source()} fallback={<Markdown text={current().content} class="message-markdown" />}>
            <pre class="project-resource-source">{current().content}</pre>
          </Show>
        </Show>
      </Show>
    }>
      <label class="field project-instructions-field">Instructions
        <textarea class="input project-instructions-editor" aria-label="AGENTS.md content" spellcheck={false}
          value={draft()} disabled={saving() || loading()} onInput={(event) => setDraft(event.currentTarget.value)} />
      </label>
      <div class="row-wrap">
        <button class="btn btn-primary" disabled={saving() || loading() || !dirty()} onClick={() => void save()}>{saving() ? "Saving..." : "Save instructions"}</button>
        <button class="btn" disabled={saving() || loading()} onClick={() => { setDraft(current().content); setEditing(false); setError(""); }}>Cancel editing</button>
      </div>
    </Show>}</Show>
  </section>;
}
