import {
  createEffect,
  createMemo,
  createSignal,
  For,
  JSX,
  onMount,
  Show,
} from "solid-js";
import {
  Bot,
  CheckCircle2,
  Copy,
  ExternalLink,
  GitPullRequest,
  PlugZap,
  Save,
  Settings as SettingsIcon,
  TriangleAlert,
  Upload,
  Webhook,
  XCircle,
} from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SelectControl, type SelectControlOption } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { GitHubPayload, SettingInfo } from "@/types";

import {
  type PendingChange,
  SettingRow,
  sameValue,
  typedValue,
} from "../shared";

type DrawerSection = {
  id: string;
  title: string;
  subtitle?: string;
  icon?: JSX.Element;
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

const GITHUB_SECTIONS: DrawerSection[] = [
  {
    id: "app",
    title: "GitHub App",
    subtitle: "App identity and private key",
    fields: ["app_id", "app_slug", "private_key", "private_key_path"],
  },
  {
    id: "triggers",
    title: "Triggers",
    subtitle: "Labels and mentions that summon the agent",
    fields: ["default_agent", "trigger_label", "mention_triggers"],
  },
  {
    id: "webhook",
    title: "Webhook",
    subtitle: "Shared secret for webhook signature verification",
    icon: <Webhook size={14} />,
    fields: ["webhook_secret"],
  },
];

const ENABLED_FIELD = "enabled";

export function RepositoriesTab() {
  return (
    <div class="channels-grid">
      <GitHubConnectionCard />
    </div>
  );
}

function GitHubConnectionCard() {
  const [channelsData, setChannelsData] = createSignal<ChannelsPayload>();
  const [channelsError, setChannelsError] = createSignal("");
  const [github, setGitHub] = createSignal<GitHubPayload>();
  const [githubError, setGitHubError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [collapsed, setCollapsed] = createSignal<Record<string, boolean>>({});
  const [drawerOpen, setDrawerOpen] = createSignal(false);
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

  createEffect(() => {
    const data = channelsData();
    if (!data) {
      return;
    }
    const channelSettings = data.by_channel["github"] || {};
    setCollapsed((current) => {
      const next = { ...current };
      for (const section of GITHUB_SECTIONS) {
        const id = `github:${section.id}`;
        if (id in next) {
          continue;
        }
        const hasValue = section.fields.some((field) => {
          const info = channelSettings[`channels.github.${field}`];
          return info?.value && String(info.value).length > 0;
        });
        next[id] = !hasValue && section.id !== "app";
      }
      return next;
    });
  });

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
      setDrawerOpen(false);
      return;
    }
    setBusy("save");
    try {
      const values = Object.fromEntries(
        changes.map((change) => [change.key, change.newValue]),
      );
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} GitHub setting(s).`);
      setDrawerOpen(false);
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

  const toggleSection = (sectionId: string) => {
    setCollapsed((current) => ({
      ...current,
      [`github:${sectionId}`]: !current[`github:${sectionId}`],
    }));
  };

  return (
    <>
      <article class="channel-card">
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
            <div class="channel-card-subtitle">Repository hosting</div>
          </div>
          <div class="row-wrap">
            <span
              class={
                github()?.enabled
                  ? "badge badge-success"
                  : "badge badge-warning"
              }
              title={github()?.enabled ? "Enabled" : "Disabled"}
            >
              {github()?.enabled ? "enabled" : "disabled"}
            </span>
            <Show when={github()?.install_url}>
              <a
                class="btn btn-sm"
                href={github()!.install_url}
                target="_blank"
                rel="noreferrer"
                title="Open GitHub App installation page"
              >
                <ExternalLink size={13} />
                Install App
              </a>
            </Show>
          </div>
        </header>

        <div class="channel-card-body">
          <div class="hint">
            Trigger agents from issues, pull requests, and discussions.
          </div>

          <Show when={!isConfigured() && (channelsData() || github())}>
            <div class="channel-card-empty-hint">
              <TriangleAlert size={14} />
              <div>
                Add credentials in <strong>Configure</strong> to enable this
                connection.
              </div>
            </div>
          </Show>

          <DataGate
            data={github()}
            error={channelsError() || githubError()}
            onRetry={load}
          >
            {(payload) => (
              <>
                <div class="channel-card-meta">
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">App Slug</span>
                    <span class="mono">{payload.app_slug || "Not set"}</span>
                  </div>
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Repositories</span>
                    <span class="mono">{payload.repositories.length}</span>
                  </div>
                </div>
                <div class="field">
                  <label>Webhook URL</label>
                  <div class="channel-webhook-helper-value">
                    <code>{payload.webhook_url}</code>
                    <button
                      class="btn btn-sm"
                      type="button"
                      onClick={() =>
                        void writeToClipboard(payload.webhook_url).catch(() => {})
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
          <label class="channel-toggle-cell">
            <button
              class={`toggle-control ${github()?.enabled ? "active" : ""}`}
              type="button"
              role="switch"
              aria-checked={Boolean(github()?.enabled)}
              disabled={busy() === "toggle"}
              onClick={() => void toggleEnabled(!github()?.enabled)}
            >
              <span class="toggle-track">
                <span class="toggle-thumb" />
              </span>
              <span class="toggle-label">Enabled</span>
            </button>
            <span>{github()?.enabled ? "Enabled" : "Disabled"}</span>
          </label>

          <div class="channel-card-actions">
            <button
              class="btn btn-sm"
              type="button"
              disabled={busy() === "sync"}
              onClick={() => void sync()}
              title="Sync repositories from GitHub"
            >
              <GitPullRequest size={13} />
              {busy() === "sync" ? "Syncing..." : "Sync"}
            </button>
            <button
              class="btn btn-sm"
              type="button"
              disabled={busy() === "test" || !isConfigured()}
              onClick={() => void test()}
              title={
                isConfigured()
                  ? "Verify credentials with GitHub API"
                  : "Configure credentials first"
              }
            >
              <PlugZap size={13} />
              {busy() === "test" ? "Testing..." : "Test"}
            </button>
            <button
              class="btn btn-sm btn-primary"
              type="button"
              onClick={() => setDrawerOpen(true)}
            >
              <SettingsIcon size={13} />
              Configure
              <Show when={pendingChanges().length > 0}>
                <span
                  class="badge badge-warning"
                  style={{ "margin-left": "4px" }}
                >
                  {pendingChanges().length}
                </span>
              </Show>
            </button>
          </div>
        </footer>

        <Show when={testResult()}>
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

      <Show when={drawerOpen()}>
        <Dialog
          open={true}
          title="GitHub settings"
          wide
          onClose={() => setDrawerOpen(false)}
          footer={
            <div
              class="row-wrap"
              style={{
                "justify-content": "space-between",
                width: "100%",
              }}
            >
              <div class="row-wrap">
                <button
                  class="btn"
                  type="button"
                  disabled={busy() === "test"}
                  onClick={() => void test()}
                >
                  <PlugZap size={14} />
                  {busy() === "test" ? "Testing..." : "Test connection"}
                </button>
              </div>
              <div class="row-wrap">
                <button
                  class="btn"
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                >
                  Close
                </button>
                <button
                  class="btn btn-primary"
                  type="button"
                  disabled={
                    pendingChanges().length === 0 || busy() === "save"
                  }
                  onClick={() => void save()}
                >
                  <Save size={14} />
                  {busy() === "save"
                    ? "Saving..."
                    : `Save ${
                        pendingChanges().length
                          ? `(${pendingChanges().length})`
                          : ""
                      }`}
                </button>
              </div>
            </div>
          }
        >
          <div class="channel-drawer-sections">
            <For each={GITHUB_SECTIONS}>
              {(section) => {
                const id = `github:${section.id}`;
                const channelSettings = () =>
                  channelsData()?.by_channel["github"] || {};
                return (
                  <DrawerSection
                    section={section}
                    settings={channelSettings()}
                    drafts={drafts()}
                    pending={pendingChanges()}
                    agentNames={github()?.agent_names || []}
                    collapsed={Boolean(collapsed()[id])}
                    onToggle={() => toggleSection(section.id)}
                    onChange={setDraft}
                    onRestore={restoreDraft}
                  />
                );
              }}
            </For>
          </div>
        </Dialog>
      </Show>
    </>
  );
}

function DrawerSection(props: {
  section: DrawerSection;
  settings: Record<string, SettingInfo>;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  agentNames: string[];
  collapsed: boolean;
  onToggle: () => void;
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
    <section class="channel-drawer-section" data-collapsed={props.collapsed}>
      <button
        type="button"
        class="channel-drawer-section-header"
        onClick={props.onToggle}
      >
        <div>
          <div class="channel-drawer-section-title">
            <Show when={props.section.icon}>{props.section.icon}</Show>
            {props.section.title}
          </div>
          <Show when={props.section.subtitle}>
            <div class="channel-drawer-section-subtitle">
              {props.section.subtitle}
            </div>
          </Show>
        </div>
      </button>
      <div class="channel-drawer-section-body">
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
              props.pending.some((change) => change.key === entry.key);
            const isPrivateKey = entry.key === "channels.github.private_key";
            const isDefaultAgent = entry.key === "channels.github.default_agent";
            return (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={dirty()}
                showDescription
                actions={
                  isPrivateKey ? (
                    <PrivateKeyUpload
                      onLoad={(content) => props.onChange(entry.key, content)}
                    />
                  ) : undefined
                }
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
    </section>
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

function PrivateKeyUpload(props: { onLoad: (content: string) => void }) {
  let inputRef!: HTMLInputElement;
  const { showToast } = useToast();

  const handleFile = async (event: Event) => {
    const target = event.currentTarget as HTMLInputElement;
    const file = target.files?.[0];
    if (!file) {
      return;
    }
    try {
      const content = await file.text();
      const trimmed = content.trim();
      if (!trimmed) {
        showToast("Selected file is empty.", "warning");
        return;
      }
      if (!trimmed.includes("PRIVATE KEY")) {
        showToast(
          "File does not look like a PEM private key, loaded anyway.",
          "warning",
        );
      } else {
        showToast(`Loaded private key from ${file.name}.`);
      }
      props.onLoad(trimmed);
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to read file",
        "error",
      );
    } finally {
      target.value = "";
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pem,.key,.crt,application/x-pem-file,text/plain"
        style={{ display: "none" }}
        onChange={(event) => void handleFile(event)}
      />
      <button
        class="btn btn-sm"
        type="button"
        title="Read a PEM file from your computer"
        onClick={() => inputRef.click()}
      >
        <Upload size={13} />
        Upload .pem
      </button>
    </>
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
