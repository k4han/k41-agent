import { createEffect, createMemo, createSignal, For, on, onCleanup, Show } from "solid-js";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";

type ProjectSecret = { name: string; enabled: boolean; created_at: string; updated_at: string };

export function ProjectSecrets(props: { projectId: number }) {
  const [secrets, setSecrets] = createSignal<ProjectSecret[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const [formOpen, setFormOpen] = createSignal(false);
  const [editing, setEditing] = createSignal<string | null>(null);
  const [name, setName] = createSignal("");
  const [value, setValue] = createSignal("");
  let generation = 0;
  let loadRevision = 0;
  const projectId = createMemo(() => props.projectId);
  const url = () => `${API_PATHS.projects}/${projectId()}/secrets`;
  const closeForm = () => { setValue(""); setName(""); setEditing(null); setFormOpen(false); };
  const dirty = () => formOpen() && (value() !== "" || (editing() === null && name() !== ""));
  useUnsavedChanges(dirty, closeForm);

  const load = async () => {
    const current = generation;
    const revision = ++loadRevision;
    setLoading(true); setError("");
    try {
      const result = await apiFetch<{ secrets: ProjectSecret[] }>(url(), { cache: "no-store" });
      if (current === generation && revision === loadRevision) setSecrets(result.secrets);
    } catch (error) {
      if (current === generation && revision === loadRevision) setError(error instanceof Error ? error.message : "Failed to load project secrets.");
    } finally {
      if (current === generation && revision === loadRevision) setLoading(false);
    }
  };
  const mutate = async (method: string, suffix = "", body?: Record<string, unknown>, close = false) => {
    if (saving()) return;
    const current = generation;
    setSaving(true); setError(""); setNotice("");
    try {
      await apiFetch(url() + suffix, { method, json: body });
      if (current !== generation) return;
      if (close) closeForm();
      setNotice("Secrets updated. Changes apply to newly started commands.");
      await load();
    } catch (error) {
      if (current === generation) setError(error instanceof Error ? error.message : "Failed to update project secrets.");
    } finally { if (current === generation) setSaving(false); }
  };
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    if (editing()) {
      void mutate("PATCH", `/${encodeURIComponent(editing()!)}`, value() ? { value: value() } : {}, true);
    } else {
      void mutate("POST", "", { name: name(), value: value(), enabled: true }, true);
    }
  };
  createEffect(on(projectId, () => {
    generation += 1;
    closeForm(); setSecrets([]); setError(""); setNotice(""); setSaving(false);
    void load();
  }));
  onCleanup(() => { generation += 1; closeForm(); });

  return <section class="panel project-resource-panel" aria-label="Project secrets" aria-busy={loading() || saving()}>
    <div class="project-resource-toolbar">
      <h2>Secrets</h2>
      <div class="row-wrap">
        <button class="btn" disabled={loading() || saving()} onClick={() => void load()}>Reload secrets</button>
        <button class="btn btn-primary" disabled={saving() || formOpen()} onClick={() => {
          closeForm(); setError(""); setNotice(""); setFormOpen(true);
        }}>Add secret</button>
      </div>
    </div>
    <p>Enabled secrets are provided as environment variables to all commands in this project on Local, Daytona, and Modal.</p>
    <p class="hint">Changes apply to newly started commands. Running processes keep their existing values.</p>
    <Show when={error()}><div class="project-error" role="alert">{error()}</div></Show>
    <Show when={notice()}><p role="status">{notice()}</p></Show>
    <Show when={loading()}><p role="status">Loading secrets...</p></Show>
    <Show when={formOpen()}>
      <form class="project-form project-secret-form" autocomplete="off" onSubmit={submit}>
        <label class="field">Name
          <input class="input" aria-label="Secret name" value={name()} required maxlength={255}
            pattern="[A-Z_][A-Z0-9_]*" spellcheck={false} disabled={saving() || editing() !== null}
            onInput={(event) => setName(event.currentTarget.value)} placeholder="DEPLOY_TOKEN" />
        </label>
        <label class="field">Value
          <input class="input" aria-label="Secret value" type="password" autocomplete="new-password"
            required={editing() === null} value={value()} disabled={saving()} spellcheck={false}
            onInput={(event) => setValue(event.currentTarget.value)} />
        </label>
        <Show when={editing()}><p class="hint">Leave the value empty to keep the stored secret.</p></Show>
        <div class="row-wrap">
          <button class="btn btn-primary" type="submit" disabled={saving()}>{saving() ? "Saving..." : "Save secret"}</button>
          <button class="btn" type="button" disabled={saving()} onClick={closeForm}>Cancel</button>
        </div>
      </form>
    </Show>
    <Show when={!loading() && !secrets().length}><p class="empty compact">This project has no secrets.</p></Show>
    <div class="project-secret-list">
      <For each={secrets()}>{(secret) => <div class="project-secret-row">
        <div class="project-secret-info"><code>{secret.name}</code>
          <span>{secret.enabled ? "Enabled" : "Disabled"} · Updated {new Date(secret.updated_at).toLocaleString()}</span>
        </div>
        <div class="row-wrap">
          <button class="btn" disabled={saving() || loading()} aria-label={`${secret.enabled ? "Disable" : "Enable"} ${secret.name}`}
            onClick={() => void mutate("PATCH", `/${encodeURIComponent(secret.name)}`, { enabled: !secret.enabled })}>
            {secret.enabled ? "Disable" : "Enable"}
          </button>
          <button class="btn" disabled={saving() || loading() || formOpen()} aria-label={`Edit ${secret.name}`} onClick={() => {
            closeForm(); setEditing(secret.name); setName(secret.name); setFormOpen(true); setError("");
          }}>Edit</button>
          <button class="btn" disabled={saving() || loading()} aria-label={`Delete ${secret.name}`} onClick={() => {
            if (window.confirm(`Delete ${secret.name}? Newly started commands will no longer receive it.`)) {
              void mutate("DELETE", `/${encodeURIComponent(secret.name)}`, undefined, editing() === secret.name);
            }
          }}>Delete</button>
        </div>
      </div>}</For>
    </div>
  </section>;
}
