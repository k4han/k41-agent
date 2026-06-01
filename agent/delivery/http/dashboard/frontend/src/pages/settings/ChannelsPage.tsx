import {
  createEffect,
  createMemo,
  createSignal,
  For,
  JSX,
  onMount,
  Show,
} from "solid-js";
import { useSearchParams } from "@solidjs/router";
import {
  CheckCircle2,
  ChevronDown,
  Copy,
  Fingerprint,
  Link2,
  Play,
  PlugZap,
  RotateCcw,
  Save,
  Settings as SettingsIcon,
  StopCircle,
  Trash2,
  TriangleAlert,
  Webhook,
  XCircle,
} from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { Identity, SettingInfo, SourceValue } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  sameValue,
  typedValue,
} from "./shared";

type ChannelRuntime = {
  name: string;
  status: string;
  error: string | null;
  registered: boolean;
};

type ChannelsPayload = {
  identities: Identity[];
  settings: Record<string, SettingInfo>;
  by_channel: Record<string, Record<string, SettingInfo>>;
  settings_sources: Record<string, SourceValue[]>;
  runtimes: Record<string, ChannelRuntime>;
};

type TestOutcome = {
  ok: boolean;
  message: string;
  latency_ms?: number;
  details?: Record<string, unknown>;
};

type PairingResponse = {
  code: string;
  user_id: string;
};

type TabKey = "channels" | "pairing";

type DrawerSection = {
  id: string;
  title: string;
  subtitle?: string;
  icon?: JSX.Element;
  fields: string[];
  defaultCollapsed?: boolean;
  visibleWhen?: (
    valueFor: (suffix: string) => unknown,
  ) => boolean;
  helper?: (
    valueFor: (suffix: string) => unknown,
  ) => JSX.Element | null;
};

type ChannelDefinition = {
  name: string;
  title: string;
  summary: string;
  tagline: string;
  brandIcon: () => JSX.Element;
  sections: DrawerSection[];
};

const CHANNEL_DEFS: ChannelDefinition[] = [
  {
    name: "telegram",
    title: "Telegram",
    summary: "Chat with your agents from Telegram private chats and groups.",
    tagline: "Bot platform",
    brandIcon: TelegramIcon,
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        subtitle: "Bot credentials from @BotFather",
        fields: ["bot_token"],
      },
      {
        id: "agents",
        title: "Agents",
        subtitle: "Default agent and command routing",
        fields: ["default_agent", "code_agent", "research_agent"],
      },
      {
        id: "webhook",
        title: "Update Mode",
        subtitle: "Polling or webhook delivery",
        icon: <Webhook size={14} />,
        fields: ["update_mode", "webhook_url", "webhook_secret"],
        helper: (valueFor) => {
          const mode = String(valueFor("update_mode") ?? "polling").toLowerCase();
          if (mode !== "webhook") {
            return null;
          }
          const url = String(valueFor("webhook_url") ?? "");
          if (!url) {
            return (
              <div class="channel-webhook-helper">
                <span class="channel-webhook-helper-label">Webhook tip</span>
                <span class="hint">
                  Set the webhook URL above, then register it from Telegram via{" "}
                  <span class="mono">setWebhook</span>.
                </span>
              </div>
            );
          }
          return (
            <div class="channel-webhook-helper">
              <span class="channel-webhook-helper-label">Webhook target</span>
              <div class="channel-webhook-helper-value">
                <code>{url}</code>
                <button
                  class="btn btn-sm"
                  type="button"
                  onClick={() => void writeToClipboard(url).catch(() => {})}
                >
                  <Copy size={13} />
                  Copy
                </button>
              </div>
            </div>
          );
        },
      },
    ],
  },
  {
    name: "discord",
    title: "Discord",
    summary: "Run agents inside Discord servers and DMs.",
    tagline: "Bot platform",
    brandIcon: DiscordIcon,
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        subtitle: "Bot token from Discord Developer Portal",
        fields: ["bot_token"],
      },
      {
        id: "agents",
        title: "Agents",
        subtitle: "Default agent and command routing",
        fields: ["default_agent", "code_agent", "research_agent"],
      },
    ],
  },
];

const ENABLED_FIELD = "enabled";

export function ChannelsPage() {
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const [data, setData] = createSignal<ChannelsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [drawerChannel, setDrawerChannel] = createSignal<string | null>(null);
  const [collapsed, setCollapsed] = createSignal<Record<string, boolean>>({});
  const [busy, setBusy] = createSignal<Record<string, string>>({});
  const [testResults, setTestResults] = createSignal<Record<string, TestOutcome>>({});
  const [stopTarget, setStopTarget] = createSignal<string | null>(null);
  const [pairing, setPairing] = createSignal<PairingResponse | null>(null);
  const [creatingPairingCode, setCreatingPairingCode] = createSignal(false);
  const [unpairTarget, setUnpairTarget] = createSignal<Identity | null>(null);
  const { showToast } = useToast();

  const tab = (): TabKey => {
    const value = searchParams.tab;
    if (value === "pairing") {
      return "pairing";
    }
    return "channels";
  };

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<ChannelsPayload>(
        "/dashboard-api/channels",
      );
      setData(payload);
      setDrafts(
        Object.fromEntries(
          Object.entries(payload.settings).map(([key, info]) => [key, info.value]),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    }
  };

  onMount(load);

  createEffect(() => {
    const payload = data();
    if (!payload) {
      return;
    }
    setCollapsed((current) => {
      const next = { ...current };
      for (const channel of CHANNEL_DEFS) {
        const channelSettings = payload.by_channel[channel.name] || {};
        for (const section of channel.sections) {
          const id = `${channel.name}:${section.id}`;
          if (id in next) {
            continue;
          }
          const hasValue = section.fields.some((field) => {
            const info = channelSettings[settingKey(channel.name, field)];
            return info?.value && String(info.value).length > 0;
          });
          next[id] = section.defaultCollapsed ?? (!hasValue && section.id !== "authentication");
        }
      }
      return next;
    });
  });

  const settingKey = (channel: string, suffix: string) =>
    `channels.${channel}.${suffix}`;

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const restoreDraft = (key: string) => {
    const payload = data();
    const info = payload?.settings[key];
    if (!info) {
      showToast("No server value to restore.", "warning");
      return;
    }
    setDraft(key, info.value ?? null);
    showToast("Change reverted.", "warning");
  };

  const changesForChannel = (channel: string): PendingChange[] => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const prefix = `channels.${channel}.`;
    return Object.entries(payload.settings)
      .filter(([key]) => key.startsWith(prefix))
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  };

  const pendingByChannel = createMemo<Record<string, PendingChange[]>>(() => {
    return Object.fromEntries(
      CHANNEL_DEFS.map((channel) => [channel.name, changesForChannel(channel.name)]),
    );
  });

  const runtimeFor = (channel: string): ChannelRuntime => {
    const runtime = data()?.runtimes?.[channel];
    if (runtime) {
      return runtime;
    }
    return {
      name: channel,
      status: "unregistered",
      error: null,
      registered: false,
    };
  };

  const draftValueFor = (channel: string, suffix: string): unknown => {
    const key = settingKey(channel, suffix);
    const current = drafts();
    if (Object.prototype.hasOwnProperty.call(current, key)) {
      return current[key];
    }
    return data()?.settings[key]?.value ?? null;
  };

  const isChannelConfigured = (channel: string): boolean => {
    const payload = data();
    if (!payload) {
      return false;
    }
    const settings = payload.by_channel[channel] || {};
    const tokenKeys = Object.keys(settings).filter((key) => {
      const info = settings[key];
      return info?.input_type === "password";
    });
    if (!tokenKeys.length) {
      return Object.values(settings).some((info) => {
        const value = info?.value;
        return value !== null && value !== "" && value !== undefined;
      });
    }
    return tokenKeys.some((key) => {
      const value = settings[key]?.value;
      return value !== null && value !== "" && value !== undefined;
    });
  };

  const setBusyState = (channel: string, action: string | null) => {
    setBusy((current) => {
      const next = { ...current };
      if (action) {
        next[channel] = action;
      } else {
        delete next[channel];
      }
      return next;
    });
  };

  const toggleEnabled = async (channel: string, next: boolean) => {
    const key = settingKey(channel, ENABLED_FIELD);
    setDraft(key, next);
    setBusyState(channel, "toggle");
    try {
      await putJson("/settings", { values: { [key]: next } });
      showToast(`${titleOf(channel)} ${next ? "enabled" : "disabled"}.`);
      await load();
    } catch (err) {
      const payload = data();
      const previous = payload?.settings[key]?.value ?? null;
      setDraft(key, previous);
      showToast(
        err instanceof Error ? err.message : "Failed to update setting",
        "error",
      );
    } finally {
      setBusyState(channel, null);
    }
  };

  const startChannel = async (channel: string) => {
    setBusyState(channel, "start");
    try {
      await postJson(`/services/${channel}/start`);
      showToast(`${titleOf(channel)} starting.`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to start channel",
        "error",
      );
    } finally {
      setBusyState(channel, null);
    }
  };

  const stopChannel = async (channel: string) => {
    setBusyState(channel, "stop");
    try {
      await postJson(`/services/${channel}/stop`);
      showToast(`${titleOf(channel)} stopped.`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to stop channel",
        "error",
      );
    } finally {
      setBusyState(channel, null);
    }
  };

  const testChannel = async (channel: string) => {
    setBusyState(channel, "test");
    try {
      const result = await postJson<TestOutcome>(`/services/${channel}/test`);
      setTestResults((current) => ({ ...current, [channel]: result }));
      if (result.ok) {
        showToast("Connection successful.");
      } else {
        showToast(`Connection failed: ${result.message}`, "error");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Test failed";
      setTestResults((current) => ({
        ...current,
        [channel]: { ok: false, message },
      }));
      showToast(message, "error");
    } finally {
      setBusyState(channel, null);
    }
  };

  const saveChannel = async (channel: string) => {
    const changes = pendingByChannel()[channel] || [];
    if (!changes.length) {
      return;
    }
    setBusyState(channel, "save");
    try {
      const values = Object.fromEntries(
        changes.map((change) => [change.key, change.newValue]),
      );
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} ${titleOf(channel)} setting(s).`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save settings",
        "error",
      );
    } finally {
      setBusyState(channel, null);
    }
  };

  const toggleSection = (channel: string, sectionId: string) => {
    const id = `${channel}:${sectionId}`;
    setCollapsed((current) => ({ ...current, [id]: !current[id] }));
  };

  const createPairingCode = async () => {
    setCreatingPairingCode(true);
    try {
      const response = await postJson<PairingResponse>("/channels/pair");
      setPairing(response);
      showToast("Pairing code created.");
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to create pairing code",
        "error",
      );
    } finally {
      setCreatingPairingCode(false);
    }
  };

  const copyPairingCode = async () => {
    const item = pairing();
    if (!item) {
      return;
    }
    try {
      await writeToClipboard(item.code);
      showToast("Pairing code copied.");
    } catch {
      showToast("Failed to copy pairing code.", "error");
    }
  };

  const requestUnpair = (identity: Identity) => {
    if (identity.id === null) {
      return;
    }
    setUnpairTarget(identity);
  };

  const confirmUnpair = async () => {
    const identity = unpairTarget();
    if (!identity || identity.id === null) {
      return;
    }
    try {
      await deleteJson(`/channels/identities/${identity.id}`);
      showToast("Identity unpaired.");
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to unpair identity",
        "error",
      );
    } finally {
      setUnpairTarget(null);
    }
  };

  return (
    <SettingsLayout
      title="Channels"
      subtitle="Manage chat integrations and runtime status."
      breadcrumbLabel="Channels"
      contentWidth="wide"
    >
      <div class="tab-bar">
        <button
          class={`btn btn-sm ${tab() === "channels" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "channels" })}
        >
          Channels
        </button>
        <button
          class={`btn btn-sm ${tab() === "pairing" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "pairing" })}
        >
          Pairing
        </button>
      </div>

      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <Show when={tab() === "channels"}>
              <div class="channels-grid">
                <For each={CHANNEL_DEFS}>
                  {(channel) => (
                    <ChannelCard
                      channel={channel}
                      runtime={runtimeFor(channel.name)}
                      enabled={Boolean(draftValueFor(channel.name, ENABLED_FIELD))}
                      configured={isChannelConfigured(channel.name)}
                      pendingCount={(pendingByChannel()[channel.name] || []).length}
                      busy={busy()[channel.name] || null}
                      testResult={testResults()[channel.name] || null}
                      paired={countPairedFor(payload, channel.name)}
                      onToggle={(value) => void toggleEnabled(channel.name, value)}
                      onStart={() => void startChannel(channel.name)}
                      onStopRequest={() => setStopTarget(channel.name)}
                      onTest={() => void testChannel(channel.name)}
                      onConfigure={() => setDrawerChannel(channel.name)}
                    />
                  )}
                </For>
              </div>
            </Show>

            <Show when={tab() === "pairing"}>
              <PairingPanel
                pairing={pairing()}
                creating={creatingPairingCode()}
                onCreate={() => void createPairingCode()}
                onCopy={() => void copyPairingCode()}
              />

              <PairedIdentitiesTable
                identities={payload.identities}
                onUnpair={requestUnpair}
              />
            </Show>
          </div>
        )}
      </DataGate>

      <Show when={drawerChannel()}>
        {(name) => {
          const def = () => CHANNEL_DEFS.find((c) => c.name === name())!;
          const channelPayload = () => data()?.by_channel[name()] || {};
          const pending = () => pendingByChannel()[name()] || [];
          return (
            <Dialog
              open={true}
              title={`${def().title} settings`}
              wide
              onClose={() => setDrawerChannel(null)}
              footer={
                <div class="row-wrap" style={{ "justify-content": "space-between", width: "100%" }}>
                  <div class="row-wrap">
                    <button
                      class="btn"
                      type="button"
                      disabled={busy()[name()] === "test"}
                      onClick={() => void testChannel(name())}
                    >
                      <PlugZap size={14} />
                      {busy()[name()] === "test" ? "Testing..." : "Test connection"}
                    </button>
                    <Show when={testResults()[name()]}>
                      {(result) => (
                        <TestFeedback channel={name()} result={result()} />
                      )}
                    </Show>
                  </div>
                  <div class="row-wrap">
                    <button class="btn" type="button" onClick={() => setDrawerChannel(null)}>
                      Close
                    </button>
                    <button
                      class="btn btn-primary"
                      type="button"
                      disabled={pending().length === 0 || busy()[name()] === "save"}
                      onClick={() => void saveChannel(name())}
                    >
                      <Save size={14} />
                      {busy()[name()] === "save"
                        ? "Saving..."
                        : `Save ${pending().length ? `(${pending().length})` : ""}`}
                    </button>
                  </div>
                </div>
              }
            >
              <div class="channel-drawer-sections">
                <For each={def().sections}>
                  {(section) => {
                    const visible = () =>
                      section.visibleWhen?.((suffix) =>
                        draftValueFor(name(), suffix),
                      ) ?? true;
                    const id = `${name()}:${section.id}`;
                    return (
                      <Show when={visible()}>
                        <DrawerSection
                          channel={name()}
                          settings={channelPayload()}
                          section={section}
                          drafts={drafts()}
                          pending={pending()}
                          collapsed={Boolean(collapsed()[id])}
                          onToggle={() => toggleSection(name(), section.id)}
                          onChange={setDraft}
                          onRestore={restoreDraft}
                          helper={section.helper?.((suffix) =>
                            draftValueFor(name(), suffix),
                          ) ?? null}
                        />
                      </Show>
                    );
                  }}
                </For>
              </div>
            </Dialog>
          );
        }}
      </Show>

      <ConfirmDialog
        open={stopTarget() !== null}
        title="Stop channel"
        message={
          <p>
            Stop <span class="mono">{stopTarget()}</span>? Incoming messages will
            be ignored until the channel is started again.
          </p>
        }
        confirmLabel="Stop"
        confirmVariant="danger"
        loading={busy()[stopTarget() || ""] === "stop"}
        onClose={() => setStopTarget(null)}
        onConfirm={() => {
          const target = stopTarget();
          if (!target) {
            return;
          }
          setStopTarget(null);
          void stopChannel(target);
        }}
      />

      <ConfirmDialog
        open={unpairTarget() !== null}
        title="Unpair identity"
        message={
          <p>
            Unpair{" "}
            <span class="mono">
              {unpairTarget()?.platform}:{unpairTarget()?.external_id}
            </span>
            ? The connected account will lose access until paired again.
          </p>
        }
        confirmLabel="Unpair"
        confirmVariant="danger"
        onClose={() => setUnpairTarget(null)}
        onConfirm={() => void confirmUnpair()}
      />
    </SettingsLayout>
  );
}

function PairingPanel(props: {
  pairing: PairingResponse | null;
  creating: boolean;
  onCreate: () => void;
  onCopy: () => void;
}) {
  return (
    <section class="panel">
      <div class="panel-header pairing-panel-header">
        <div>
          <div class="panel-title">Pairing</div>
          <div class="hint">Create a one-time code to link Telegram or Discord identities.</div>
        </div>
        <button
          class="btn btn-primary"
          type="button"
          disabled={props.creating}
          onClick={props.onCreate}
        >
          <Link2 size={14} />
          {props.creating ? "Creating..." : "New Pairing Code"}
        </button>
      </div>
      <div class="panel-body">
        <Show
          when={props.pairing}
          fallback={
            <div class="row-wrap" style={{ gap: "10px", "align-items": "center" }}>
              <Fingerprint size={18} />
              <div>
                <div class="setting-title">No active code</div>
                <div class="hint">
                  Generate a pairing code, then send <span class="mono">/pair XXXX-XXXX</span> from
                  Telegram or Discord.
                </div>
              </div>
            </div>
          }
        >
          {(item) => (
            <section class="pairing-code-display">
              <div class="channel-card-meta-label">Pairing Code</div>
              <div class="pairing-code-value">
                <span class="chip">{item().code}</span>
                <button class="btn btn-sm" type="button" onClick={props.onCopy}>
                  <Copy size={13} />
                  Copy
                </button>
                <span class="hint">
                  User ID <span class="mono">{item().user_id}</span>. The code expires in 24 hours.
                  Send <span class="mono">/pair {item().code}</span> from Telegram or Discord.
                </span>
              </div>
            </section>
          )}
        </Show>
      </div>
    </section>
  );
}

function PairedIdentitiesTable(props: {
  identities: Identity[];
  onUnpair: (identity: Identity) => void;
}) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Paired Identities</div>
        <span class="hint">{props.identities.length} linked</span>
      </div>
      <DashboardTable
        columns={[
          { header: "Platform" },
          { header: "External ID" },
          { header: "User ID" },
          { header: "Linked Since" },
          { header: "Actions" },
        ]}
        rows={props.identities}
        emptyMessage="No paired identities yet."
      >
        {(identity) => (
          <tr>
            <td>
              <span class="badge">{identity.platform}</span>
            </td>
            <td class="mono">{identity.external_id}</td>
            <td class="mono">{identity.user_id ?? "-"}</td>
            <td class="mono hint">{formatDate(identity.created_at)}</td>
            <td>
              <button
                class="btn btn-sm btn-danger"
                type="button"
                onClick={() => props.onUnpair(identity)}
              >
                <Trash2 size={13} />
                Unpair
              </button>
            </td>
          </tr>
        )}
      </DashboardTable>
    </section>
  );
}

function ChannelCard(props: {
  channel: ChannelDefinition;
  runtime: ChannelRuntime;
  enabled: boolean;
  configured: boolean;
  pendingCount: number;
  busy: string | null;
  testResult: TestOutcome | null;
  paired: number;
  onToggle: (value: boolean) => void;
  onStart: () => void;
  onStopRequest: () => void;
  onTest: () => void;
  onConfigure: () => void;
}) {
  const isRunning = () =>
    props.runtime.status === "running" || props.runtime.status === "starting";
  const statusLabel = () => formatStatus(props.runtime);
  return (
    <article class="channel-card">
      <header class="channel-card-header">
        <div
          class="channel-brand-icon"
          data-brand={props.channel.name}
          aria-hidden="true"
        >
          {props.channel.brandIcon()}
        </div>
        <div class="channel-card-title">
          <div class="channel-card-name">{props.channel.title}</div>
          <div class="channel-card-subtitle">{props.channel.tagline}</div>
        </div>
        <span
          class="channel-status-pill"
          data-state={props.runtime.status}
          title={props.runtime.error || statusLabel()}
        >
          <span class="channel-status-dot" />
          {statusLabel()}
        </span>
      </header>

      <div class="channel-card-body">
        <div class="hint">{props.channel.summary}</div>

        <Show when={props.runtime.error}>
          <div class="channel-card-error">
            <TriangleAlert size={14} />
            <div>
              <strong>Runtime error</strong>
              <div class="mono" style={{ "font-size": "11px" }}>
                {props.runtime.error}
              </div>
            </div>
          </div>
        </Show>

        <Show when={!props.configured && !props.runtime.error}>
          <div class="channel-card-empty-hint">
            <TriangleAlert size={14} />
            <div>
              Add credentials in <strong>Configure</strong> to enable this channel.
            </div>
          </div>
        </Show>

        <div class="channel-card-meta">
          <div class="channel-card-meta-row">
            <span class="channel-card-meta-label">Paired</span>
            <span class="mono">{props.paired}</span>
            <span class="channel-card-meta-label" style={{ "margin-left": "auto" }}>
              Pending
            </span>
            <span class={`mono ${props.pendingCount ? "" : "muted"}`}>
              {props.pendingCount}
            </span>
          </div>
        </div>
      </div>

      <footer class="channel-card-footer">
        <label class="channel-toggle-cell">
          <button
            class={`toggle-control ${props.enabled ? "active" : ""}`}
            type="button"
            role="switch"
            aria-checked={props.enabled}
            disabled={props.busy === "toggle"}
            onClick={() => props.onToggle(!props.enabled)}
          >
            <span class="toggle-track">
              <span class="toggle-thumb" />
            </span>
            <span class="toggle-label">Enabled</span>
          </button>
          <span>{props.enabled ? "Enabled" : "Disabled"}</span>
        </label>

        <div class="channel-card-actions">
          <Show
            when={isRunning()}
            fallback={
              <button
                class="btn btn-sm"
                type="button"
                disabled={props.busy === "start" || !props.configured}
                title={
                  props.configured
                    ? "Start channel"
                    : "Configure credentials first"
                }
                onClick={props.onStart}
              >
                <Play size={13} />
                {props.busy === "start" ? "Starting..." : "Start"}
              </button>
            }
          >
            <button
              class="btn btn-sm btn-warning"
              type="button"
              disabled={props.busy === "stop"}
              onClick={props.onStopRequest}
            >
              <StopCircle size={13} />
              {props.busy === "stop" ? "Stopping..." : "Stop"}
            </button>
          </Show>
          <button
            class="btn btn-sm"
            type="button"
            disabled={props.busy === "test" || !props.configured}
            onClick={props.onTest}
            title={
              props.configured
                ? "Verify credentials with provider API"
                : "Configure credentials first"
            }
          >
            <PlugZap size={13} />
            {props.busy === "test" ? "Testing..." : "Test"}
          </button>
          <button
            class="btn btn-sm btn-primary"
            type="button"
            onClick={props.onConfigure}
          >
            <SettingsIcon size={13} />
            Configure
            <Show when={props.pendingCount > 0}>
              <span class="badge badge-warning" style={{ "margin-left": "4px" }}>
                {props.pendingCount}
              </span>
            </Show>
          </button>
        </div>
      </footer>

      <Show when={props.testResult}>
        {(result) => (
          <TestFeedback channel={props.channel.name} result={result()} />
        )}
      </Show>
    </article>
  );
}

function DrawerSection(props: {
  channel: string;
  settings: Record<string, SettingInfo>;
  section: DrawerSection;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  collapsed: boolean;
  helper: JSX.Element | null;
  onToggle: () => void;
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
}) {
  const fieldEntries = createMemo(() => {
    return props.section.fields
      .map((suffix) => {
        const key = `channels.${props.channel}.${suffix}`;
        const info = props.settings[key];
        return info ? { key, info } : null;
      })
      .filter((entry): entry is { key: string; info: SettingInfo } => entry !== null);
  });

  const dirtyCount = createMemo(() => {
    const keys = new Set(fieldEntries().map((entry) => entry.key));
    return props.pending.filter((change) => keys.has(change.key)).length;
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
            <Show when={dirtyCount() > 0}>
              <span class="badge badge-warning">{dirtyCount()}</span>
            </Show>
          </div>
          <Show when={props.section.subtitle}>
            <div class="channel-drawer-section-subtitle">
              {props.section.subtitle}
            </div>
          </Show>
        </div>
        <span class="channel-drawer-section-caret" aria-hidden="true">
          <ChevronDown size={16} />
        </span>
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
            return (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={dirty()}
                showDescription
                onChange={(value) => props.onChange(entry.key, value)}
                onRestore={() => props.onRestore(entry.key)}
              />
            );
          }}
        </For>
        <Show when={props.helper}>
          <div style={{ padding: "12px 14px" }}>{props.helper}</div>
        </Show>
      </div>
    </section>
  );
}

function TestFeedback(props: { channel: string; result: TestOutcome }) {
  return (
    <div
      class="channel-test-feedback"
      data-state={props.result.ok ? "ok" : "error"}
    >
      <div class="channel-test-feedback-title">
        <Show when={props.result.ok} fallback={<XCircle size={14} />}>
          <CheckCircle2 size={14} />
        </Show>
        <span>{props.result.message}</span>
      </div>
      <Show when={props.result.latency_ms !== undefined || props.result.details}>
        <div class="channel-test-feedback-details">
          <Show when={props.result.latency_ms !== undefined}>
            <span>
              Latency <strong>{props.result.latency_ms}ms</strong>
            </span>
          </Show>
          <Show when={props.result.details}>
            <For
              each={Object.entries(props.result.details || {}).filter(
                ([, value]) =>
                  value !== null && value !== undefined && value !== "",
              )}
            >
              {([label, value]) => (
                <span>
                  {label} <strong>{String(value)}</strong>
                </span>
              )}
            </For>
          </Show>
        </div>
      </Show>
    </div>
  );
}

function formatStatus(runtime: ChannelRuntime): string {
  if (!runtime.registered) {
    return "Not registered";
  }
  switch (runtime.status) {
    case "running":
      return "Running";
    case "stopped":
      return "Stopped";
    case "starting":
      return "Starting";
    case "stopping":
      return "Stopping";
    case "error":
      return "Error";
    default:
      return runtime.status;
  }
}

function titleOf(channel: string): string {
  const def = CHANNEL_DEFS.find((item) => item.name === channel);
  return def?.title ?? channel;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function countPairedFor(
  payload: ChannelsPayload | undefined,
  channel: string,
): number {
  if (!payload?.identities) {
    return 0;
  }
  return payload.identities.filter((identity) => identity.platform === channel)
    .length;
}

function TelegramIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M21.5 4.3 18.4 19.6c-.2 1.1-.9 1.4-1.8.9l-5-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-4.7 8.5-7.6c.4-.3-.1-.5-.6-.2L5.9 13.4 1.4 12c-1-.3-1-1 .2-1.5L20 4c.9-.3 1.7.2 1.5 1.3z" />
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M20.3 4.4A19.7 19.7 0 0 0 15.5 3l-.2.4a17.8 17.8 0 0 0-6.6 0L8.5 3a19.7 19.7 0 0 0-4.8 1.4C1.4 8 .8 11.4 1 14.8a19.9 19.9 0 0 0 5.9 3l1.1-1.7a12.7 12.7 0 0 1-2-1c.2-.1.4-.3.5-.4 3.9 1.8 8.1 1.8 11.9 0 .2.1.3.3.5.4-.6.4-1.3.7-2 1l1.1 1.7a19.9 19.9 0 0 0 6-3c.3-3.9-.4-7.3-2.7-10.4zM8.4 13.1c-1.2 0-2.1-1.1-2.1-2.4 0-1.3.9-2.4 2.1-2.4 1.2 0 2.2 1.1 2.1 2.4 0 1.3-.9 2.4-2.1 2.4zm7.2 0c-1.2 0-2.1-1.1-2.1-2.4 0-1.3.9-2.4 2.1-2.4 1.2 0 2.2 1.1 2.1 2.4 0 1.3-.9 2.4-2.1 2.4z" />
    </svg>
  );
}
