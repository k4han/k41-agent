import { createMemo, createSignal, For, onMount } from "solid-js";
import { GitPullRequest, Save } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { DataGate } from "@/components/State";
import { IdentityPicker } from "@/components/IdentityPicker";
import { SelectControl } from "@/components/SelectControl";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson, putJson } from "@/lib/api";
import type { GitHubPayload, GitHubRepositoryBinding, Identity } from "@/types";

type RepositoryDraft = {
  enabled: boolean;
  agent_name: string;
  trigger_label: string;
  mention_triggers_text: string;
  notify_identity: string;
};

function toDraft(repo: GitHubRepositoryBinding): RepositoryDraft {
  const notifyIdentity = repo.notify_platform && repo.notify_external_id
    ? `${repo.notify_platform}:${repo.notify_external_id}`
    : "";
  return {
    enabled: repo.enabled,
    agent_name: repo.agent_name,
    trigger_label: repo.trigger_label,
    mention_triggers_text: repo.mention_triggers.join(", "),
    notify_identity: notifyIdentity,
  };
}

function splitTriggers(value: string): string[] {
  return value
    .replace(/\n/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function RepositoriesPage() {
  const [data, setData] = createSignal<GitHubPayload>();
  const [identities, setIdentities] = createSignal<Identity[]>([]);
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<number, RepositoryDraft>>({});
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const [payload, identitiesPayload] = await Promise.all([
        apiFetch<GitHubPayload>("/dashboard-api/github"),
        apiFetch<{ identities: Identity[] }>("/dashboard-api/tasks"),
      ]);
      setData(payload);
      setIdentities(identitiesPayload.identities || []);
      setDrafts(
        Object.fromEntries(payload.repositories.map((repo) => [repo.repository_id, toDraft(repo)])),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load repositories");
    }
  };

  const sync = async () => {
    try {
      await postJson("/dashboard-api/github/sync");
      showToast("GitHub repositories synced.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to sync GitHub", "error");
    }
  };

  const updateDraft = <K extends keyof RepositoryDraft>(
    repo: GitHubRepositoryBinding,
    key: K,
    value: RepositoryDraft[K],
  ) => {
    setDrafts((current) => ({
      ...current,
      [repo.repository_id]: {
        ...(current[repo.repository_id] || toDraft(repo)),
        [key]: value,
      },
    }));
  };

  const save = async (repo: GitHubRepositoryBinding) => {
    const draft = drafts()[repo.repository_id] || toDraft(repo);
    let notify_platform = "";
    let notify_external_id = "";
    if (draft.notify_identity) {
      const [platform, externalId] = draft.notify_identity.split(":", 2);
      notify_platform = platform;
      notify_external_id = externalId;
    }
    try {
      await putJson(`/dashboard-api/github/repositories/${repo.repository_id}/binding`, {
        enabled: draft.enabled,
        agent_name: draft.agent_name,
        trigger_label: draft.trigger_label,
        mention_triggers: splitTriggers(draft.mention_triggers_text),
        notify_platform,
        notify_external_id,
        notify_channel_id: notify_external_id,
      });
      showToast("Repository binding updated.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to update binding", "error");
    }
  };

  const filteredRepositories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return payload.repositories;
    }
    return payload.repositories.filter((repo) =>
      [
        repo.full_name,
        repo.account_login,
        repo.default_branch,
        String(repo.installation_id),
        repo.private ? "private" : "public",
        repo.enabled ? "enabled" : "disabled",
        repo.agent_name,
        repo.trigger_label,
        repo.mention_triggers.join(" "),
        repo.notify_platform,
        repo.notify_external_id,
        repo.notify_channel_id,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  });

  onMount(load);

  return (
    <AppShell
      title="Repositories"
      subtitle="Map GitHub repositories to agents."
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search repositories..."
              onSearchInput={setQuery}
              actions={
                <button class="btn btn-primary" type="button" onClick={sync}>
                  <GitPullRequest size={14} />
                  Sync GitHub
                </button>
              }
            />

            <section class="panel">
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Repository</th>
                      <th>Enabled</th>
                      <th>Agent</th>
                      <th>Triggers</th>
                      <th>Notification</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={filteredRepositories()}
                      fallback={
                        <EmptyTableRow
                          colSpan={6}
                          message={query().trim() ? "No repositories found." : "No repositories synced."}
                        />
                      }
                    >
                      {(repo) => {
                        const draft = () => drafts()[repo.repository_id] || toDraft(repo);
                        return (
                          <tr>
                            <td>
                              <div class="mono">{repo.full_name}</div>
                              <div class="chips chips-spaced">
                                <span class="chip">{repo.default_branch}</span>
                                <span class="chip">{repo.private ? "private" : "public"}</span>
                                <span class="chip">installation {repo.installation_id}</span>
                              </div>
                            </td>
                            <td>
                              <label class="checkbox-row">
                                <input
                                  type="checkbox"
                                  checked={draft().enabled}
                                  onChange={(event) => updateDraft(repo, "enabled", event.currentTarget.checked)}
                                />
                                <span>{draft().enabled ? "enabled" : "disabled"}</span>
                              </label>
                            </td>
                            <td>
                              <SelectControl
                                class="table-select"
                                value={draft().agent_name}
                                options={[...new Set([repo.agent_name, payload.default_agent, ...payload.agent_names].filter(Boolean))].map((agent) => ({ value: agent, label: agent }))}
                                onChange={(value) => updateDraft(repo, "agent_name", value)}
                                ariaLabel="Agent"
                              />
                            </td>
                            <td>
                              <div class="stack">
                                <input
                                  class="input"
                                  value={draft().trigger_label}
                                  placeholder={payload.trigger_label}
                                  onInput={(event) => updateDraft(repo, "trigger_label", event.currentTarget.value)}
                                />
                                <input
                                  class="input"
                                  value={draft().mention_triggers_text}
                                  placeholder={payload.mention_triggers.join(", ")}
                                  onInput={(event) => updateDraft(repo, "mention_triggers_text", event.currentTarget.value)}
                                />
                              </div>
                            </td>
                            <td>
                              <IdentityPicker
                                value={draft().notify_identity}
                                onChange={(value) => updateDraft(repo, "notify_identity", value)}
                                identities={identities()}
                              />
                            </td>
                            <td>
                              <button class="btn btn-sm" type="button" onClick={() => save(repo)}>
                                <Save size={13} />
                                Save
                              </button>
                            </td>
                          </tr>
                        );
                      }}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
