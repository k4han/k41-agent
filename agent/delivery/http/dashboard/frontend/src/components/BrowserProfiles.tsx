import { A } from "@solidjs/router";
import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { apiFetch, deleteJson, patchJson, postJson, putJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { chatThreadHref } from "@/lib/chatThreads";
import type { BrowserProfile } from "@/types";

export function BrowserProfiles() {
  const [profiles, setProfiles] = createSignal<BrowserProfile[]>([]);
  const [selected, setSelected] = createSignal<string | null>(null);
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
      const [list, binding] = await Promise.all([
        apiFetch<{ profiles: BrowserProfile[] }>(API_PATHS.browserProfiles, { cache: "no-store" }),
        apiFetch<{ profile_id: string | null }>(API_PATHS.browserDefaultProfile, { cache: "no-store" }),
      ]);
      if (current === generation) { setProfiles(list.profiles); setSelected(binding.profile_id); setLoaded(true); }
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
  const selectProfile = async (select: HTMLSelectElement) => {
    const profileId = select.value || null;
    const saved = await mutate(async () => {
      await putJson(API_PATHS.browserDefaultProfile, { profile_id: profileId });
      if (!disposed) setSelected(profileId);
      return "Account default login profile updated.";
    });
    // A rejected choice changes no state, so nothing else would restore the saved option.
    if (!saved) select.value = selected() ?? "";
  };

  let disposed = false;
  onMount(() => void load());
  onCleanup(() => { disposed = true; generation += 1; });

  return <section id="login-profiles" class="panel settings-section-card" aria-label="Browser login profiles" aria-busy={busy()}>
    <div class="panel-header split">
      <h2 class="panel-title">Login profiles</h2>
      <button class="btn btn-secondary btn-sm" disabled={busy()} onClick={() => void load()}>Reload profiles</button>
    </div>
    <div class="panel-body stack">
    <p>Manage saved logins for your account. Profiles can serve several projects, but only one browser can use a profile at a time.</p>
    <p class="hint">The account default applies to conversations outside projects and projects set to use it. Existing browser bindings keep their profile.</p>
    <Show when={error()}><div class="browser-setup-error" role="alert">{error()}</div></Show>
    <Show when={notice()}><p role="status">{notice()}</p></Show>
    <Show when={loading()}><p role="status">Loading profiles...</p></Show>
    <label class="field">Default login profile
      <select class="input" aria-label="Default login profile" disabled={busy() || !loaded()}
        onChange={(event) => void selectProfile(event.currentTarget)}>
        <option value="" selected={!selected()}>Separate login for each conversation</option>
        <For each={profiles()}>{(profile) => <option value={profile.profile_id} selected={profile.profile_id === selected()}>{profile.name}</option>}</For>
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
          <div class="browser-profile-info"><strong>{profile.name}{selected() === profile.profile_id ? " (default)" : ""}</strong>
            <Show when={profile.open_thread_id} fallback={<span>Not open</span>}>{(threadId) =>
              <A href={chatThreadHref(threadId())}>Open in conversation {threadId()}</A>
            }</Show>
            <Show when={profile.projects?.length} fallback={<span>No projects currently select this profile.</span>}>
              <div class="row-wrap"><span>Used by:</span><For each={profile.projects}>{(project) =>
                <A href={`/projects/${project.id}?tab=browser`}>{project.label}</A>
              }</For></div>
            </Show>
          </div>
          <div class="row-wrap">
            <button class="btn" disabled={busy()} aria-label={`Rename ${profile.name}`} onClick={() => {
              setRenamed(profile.name); setRenaming(profile.profile_id); setError("");
            }}>Rename</button>
            <button class="btn" disabled={busy() || !!profile.open_thread_id} aria-label={`Delete ${profile.name}`}
              title={profile.open_thread_id ? "Close the browser before deleting this profile." : undefined}
              onClick={() => remove(profile)}>Delete</button>
          </div>
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
