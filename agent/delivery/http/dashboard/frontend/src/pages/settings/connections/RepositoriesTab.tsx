import {
  createEffect,
  createMemo,
  createSignal,
  For,
  JSX,
  onMount,
  Show,
} from "solid-js";
import { useNavigate } from "@solidjs/router";
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  Copy,
  ExternalLink,
  GitPullRequest,
  PlugZap,
  Save,
  Settings as SettingsIcon,
  TriangleAlert,
  XCircle,
} from "lucide-solid";

import { SelectControl, type SelectControlOption } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { GitHubPayload, SettingInfo } from "@/types";
import { SettingsLayout } from "../SettingsLayout";

import {
  type PendingChange,
  SettingRow,
  sameValue,
  typedValue,
} from "../shared";

type SectionGroupDef = {
  id: string;
  title: string;
  subtitle?: string;
  icon?: () => JSX.Element;
  fields: string[];
};

type ChannelsPayload = {
  settings: Record<string, SettingInfo>;
  by_channel: Record<string, Record<string, SettingInfo>>;
};

type TestOutcome = {
  ok: boolean;
  message: string;
};

const GITHUB_SECTIONS: SectionGroupDef[] = [
  {
    id: "triggers",
    title: "Triggers",
    subtitle: "Labels and mentions that summon the agent",
    fields: ["default_agent", "trigger_label", "mention_triggers"],
  },
];

// GitHub App identity (app_id, private key, webhook secret) is platform-managed
// via server environment (GITHUB_APP_*). End users only install the App and
// configure per-repository bindings. See GitHubSettingsPage banner below.

const ENABLED_FIELD = "enabled";

function useGitHubData() {
  const [channelsData, setChannelsData] = createSignal<ChannelsPayload>();
  const [channelsError, setChannelsError] = createSignal("");
  const [github, setGitHub] = createSignal<GitHubPayload>();
  const [githubError, setGitHubError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [busy, setBusy] = createSignal<string | null>(null);
  const [testResult, setTestResult] = createSignal<TestOutcome | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setChannelsError("");
    setGitHubError("");
    try {
      const [channelsPayload, githubPayload] = await Promise.all([
        apiFetch<ChannelsPayload>("/dashboard-api/channels"),
        apiFetch<GitHubPayload>("/dashboard-api/github"),
      ]);
      setChannelsData(channelsPayload);
      setDrafts(
        Object.fromEntries(
          Object.entries(channelsPayload.settings)
            .filter(([key]) => key.startsWith("channels.github."))
            .map(([key, info]) => [key, info.value]),
        ),
      );
      setGitHub(githubPayload);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load";
      if (!channelsData()) {
        setChannelsError(message);
      }
      if (!github()) {
        setGitHubError(message);
      }
    }
  };

  onMount(load);

  const settingKey = (suffix: string) => `channels.github.${suffix}`;

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const restoreDraft = (key: string) => {
    const info = channelsData()?.settings[key];
    if (!info) {
      showToast("No server value to restore.", "warning");
      return;
    }
    setDraft(key, info.value ?? null);
    showToast("Change reverted.", "warning");
  };

  const pendingChanges = createMemo<PendingChange[]>(() => {
    const data = channelsData();
    if (!data) {
      return [];
    }
    const prefix = "channels.github.";
    return Object.entries(data.settings)
      .filter(([key]) => key.startsWith(prefix))
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  });

  const isConfigured = (): boolean => Boolean(github()?.configured);

  const toggleEnabled = async (next: boolean) => {
    const key = settingKey(ENABLED_FIELD);
    setDraft(key, next);
    setBusy("toggle");
    try {
      await putJson("/settings", { values: { [key]: next } });
      showToast(`GitHub ${next ? "enabled" : "disabled"}.`);
      await load();
    } catch (err) {
      const previous = channelsData()?.settings[key]?.value ?? null;
      setDraft(key, previous);
      showToast(
        err instanceof Error ? err.message : "Failed to update setting",
        "error",
      );
    } finally {
      setBusy(null);
    }
  };

  const sync = async () => {
    setBusy("sync");
    try {
      await postJson("/dashboard-api/github/sync");
      showToast("GitHub repositories synced.");
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to sync GitHub",
        "error",
      );
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy("test");
    try {
      const result = await postJson<TestOutcome>("/services/github/test");
      setTestResult(result);
      if (result.ok) {
        showToast("Connection successful.");
      } else {
        showToast(`Connection failed: ${result.message}`, "error");
      }
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Test failed";
      setTestResult({ ok: false, message });
      showToast(message, "error");
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    const changes = pendingChanges();
    if (!changes.length) {
      return;
    }
    setBusy("save");
    try {
      const values = Object.fromEntries(
        changes.map((change) => [change.key, change.newValue]),
      );
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} GitHub setting(s).`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save settings",
        "error",
      );
    } finally {
      setBusy(null);
    }
  };

  return {
    channelsData,
    channelsError,
    github,
    githubError,
    drafts,
    busy,
    testResult,
    pendingChanges,
    isConfigured,
    load,
    setDraft,
    restoreDraft,
    toggleEnabled,
    sync,
    test,
    save,
  };
}

export function RepositoriesTab() {
  const navigate = useNavigate();
  const gh = useGitHubData();

  return (
    <div class="channels-grid">
      <article
        class="channel-card"
        onClick={() => navigate("/settings/connections/github")}
        style={{ cursor: "pointer" }}
      >
        <header class="channel-card-header">
          <div
            class="channel-brand-icon"
            data-brand="github"
            aria-hidden="true"
          >
            <GitHubIcon />
          </div>
          <div class="channel-card-title">
            <div class="channel-card-name">GitHub</div>
          </div>
          <div class="row-wrap">
            <span
              class={
                gh.github()?.enabled
                  ? "badge badge-success"
                  : "badge badge-warning"
              }
              title={gh.github()?.enabled ? "Enabled" : "Disabled"}
            >
              {gh.github()?.enabled ? "enabled" : "disabled"}
            </span>
            <Show when={gh.github()?.install_url && gh.isConfigured()}>
              <a
                class="btn btn-sm"
                href={gh.github()!.install_url}
                target="_blank"
                rel="noreferrer"
                title="Open GitHub App installation page"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink size={13} />
                Install App
              </a>
            </Show>
          </div>
        </header>

        <div class="channel-card-body">
          <Show when={!gh.isConfigured() && (gh.channelsData() || gh.github())}>
            <div class="channel-card-empty-hint">
              <TriangleAlert size={14} />
              <Show
                when={gh.github()?.enabled === false}
                fallback={
                  <div>
                    GitHub App is not configured on the server.{" "}
                    <Show
                      when={
                        gh.github()?.missing_requirements &&
                        (gh.github()?.missing_requirements?.length ?? 0) > 0
                      }
                      fallback={
                        <span>
                          Set <strong>GITHUB_APP_ID</strong>,{" "}
                          <strong>GITHUB_APP_PRIVATE_KEY</strong> and{" "}
                          <strong>GITHUB_WEBHOOK_SECRET</strong> in the server
                          environment.
                        </span>
                      }
                    >
                      <span>
                        Missing: {gh.github()?.missing_requirements?.join("; ")}.
                      </span>
                    </Show>
                  </div>
                }
              >
                <div>
                  GitHub integration is disabled. Enable it in the dashboard to
                  use automation.
                </div>
              </Show>
            </div>
          </Show>

          <DataGate
            data={gh.github()}
            error={gh.channelsError() || gh.githubError()}
            onRetry={gh.load}
          >
            {(payload) => (
              <>
                <div class="channel-card-meta">
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">App Slug</span>
                    <span class="mono">{payload().app_slug || "Not set"}</span>
                  </div>
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Repositories</span>
                    <span class="mono">{payload().repositories.length}</span>
                  </div>
                </div>
                <div class="field">
                  <label>Webhook URL</label>
                  <div class="channel-webhook-helper-value" onClick={(e) => e.stopPropagation()}>
                    <code>{payload().webhook_url}</code>
                    <button
                      class="btn btn-sm"
                      type="button"
                      onClick={() =>
                        void writeToClipboard(payload().webhook_url).catch(() => {})
                      }
                    >
                      <Copy size={13} />
                      Copy
                    </button>
                  </div>
                </div>
              </>
            )}
          </DataGate>
        </div>

        <footer class="channel-card-footer">
          <label class="channel-toggle-cell" onClick={(e) => e.stopPropagation()}>
            <button
              class={`toggle-control ${gh.github()?.enabled ? "active" : ""}`}
              type="button"
              role="switch"
              aria-checked={Boolean(gh.github()?.enabled)}
              disabled={gh.busy() === "toggle"}
              onClick={() => void gh.toggleEnabled(!gh.github()?.enabled)}
            >
              <span class="toggle-track">
                <span class="toggle-thumb" />
              </span>
              <span class="toggle-label">Enabled</span>
            </button>
            <span>{gh.github()?.enabled ? "Enabled" : "Disabled"}</span>
          </label>

          <div class="channel-card-actions">
            <button
              class="btn btn-sm"
              type="button"
              disabled={gh.busy() === "sync"}
              onClick={(e) => {
                e.stopPropagation();
                void gh.sync();
              }}
              title="Sync repositories from GitHub"
            >
              <GitPullRequest size={13} />
              {gh.busy() === "sync" ? "Syncing..." : "Sync"}
            </button>
            <button
              class="btn btn-sm"
              type="button"
              disabled={gh.busy() === "test" || !gh.isConfigured()}
              onClick={(e) => {
                e.stopPropagation();
                void gh.test();
              }}
              title={
                gh.isConfigured()
                  ? "Verify credentials with GitHub API"
                  : "Configure credentials first"
              }
            >
              <PlugZap size={13} />
              {gh.busy() === "test" ? "Testing..." : "Test"}
            </button>
            <button
              class="btn btn-sm btn-primary"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                navigate("/settings/connections/github");
              }}
            >
              <SettingsIcon size={13} />
              Configure
              <Show when={gh.pendingChanges().length > 0}>
                <span
                  class="badge badge-warning"
                  style={{ "margin-left": "4px" }}
                >
                  {gh.pendingChanges().length}
                </span>
              </Show>
            </button>
          </div>
        </footer>

        <Show when={gh.testResult()}>
          {(result) => (
            <div
              class="channel-test-feedback"
              data-state={result().ok ? "ok" : "error"}
            >
              <div class="channel-test-feedback-title">
                <Show when={result().ok} fallback={<XCircle size={14} />}>
                  <CheckCircle2 size={14} />
                </Show>
                <span>{result().message}</span>
              </div>
            </div>
          )}
        </Show>
      </article>
    </div>
  );
}

export function GitHubSettingsPage() {
  const navigate = useNavigate();
  const gh = useGitHubData();

  return (
    <SettingsLayout
      title="GitHub Settings"
      breadcrumbSegments={[
        { label: "Connections", href: "/settings/connections" },
        { label: "GitHub" },
      ]}
      contentWidth="wide"
      actions={
        <div class="row-wrap" style={{ gap: "8px" }}>
          <button
            class="btn btn-sm"
            type="button"
            onClick={() => navigate("/settings/connections")}
          >
            <ArrowLeft size={14} />
            Back to Connections
          </button>
          <button
            class="btn btn-sm"
            type="button"
            disabled={gh.busy() === "test" || !gh.isConfigured()}
            onClick={() => void gh.test()}
          >
            <PlugZap size={13} />
            {gh.busy() === "test" ? "Testing..." : "Test connection"}
          </button>
          <button
            class="btn btn-sm btn-primary"
            type="button"
            disabled={gh.pendingChanges().length === 0 || gh.busy() === "save"}
            onClick={() => void gh.save()}
          >
            <Save size={13} />
            {gh.busy() === "save"
              ? "Saving..."
              : `Save changes ${
                  gh.pendingChanges().length
                    ? `(${gh.pendingChanges().length})`
                    : ""
                }`}
          </button>
        </div>
      }
    >
      <DataGate
        data={gh.github()}
        error={gh.channelsError() || gh.githubError()}
        onRetry={gh.load}
      >
        {(payload) => (
          <div class="stack" style={{ gap: "20px" }}>
            {/* Overview Header */}
            <div class="channel-config-card">
              <div class="channel-config-header" style={{ "border-bottom": "none", padding: "0" }}>
                <div class="channel-config-header-left">
                  <div
                    class="channel-brand-icon"
                    data-brand="github"
                    aria-hidden="true"
                  >
                    <GitHubIcon />
                  </div>
                  <div>
                    <h2 class="channel-config-title">GitHub Connection</h2>
                    <p class="hint channel-config-subtitle">
                      App Slug: {payload().app_slug || "Not set"} &bull; Repositories: {payload().repositories.length}
                    </p>
                  </div>
                </div>
                <div class="row-wrap channel-config-header-actions">
                  <label class="channel-toggle-cell">
                    <button
                      class={`toggle-control ${gh.github()?.enabled ? "active" : ""}`}
                      type="button"
                      role="switch"
                      aria-checked={Boolean(gh.github()?.enabled)}
                      disabled={gh.busy() === "toggle"}
                      onClick={() => void gh.toggleEnabled(!gh.github()?.enabled)}
                    >
                      <span class="toggle-track">
                        <span class="toggle-thumb" />
                      </span>
                      <span class="toggle-label">Enabled</span>
                    </button>
                    <span>{gh.github()?.enabled ? "Enabled" : "Disabled"}</span>
                  </label>
                  <button
                    class="btn btn-sm"
                    type="button"
                    disabled={gh.busy() === "sync"}
                    onClick={() => void gh.sync()}
                  >
                    <GitPullRequest size={13} />
                    {gh.busy() === "sync" ? "Syncing..." : "Sync Repos"}
                  </button>
                  <Show when={payload().install_url && payload().configured}>
                    <a
                      class="btn btn-sm"
                      href={payload().install_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={13} />
                      Install App
                    </a>
                  </Show>
                </div>
              </div>
            </div>

            <div class="channel-card-empty-hint">
              <TriangleAlert size={14} />
              <div>
                GitHub App credentials (<strong>GITHUB_APP_ID</strong>,{" "}
                <strong>GITHUB_APP_PRIVATE_KEY</strong>,{" "}
                <strong>GITHUB_WEBHOOK_SECRET</strong>) are managed by the server
                environment. Ask your operator to set them. End users only{" "}
                <strong>Install App</strong> and configure triggers per repository.
              </div>
            </div>

            {/* Test feedback */}
            <Show when={gh.testResult()}>
              {(result) => (
                <div
                  class="channel-test-feedback"
                  data-state={result().ok ? "ok" : "error"}
                >
                  <div class="channel-test-feedback-title">
                    <Show when={result().ok} fallback={<XCircle size={14} />}>
                      <CheckCircle2 size={14} />
                    </Show>
                    <span>{result().message}</span>
                  </div>
                </div>
              )}
            </Show>

            {/* Section Groups */}
            <div class="channel-sections-list">
              <For each={GITHUB_SECTIONS}>
                {(section) => {
                  const channelSettings = () =>
                    gh.channelsData()?.by_channel["github"] || {};
                  return (
                    <GitHubSectionGroup
                      section={section}
                      settings={channelSettings()}
                      drafts={gh.drafts()}
                      pending={gh.pendingChanges()}
                      agentNames={gh.github()?.agent_names || []}
                      onChange={gh.setDraft}
                      onRestore={gh.restoreDraft}
                    />
                  );
                }}
              </For>
            </div>
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function GitHubSectionGroup(props: {
  section: SectionGroupDef;
  settings: Record<string, SettingInfo>;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  agentNames: string[];
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
}) {
  const fieldEntries = createMemo(() => {
    return props.section.fields
      .map((suffix) => {
        const key = `channels.github.${suffix}`;
        const info = props.settings[key];
        return info ? { key, info } : null;
      })
      .filter(
        (entry): entry is { key: string; info: SettingInfo } => entry !== null,
      );
  });

  return (
    <div class="channel-section-group">
      <div class="channel-section-header">
        <div class="channel-section-header-left">
          <div class="channel-section-title">
            <Show when={props.section.icon}>{props.section.icon!()}</Show>
            {props.section.title}
          </div>
          <Show when={props.section.subtitle}>
            <span class="hint channel-section-subtitle">
              {props.section.subtitle}
            </span>
          </Show>
        </div>
      </div>
      <div class="channel-section-body">
        <For
          each={fieldEntries()}
          fallback={
            <div class="empty" style={{ padding: "18px" }}>
              No fields available.
            </div>
          }
        >
          {(entry) => {
            const dirty = () =>
              props.pending.some((change: PendingChange) => change.key === entry.key);
            const isDefaultAgent = entry.key === "channels.github.default_agent";
            return (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={dirty()}
                control={
                  isDefaultAgent ? (
                    <AgentNameSelect
                      value={String(props.drafts[entry.key] ?? "")}
                      agentNames={props.agentNames}
                      onChange={(value) => props.onChange(entry.key, value)}
                    />
                  ) : undefined
                }
                onChange={(value) => props.onChange(entry.key, value)}
                onRestore={() => props.onRestore(entry.key)}
              />
            );
          }}
        </For>
      </div>
    </div>
  );
}

function AgentNameSelect(props: {
  value: string;
  agentNames: string[];
  onChange: (value: string) => void;
}) {
  const options = createMemo<SelectControlOption[]>(() => {
    const names = new Set(props.agentNames);
    if (props.value) {
      names.add(props.value);
    }
    return [...names]
      .sort((a, b) => a.localeCompare(b))
      .map((name) => ({ value: name, label: name }));
  });

  return (
    <SelectControl
      value={props.value}
      options={options()}
      onChange={props.onChange}
      ariaLabel="GitHub default agent"
      icon={<Bot size={14} />}
    />
  );
}

function GitHubIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 .5a11.5 11.5 0 0 0-3.6 22.4c.6.1.8-.2.8-.5v-2c-3.2.7-3.8-1.5-3.8-1.5-.5-1.3-1.3-1.6-1.3-1.6-1-.7.1-.7.1-.7 1.1.1 1.7 1.2 1.7 1.2 1 1.8 2.7 1.3 3.4 1 .1-.7.4-1.2.8-1.5-2.6-.3-5.3-1.3-5.3-5.7 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.7 1.7.2 2.9.1 3.2.8.8 1.2 1.8 1.2 3.1 0 4.4-2.7 5.4-5.3 5.7.4.4.8 1.1.8 2.3v3.3c0 .3.2.6.8.5A11.5 11.5 0 0 0 12 .5z" />
    </svg>
  );
}
