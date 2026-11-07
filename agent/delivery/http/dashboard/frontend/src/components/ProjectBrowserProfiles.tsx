import { A } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, on, onCleanup, Show } from "solid-js";
import { apiFetch, putJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { BrowserProfile, BrowserProjectProfile } from "@/types";

export function ProjectBrowserProfiles(props: { projectId: number }) {
  const [profiles, setProfiles] = createSignal<BrowserProfile[]>([]);
  const [settings, setSettings] = createSignal<BrowserProjectProfile>();
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const projectId = createMemo(() => props.projectId);
  const busy = () => loading() || saving();
  const selection = () => settings()?.mode === "profile" ? settings()?.profile_id ?? "separate" : settings()?.mode ?? "separate";
  const profileName = (id: string | null | undefined) => profiles().find(profile => profile.profile_id === id)?.name;
  let generation = 0;

  const load = async () => {
    const current = ++generation;
    setLoading(true); setError("");
    try {
      const [list, binding] = await Promise.all([
        apiFetch<{ profiles: BrowserProfile[] }>(API_PATHS.browserProfiles, { cache: "no-store" }),
        apiFetch<BrowserProjectProfile>(API_PATHS.browserProjectProfile(projectId()), { cache: "no-store" }),
      ]);
      if (current === generation) { setProfiles(list.profiles); setSettings(binding); }
    } catch (cause) {
      if (current === generation) setError(cause instanceof Error ? cause.message : "Failed to load login profiles.");
    } finally {
      if (current === generation) setLoading(false);
    }
  };

  const selectProfile = async (select: HTMLSelectElement) => {
    if (busy() || !settings()) return;
    const current = generation;
    const value = select.value;
    const mode = value === "inherit" || value === "separate" ? value : "profile";
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await putJson<BrowserProjectProfile>(API_PATHS.browserProjectProfile(projectId()), {
        mode, profile_id: mode === "profile" ? value : null,
      });
      if (current === generation) { setSettings(result); setNotice("Project login profile updated."); }
    } catch (cause) {
      if (current === generation) setError(cause instanceof Error ? cause.message : "Failed to select a login profile.");
    } finally {
      if (current === generation) { setSaving(false); select.value = selection(); }
    }
  };

  createEffect(on(projectId, () => {
    setProfiles([]); setSettings(undefined); setSaving(false); setNotice("");
    void load();
  }));
  onCleanup(() => { generation += 1; });

  return <section class="panel project-resource-panel" aria-label="Browser login profiles" aria-busy={busy()}>
    <div class="project-resource-toolbar">
      <h2>Browser login profile</h2>
      <button class="btn" disabled={busy()} onClick={() => void load()}>Reload profiles</button>
    </div>
    <p>Choose the login profile for conversations that do not have a browser binding yet. Existing browser bindings keep their current choice.</p>
    <p class="hint">A profile can serve several projects, but only one browser can use it at a time. Change a conversation's choice from Login profiles in its Browser tab.</p>
    <Show when={error()}><div class="project-error" role="alert">{error()}</div></Show>
    <Show when={notice()}><p role="status">{notice()}</p></Show>
    <Show when={loading()}><p role="status">Loading profiles...</p></Show>
    <label class="field">Profile for new conversations
      <select class="input" aria-label="Profile for new conversations" disabled={busy() || !settings()}
        onChange={(event) => void selectProfile(event.currentTarget)}>
        <option value="inherit" selected={selection() === "inherit"}>Use account default ({profileName(settings()?.default_profile_id) ?? "separate logins"})</option>
        <option value="separate" selected={selection() === "separate"}>Separate login for each conversation</option>
        <For each={profiles()}>{(profile) => <option value={profile.profile_id} selected={selection() === profile.profile_id}>
          {profile.name}{profile.open_thread_id ? " (currently in use)" : ""}
        </option>}</For>
      </select>
    </label>
    <p class="hint">Effective profile: {profileName(settings()?.effective_profile_id) ?? "Separate login for each conversation"}.</p>
    <A class="btn" href="/settings/browser#login-profiles">Manage login profiles</A>
  </section>;
}
