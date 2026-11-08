import { A } from "@solidjs/router";
import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { apiFetch, deleteJson, patchJson, postJson, putJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { chatThreadHref } from "@/lib/chatThreads";
import type { BrowserProfile } from "@/types";

export function BrowserProfiles() {
  const [profiles, setProfiles] = createSignal<BrowserProfile[]>([]);
  const [selected, setSelected] = createSignal<string | null>(null);
  const [temporarySelected, setTemporarySelected] = createSignal<string | null>(null);
  const [loading, setLoading] = createSignal(false);
  const [loaded, setLoaded] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const [newName, setNewName] = createSignal("");
  const [renaming, setRenaming] = createSignal<string | null>(null);
  const [renamed, setRenamed] = createSignal("");
  let generation = 0;
  const busy = () => loading() || saving();

  const load = async () => {
    const current = ++generation;
    setLoading(true); setError("");
    try {
      const [list, binding, temporary] = await Promise.all([
        apiFetch<{ profiles: BrowserProfile[] }>(API_PATHS.browserProfiles, { cache: "no-store" }),
        apiFetch<{ profile_id: string | null }>(API_PATHS.browserDefaultProfile, { cache: "no-store" }),
        apiFetch<{ profile_id: string | null }>(API_PATHS.browserTemporaryProfile, { cache: "no-store" }),
      ]);
      if (current === generation) {
        setProfiles(list.profiles); setSelected(binding.profile_id);
        setTemporarySelected(temporary.profile_id); setLoaded(true);
      }
    } catch (cause) {
      if (current === generation) setError(cause instanceof Error ? cause.message : "Failed to load login profiles.");
    } finally {
      if (current === generation) setLoading(false);
    }
  };
  const mutate = async (operation: () => Promise<string>) => {
    if (busy()) return false;
    setSaving(true); setError(""); setNotice("");
    try {
      const message = await operation();
      if (disposed) return false;
      setNotice(message);
      await load();
      return true;
    } catch (cause) {
      if (!disposed) setError(cause instanceof Error ? cause.message : "Failed to update login profiles.");
      return false;
    } finally { if (!disposed) setSaving(false); }
  };
  const create = async (event: SubmitEvent) => {
    event.preventDefault();
    const name = newName().trim();
    if (name && await mutate(async () => {
      await postJson(API_PATHS.browserProfiles, { name });
      return `Created ${name}.`;
    })) setNewName("");
  };
  const rename = async (event: SubmitEvent, profile: BrowserProfile) => {
    event.preventDefault();
    const name = renamed().trim();
    if (name && await mutate(async () => {
      await patchJson(`${API_PATHS.browserProfiles}/${profile.profile_id}`, { name });
      return `Renamed to ${name}.`;
    })) setRenaming(null);
  };
  const remove = (profile: BrowserProfile) => {
    if (window.confirm(`Delete ${profile.name}? Its saved logins are removed for every project and conversation that uses it.`)) {
      void mutate(async () => {
        const result = await deleteJson<{ saved_data_removed?: boolean }>(`${API_PATHS.browserProfiles}/${profile.profile_id}`);
        return result.saved_data_removed === false
          ? `Deleted ${profile.name}. Some of its files are still in use, and K41 removes them automatically once they are released.`
          : `Deleted ${profile.name}.`;
      });
    }
  };
  const selectProfile = async (select: HTMLSelectElement, temporary = false) => {
    const profileId = select.value || null;
    const saved = await mutate(async () => {
      const result = await putJson<{ profile_id: string }>(temporary ? API_PATHS.browserTemporaryProfile : API_PATHS.browserDefaultProfile, { profile_id: profileId });
      if (!disposed) (temporary ? setTemporarySelected : setSelected)(result.profile_id);
      return temporary ? "No workspace login profile updated." : "Account default login profile updated.";
    });
    // A rejected choice changes no state, so nothing else would restore the saved option.
    if (!saved) select.value = (temporary ? temporarySelected() : selected()) ?? "";
  };
  const openThreads = (profile: BrowserProfile) => profile.open_thread_ids ?? (profile.open_thread_id ? [profile.open_thread_id] : []);

  let disposed = false;
  onMount(() => void load());
  onCleanup(() => { disposed = true; generation += 1; });

  return <section id="login-profiles" class="panel settings-section-card" aria-label="Browser login profiles" aria-busy={busy()}>
    <div class="panel-header split">
      <h2 class="panel-title">Login profiles</h2>
      <button class="btn btn-secondary btn-sm" disabled={busy()} onClick={() => void load()}>Reload profiles</button>
    </div>
    <div class="panel-body stack">
    <p>Manage saved logins for your account. Conversations using the same profile share one browser and saved logins, with their own tabs.</p>
    <p class="hint">The account default applies to projects that inherit it. Conversations with No workspace have an independent setting. Changes apply when a browser next opens; conversation overrides keep their choice.</p>
    <Show when={error()}><div class="browser-setup-error" role="alert">{error()}</div></Show>
    <Show when={notice()}><p role="status">{notice()}</p></Show>
    <Show when={loading()}><p role="status">Loading profiles...</p></Show>
    <label class="field">Default login profile
      <select class="input" aria-label="Default login profile" disabled={busy() || !loaded()}
        onChange={(event) => void selectProfile(event.currentTarget)}>
        <For each={profiles()}>{(profile) => <option value={profile.profile_id} selected={profile.profile_id === selected()}>{profile.name}</option>}</For>
      </select>
    </label>
    <label class="field">No workspace login profile
      <select class="input" aria-label="No workspace login profile" disabled={busy() || !loaded()}
        onChange={(event) => void selectProfile(event.currentTarget, true)}>
        <For each={profiles()}>{(profile) => <option value={profile.profile_id} selected={profile.profile_id === temporarySelected()}>{profile.name}</option>}</For>
      </select>
    </label>
    <form class="row-wrap" autocomplete="off" onSubmit={(event) => void create(event)}>
      <label class="field">New profile name
        <input class="input" aria-label="New profile name" maxlength={100} value={newName()} disabled={busy()}
          onInput={(event) => setNewName(event.currentTarget.value)} />
      </label>
      <button class="btn btn-primary" type="submit" disabled={busy() || !newName().trim()}>Create profile</button>
    </form>
    <Show when={loaded() && !loading() && !profiles().length}><p class="empty compact">You have no login profiles.</p></Show>
    <div class="browser-profile-list">
      <For each={profiles()}>{(profile) => <div class="browser-profile-row">
        <Show when={renaming() === profile.profile_id} fallback={<>
          <div class="browser-profile-info"><strong>{profile.name}{profile.is_builtin ? " (built-in)" : ""}{selected() === profile.profile_id ? " (default)" : ""}</strong>
            <Show when={openThreads(profile).length} fallback={<span>Not open</span>}>
              <For each={openThreads(profile)}>{threadId => <A href={chatThreadHref(threadId)}>Open in conversation {threadId}</A>}</For>
            </Show>
            <Show when={profile.projects?.length} fallback={<span>No projects currently select this profile.</span>}>
              <div class="row-wrap"><span>Used by:</span><For each={profile.projects}>{(project) =>
                <A href={`/projects/${project.id}?tab=browser`}>{project.label}</A>
              }</For></div>
            </Show>
          </div>
          <Show when={!profile.is_builtin}><div class="row-wrap">
            <button class="btn" disabled={busy()} aria-label={`Rename ${profile.name}`} onClick={() => {
              setRenamed(profile.name); setRenaming(profile.profile_id); setError("");
            }}>Rename</button>
            <button class="btn" disabled={busy() || !!openThreads(profile).length} aria-label={`Delete ${profile.name}`}
              title={openThreads(profile).length ? "Close every conversation using this profile before deleting it." : undefined}
              onClick={() => remove(profile)}>Delete</button>
          </div></Show>
        </>}>
          <form class="row-wrap" autocomplete="off" onSubmit={(event) => void rename(event, profile)}>
            <input class="input" aria-label={`New name for ${profile.name}`} maxlength={100} value={renamed()} disabled={saving()}
              onInput={(event) => setRenamed(event.currentTarget.value)} />
            <button class="btn btn-primary" type="submit" disabled={saving() || !renamed().trim()}>Save name</button>
            <button class="btn" type="button" disabled={saving()} onClick={() => setRenaming(null)}>Cancel</button>
          </form>
        </Show>
      </div>}</For>
    </div>
    </div>
  </section>;
}
