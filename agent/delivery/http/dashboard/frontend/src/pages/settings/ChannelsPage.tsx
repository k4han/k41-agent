import {
  createEffect,
  createMemo,
  createSignal,
  For,
  JSX,
  Show,
} from "solid-js";
import { useNavigate, useParams, useSearchParams } from "@solidjs/router";
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  Copy,
  Fingerprint,
  Link2,
  Network,
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
import { SelectControl, type SelectControlOption } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getChannels } from "@/lib/catalogStore";
import { getChannelIcon } from "@/lib/iconRegistry";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { writeToClipboard } from "@/lib/utils";
import type {
  ChannelCatalogItem,
  Identity,
  SettingInfo,
  SourceValue,
} from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  sameValue,
  SettingsTabBar,
  type SettingsTabItem,
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

const TAB_ITEMS: ReadonlyArray<SettingsTabItem<TabKey>> = [
  { value: "channels", label: "Channels", icon: () => <Network size={13} /> },
  { value: "pairing", label: "Pairing", icon: () => <Fingerprint size={13} /> },
];

const AGENT_FIELD_NAMES = new Set(["default_agent", "code_agent", "research_agent"]);
const isAgentField = (name: string) => AGENT_FIELD_NAMES.has(name) || name.endsWith("_agent");

type AgentCardsPayload = {
  agent_names: string[];
  cards: Array<{ name: string; display_name?: string }>;
};

type DrawerSection = {
  id: string;
  title: string;
  icon?: () => JSX.Element;
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
  sections: DrawerSection[];
};

const CHANNEL_DEFS: ChannelDefinition[] = [
  {
    name: "telegram",
    title: "Telegram",
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        fields: ["bot_token"],
      },
      {
        id: "agents",
        title: "Agents",
        fields: ["default_agent", "code_agent", "research_agent"],
      },
      {
        id: "webhook",
        title: "Update Mode",
        icon: () => <Webhook size={14} />,
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
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        fields: ["bot_token"],
      },
      {
        id: "agents",
        title: "Agents",
        fields: ["default_agent", "code_agent", "research_agent"],
      },
    ],
  },
];

const ENABLED_FIELD = "enabled";

function channelDefinitionFromCatalog(channel: ChannelCatalogItem): ChannelDefinition {
  const settings = channel.settings ?? [];
  const sections = channel.sections?.length
    ? channel.sections
    : [{ id: "general", title: "Settings" }];

  const drawerSections = sections
    .map((section): DrawerSection => ({
      id: section.id,
      title: section.title,
      fields: settings
        .filter(
          (field) =>
            field.section === section.id && field.name !== ENABLED_FIELD,
        )
        .map((field) => field.name),
      defaultCollapsed: section.default_collapsed,
      helper: channel.name === "telegram" && section.id === "webhook"
        ? telegramWebhookHelper
        : undefined,
    }))
    .filter((section) => section.fields.length > 0 || section.helper);

  const knownSectionIds = new Set(sections.map((section) => section.id));
  const extraFields = settings
    .filter(
      (field) =>
        field.name !== ENABLED_FIELD && !knownSectionIds.has(field.section),
    )
    .map((field) => field.name);
  if (extraFields.length) {
    drawerSections.push({
      id: "general",
      title: "Settings",
      fields: extraFields,
    });
  }

  return {
    name: channel.name,
    title: channel.title || titleOf(channel.name),
    sections: drawerSections,
  };
}

function telegramWebhookHelper(
  valueFor: (suffix: string) => unknown,
): JSX.Element | null {
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
}

export function ChannelsPage() {
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const [data, setData] = createSignal<ChannelsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [selectedChannel, setSelectedChannel] = createSignal<string>("telegram");
  const [busy, setBusy] = createSignal<Record<string, string>>({});
  const [testResults, setTestResults] = createSignal<Record<string, TestOutcome>>({});
  const [stopTarget, setStopTarget] = createSignal<string | null>(null);
  const [pairing, setPairing] = createSignal<PairingResponse | null>(null);
  const [creatingPairingCode, setCreatingPairingCode] = createSignal(false);
  const [unpairTarget, setUnpairTarget] = createSignal<Identity | null>(null);
  const [agentNames, setAgentNames] = createSignal<string[]>([]);
  const { showToast } = useToast();

  const channelDefs = createMemo<ChannelDefinition[]>(() => {
    const catalogChannels = getChannels();
    if (!catalogChannels.length) {
      return CHANNEL_DEFS;
    }
    return catalogChannels.map(channelDefinitionFromCatalog);
  });

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
      const [payload, agentsPayload] = await Promise.all([
        apiFetch<ChannelsPayload>("/dashboard-api/channels"),
        apiFetch<AgentCardsPayload>("/dashboard-api/agents/cards").catch(() => ({ agent_names: [], cards: [] as any[] })),
      ]);
      setData(payload);
      setDrafts(
        Object.fromEntries(
          Object.entries(payload.settings).map(([key, info]) => [key, info.value]),
        ),
      );
      setAgentNames(agentsPayload.agent_names || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    }
  };

  useCatalogAndLoad(load);

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
      channelDefs().map((channel) => [channel.name, changesForChannel(channel.name)]),
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

  const params = useParams<{ channelName?: string }>();
  const navigate = useNavigate();

  return (
    <>
      <Show
      when={params.channelName}
      fallback={
        <SettingsLayout
          title="Channels"
          breadcrumbLabel="Channels"
          contentWidth="wide"
        >
          <SettingsTabBar
            items={TAB_ITEMS}
            value={tab()}
            ariaLabel="Channel section"
            onChange={(value) => setSearchParams({ tab: value })}
          />

          <DataGate data={data()} error={error()} onRetry={load}>
            {(payload) => (
              <div class="stack">
                <Show when={tab() === "channels"}>
                  <div class="stack" style={{ gap: "24px" }}>
                    <div>
                      <div class="settings-section-title" style={{ "margin-bottom": "12px" }}>
                        Select Channel
                      </div>
                      <div class="channels-grid">
                        <For each={channelDefs()}>
                          {(channel) => (
                            <ChannelCard
                              channel={channel}
                              runtime={runtimeFor(channel.name)}
                              enabled={Boolean(draftValueFor(channel.name, ENABLED_FIELD))}
                              configured={isChannelConfigured(channel.name)}
                              busy={busy()[channel.name] || null}
                              testResult={testResults()[channel.name] || null}
                              paired={countPairedFor(payload, channel.name)}
                              onToggle={(value) => void toggleEnabled(channel.name, value)}
                              onStart={() => void startChannel(channel.name)}
                              onStopRequest={() => setStopTarget(channel.name)}
                              onTest={() => void testChannel(channel.name)}
                              onConfigure={() => navigate(`/settings/channels/${channel.name}`)}
                            />
                          )}
                        </For>
                      </div>
                    </div>
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
        </SettingsLayout>
      }
    >
      {(channelName) => {
        const def = () => channelDefs().find((c) => c.name === channelName()) ?? null;
        const name = () => channelName();
        const channelPayload = () => data()?.by_channel[name()] || {};
        const pending = () => pendingByChannel()[name()] || [];
        const runtime = () => runtimeFor(name());
        const isRunning = () => runtime().status === "running";

        return (
          <SettingsLayout
            title={def() ? `${def()!.title} Settings` : "Channel Settings"}
            breadcrumbSegments={[
              { label: "Channels", href: "/settings/channels" },
              { label: def()?.title || name() },
            ]}
            contentWidth="wide"
            actions={
              <div class="row-wrap" style={{ gap: "8px" }}>
                <button
                  class="btn btn-sm"
                  type="button"
                  onClick={() => navigate("/settings/channels")}
                >
                  <ArrowLeft size={14} />
                  Back to Channels
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  disabled={busy()[name()] === "test"}
                  onClick={() => void testChannel(name())}
                >
                  <PlugZap size={14} />
                  {busy()[name()] === "test" ? "Testing..." : "Test connection"}
                </button>
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  disabled={pending().length === 0 || busy()[name()] === "save"}
                  onClick={() => void saveChannel(name())}
                >
                  <Save size={14} />
                  {busy()[name()] === "save"
                    ? "Saving..."
                    : `Save changes ${pending().length ? `(${pending().length})` : ""}`}
                </button>
              </div>
            }
          >
            <DataGate data={data()} error={error()} onRetry={load}>
              {() => (
                <Show
                  when={def()}
                  fallback={
                    <div class="panel empty" style={{ padding: "32px", "text-align": "center" }}>
                      <h3>Channel not found</h3>
                      <button
                        class="btn"
                        style={{ "margin-top": "12px" }}
                        type="button"
                        onClick={() => navigate("/settings/channels")}
                      >
                        <ArrowLeft size={14} />
                        Back to Channels
                      </button>
                    </div>
                  }
                >
                  <div class="stack" style={{ gap: "20px" }}>
                    {/* Overview Card */}
                    <div class="channel-config-card">
                      <div class="channel-config-header" style={{ "border-bottom": "none", padding: "0" }}>
                        <div class="channel-config-header-left">
                          <div class="channel-brand-icon" data-channel={name()} aria-hidden="true">
                            {getChannelIcon(name())()}
                          </div>
                          <div>
                            <h2 class="channel-config-title">{def()!.title}</h2>
                          </div>
                        </div>
                        <div class="row-wrap channel-config-header-actions">
                          <label class="channel-toggle-cell">
                            <button
                              class={`toggle-control ${Boolean(draftValueFor(name(), ENABLED_FIELD)) ? "active" : ""}`}
                              type="button"
                              role="switch"
                              aria-checked={Boolean(draftValueFor(name(), ENABLED_FIELD))}
                              disabled={busy()[name()] === "toggle"}
                              onClick={() => void toggleEnabled(name(), !draftValueFor(name(), ENABLED_FIELD))}
                            >
                              <span class="toggle-track">
                                <span class="toggle-thumb" />
                              </span>
                              <span class="toggle-label">
                                {draftValueFor(name(), ENABLED_FIELD) ? "Enabled" : "Disabled"}
                              </span>
                            </button>
                          </label>
                          <span
                            class="channel-status-pill"
                            data-state={runtime().status}
                            title={runtime().error || runtime().status}
                          >
                            <span class="channel-status-dot" />
                            {runtime().status}
                          </span>
                          <Show
                            when={isRunning()}
                            fallback={
                              <button
                                class="btn btn-sm"
                                type="button"
                                disabled={busy()[name()] === "start" || !isChannelConfigured(name())}
                                onClick={() => void startChannel(name())}
                              >
                                <Play size={13} />
                                {busy()[name()] === "start" ? "Starting..." : "Start"}
                              </button>
                            }
                          >
                            <button
                              class="btn btn-sm btn-warning"
                              type="button"
                              disabled={busy()[name()] === "stop"}
                              onClick={() => setStopTarget(name())}
                            >
                              <StopCircle size={13} />
                              {busy()[name()] === "stop" ? "Stopping..." : "Stop"}
                            </button>
                          </Show>
                        </div>
                      </div>
                    </div>

                    {/* Test Feedback if any */}
                    <Show when={testResults()[name()]}>
                      {(result) => (
                        <TestFeedback channel={name()} result={result()} />
                      )}
                    </Show>

                    {/* Section Groups */}
                    <div class="channel-sections-list">
                      <For each={def()!.sections}>
                        {(section) => {
                          const visible = () =>
                            section.visibleWhen?.((suffix) =>
                              draftValueFor(name(), suffix),
                            ) ?? true;
                          return (
                            <Show when={visible()}>
                              <ChannelSectionGroup
                                channel={name()}
                                settings={channelPayload()}
                                section={section}
                                drafts={drafts()}
                                pending={pending()}
                                agentNames={agentNames()}
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
                  </div>
                </Show>
              )}
            </DataGate>
          </SettingsLayout>
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
    </>
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
        <div class="panel-title">Pairing</div>
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
  active?: boolean;
  runtime: ChannelRuntime;
  enabled: boolean;
  configured: boolean;
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
    <article
      class={`channel-card ${props.active ? "channel-card--active" : ""}`}
      onClick={props.onConfigure}
      style={{ cursor: "pointer" }}
    >
      <header class="channel-card-header">
        <div
          class="channel-brand-icon"
          data-brand={props.channel.name}
          aria-hidden="true"
        >
          {getChannelIcon(props.channel.name)()}
        </div>
        <div class="channel-card-title">
          <div class="channel-card-name">{props.channel.title}</div>
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
          </div>
        </div>
      </div>

      <footer class="channel-card-footer">
        <label class="channel-toggle-cell" onClick={(e) => e.stopPropagation()}>
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
                onClick={(e) => {
                  e.stopPropagation();
                  props.onStart();
                }}
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
              onClick={(e) => {
                e.stopPropagation();
                props.onStopRequest();
              }}
            >
              <StopCircle size={13} />
              {props.busy === "stop" ? "Stopping..." : "Stop"}
            </button>
          </Show>
          <button
            class="btn btn-sm"
            type="button"
            disabled={props.busy === "test" || !props.configured}
            onClick={(e) => {
              e.stopPropagation();
              props.onTest();
            }}
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
            onClick={(e) => {
              e.stopPropagation();
              props.onConfigure();
            }}
          >
            <SettingsIcon size={13} />
            Configure
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

function ChannelSectionGroup(props: {
  channel: string;
  settings: Record<string, SettingInfo>;
  section: DrawerSection;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  agentNames: string[];
  helper: JSX.Element | null;
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
}) {
  const fieldEntries = createMemo(() => {
    return props.section.fields
      .map((suffix) => {
        const key = `channels.${props.channel}.${suffix}`;
        const info = props.settings[key];
        return info ? { key, info, suffix } : null;
      })
      .filter((entry): entry is { key: string; info: SettingInfo; suffix: string } => entry !== null);
  });

  return (
    <section class="channel-section-group">
      <div class="channel-section-header">
        <div class="channel-section-header-left">
          <Show when={props.section.icon}>{props.section.icon!()}</Show>
          <div>
            <div class="channel-section-title">
              {props.section.title}
            </div>
          </div>
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
              props.pending.some((change) => change.key === entry.key);
            const isAgent = isAgentField(entry.suffix);
            return (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={dirty()}
                actions={entry.suffix === "context_trim_threshold" ? (
                  <button
                    class="btn btn-ghost btn-sm"
                    type="button"
                    title="Reset to default (50,000 tokens)"
                    onClick={() => props.onChange(entry.key, null)}
                  >
                    Reset default
                  </button>
                ) : undefined}
                control={
                  isAgent ? (
                    <AgentNameSelect
                      value={String(props.drafts[entry.key] ?? "")}
                      agentNames={props.agentNames}
                      onChange={(value) => props.onChange(entry.key, value)}
                      ariaLabel={entry.info.label}
                    />
                  ) : entry.suffix === "update_mode" ? (
                    <SelectControl
                      value={String(props.drafts[entry.key] ?? "polling")}
                      options={[
                        { value: "polling", label: "polling" },
                        { value: "webhook", label: "webhook" },
                      ]}
                      onChange={(value) => props.onChange(entry.key, value)}
                      ariaLabel={entry.info.label}
                    />
                  ) : undefined
                }
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

function AgentNameSelect(props: {
  value: string;
  agentNames: string[];
  onChange: (value: string) => void;
  ariaLabel?: string;
}) {
  const options = createMemo<SelectControlOption[]>(() => {
    const names = new Set(props.agentNames);
    if (props.value) {
      names.add(props.value);
    }
    const sorted = [...names].sort((a, b) => a.localeCompare(b));
    const withEmpty: SelectControlOption[] = [
      { value: "", label: "— Use default —" },
      ...sorted.map((name) => ({ value: name, label: name })),
    ];
    return withEmpty;
  });

  return (
    <SelectControl
      value={props.value}
      options={options()}
      onChange={props.onChange}
      ariaLabel={props.ariaLabel || "Agent"}
      icon={<Bot size={14} />}
    />
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
  // The catalog is the source of truth for human-readable labels. The local
  // ``CHANNEL_DEFS`` table is only consulted when the catalog has no entry
  // for the channel (e.g. before the catalog request resolves on first load).
  const fromCatalog = getChannels().find((entry) => entry.name === channel)?.title;
  if (fromCatalog) {
    return fromCatalog;
  }
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
