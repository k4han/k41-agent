import "@/styles/notifications.css";

import { A } from "@solidjs/router";
import {
  Bell, Clock, Gauge, MessageCircleQuestionMark, MessageSquare, MessagesSquare,
  MousePointer2, Send, Timer, TriangleAlert, Users,
} from "lucide-solid";
import { batch, createMemo, createResource, createSignal, For, Show } from "solid-js";

import { apiFetch, putJson } from "@/lib/api";
import { SettingsLayout } from "./SettingsLayout";

const filters = [
  ["background_failure", "Background failures and crashes", "A background task fails or the agent crashes.", TriangleAlert],
  ["rate_limit", "Provider limits and quota", "A provider hits a rate limit or runs out of quota.", Gauge],
  ["ask_user", "Questions from the agent", "Kai pauses to ask for your input.", MessageCircleQuestionMark],
  ["browser_handoff", "Browser handoffs", "Kai needs you to take over the browser.", MousePointer2],
  ["interruption_timeout", "Expired requests", "An unanswered request reaches its deadline.", Clock],
] as const;
const channels = [
  ["telegram", "Telegram", Send],
  ["discord", "Discord", MessageSquare],
  ["zalo", "Zalo", MessagesSquare],
] as const;
const endpoint = "/dashboard-api/notifications";

type EventName = typeof filters[number][0];
type Settings = {
  timeout_minutes: number;
  dashboard_notify_delay_seconds: number;
  fallback_prompt: string;
  dashboard_base_url: string;
  recipient_identity_ids: number[];
  events: EventName[];
};
type Draft = Omit<Settings, "timeout_minutes"> & { timeout_minutes: string };
type Identity = { id: number; platform: string; external_id: string };
type Payload = { settings: Settings; identities: Identity[] };

function sameSelection<T>(current: T[], saved: T[]) {
  return current.length === saved.length && current.every(value => saved.includes(value));
}

export function NotificationsPage() {
  const [error, setError] = createSignal("");
  const [draft, setDraft] = createSignal<Draft>();
  const [feedback, setFeedback] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [data, { mutate }] = createResource(async () => {
    try {
      return await apiFetch<Payload>(endpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load notification settings.");
      return undefined;
    }
  });
  const savedSettings = createMemo(() => {
    const current = data()?.settings;
    return current && { ...current, timeout_minutes: String(current.timeout_minutes) };
  });
  const settings = () => draft() ?? savedSettings();
  const dirty = createMemo(() => {
    const current = draft();
    const saved = savedSettings();
    if (!current || !saved) return false;
    return current.dashboard_base_url !== saved.dashboard_base_url
      || current.timeout_minutes !== saved.timeout_minutes
      || current.fallback_prompt !== saved.fallback_prompt
      || !sameSelection(current.recipient_identity_ids, saved.recipient_identity_ids)
      || !sameSelection(current.events, saved.events);
  });

  function edit<K extends keyof Draft>(key: K, value: Draft[K]) {
    const current = settings();
    if (!current || saving()) return;
    setDraft({ ...current, [key]: value });
    setFeedback("");
  }

  function discard() {
    setDraft(undefined);
    setError("");
    setFeedback("Changes discarded.");
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    const current = settings();
    if (!current || !dirty() || saving()) return;
    setError("");
    setSaving(true);
    try {
      const result = await putJson<{ settings: Settings }>(endpoint, {
        ...current,
        timeout_minutes: Number(current.timeout_minutes),
      });
      batch(() => {
        mutate({ settings: result.settings, identities: data()?.identities ?? [] });
        setDraft(undefined);
        setFeedback("Settings saved.");
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save notification settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsLayout
      title="Notifications"
      description="Choose who receives alerts, which events to send, and how long Kai waits for a response."
    >
      <div class="notifications-overview">
        <Show when={data.loading} fallback={
          <Show when={settings()}>{current => (
            <form onSubmit={save} aria-busy={saving()}>
              <fieldset class="notification-grid" disabled={saving()}>
                <legend class="sr-only">Notification settings</legend>
                <section class="panel" aria-labelledby="notification-recipients-title">
                  <header class="panel-header notification-panel-header">
                    <span class="notification-panel-icon"><Users size={16} aria-hidden="true" /></span>
                    <h2 class="panel-title" id="notification-recipients-title">Recipients</h2>
                    <span class="badge">{current().recipient_identity_ids.length} selected</span>
                  </header>
                  <div class="panel-body notification-recipients">
                    <div>
                      <For each={channels}>{([platform, label, Icon]) => (
                        <div class="notification-channel" role="group" aria-labelledby={`notification-channel-${platform}`}>
                          <span class="notification-row-icon"><Icon size={14} aria-hidden="true" /></span>
                          <div class="notification-row-copy">
                            <h3 class="notification-row-title" id={`notification-channel-${platform}`}>{label}</h3>
                            <For each={data()?.identities.filter(identity => identity.platform === platform)}
                              fallback={<p class="hint">No paired accounts</p>}>
                              {identity => (
                                <label class="notification-account">
                                  <code class="hint">{identity.external_id}</code>
                                  <input
                                    type="checkbox"
                                    checked={current().recipient_identity_ids.includes(identity.id)}
                                    onChange={event => edit("recipient_identity_ids", event.currentTarget.checked
                                      ? [...current().recipient_identity_ids, identity.id]
                                      : current().recipient_identity_ids.filter(id => id !== identity.id))}
                                  />
                                </label>
                              )}
                            </For>
                          </div>
                        </div>
                      )}</For>
                    </div>
                    <p class="hint">Manage paired accounts in <A href="/settings/channels?tab=pairing">Settings → Channels</A>.</p>
                    <div class="notification-field notification-dashboard-url">
                      <label class="form-field-label" for="notification-dashboard-url">Dashboard URL</label>
                      <input
                        class="input"
                        id="notification-dashboard-url"
                        name="dashboard_base_url"
                        type="url"
                        spellcheck={false}
                        maxLength={2048}
                        placeholder="https://agent.example.com"
                        aria-describedby="notification-dashboard-url-hint"
                        required={current().recipient_identity_ids.length > 0}
                        value={current().dashboard_base_url}
                        onInput={event => edit("dashboard_base_url", event.currentTarget.value)}
                      />
                      <p class="hint" id="notification-dashboard-url-hint">
                        Used in notification links. Required when a recipient is selected.
                      </p>
                    </div>
                  </div>
                </section>

                <section class="panel" aria-labelledby="notification-events-title">
                  <header class="panel-header notification-panel-header">
                    <span class="notification-panel-icon"><Bell size={16} aria-hidden="true" /></span>
                    <div>
                      <h2 class="panel-title" id="notification-events-title">Events</h2>
                      <p class="hint">Send alerts for the selected events.</p>
                    </div>
                    <span class="badge">
                      {filters.filter(([name]) => current().events.includes(name)).length} of {filters.length} on
                    </span>
                  </header>
                  <div class="panel-body notification-events">
                    <For each={filters}>{([name, label, description, Icon]) => {
                      const enabled = () => current().events.includes(name);
                      return (
                        <button
                          class="toggle-control notification-event"
                          classList={{ active: enabled() }}
                          type="button"
                          role="switch"
                          aria-checked={enabled()}
                          aria-labelledby={`notification-event-${name}`}
                          aria-describedby={`notification-event-${name}-hint`}
                          onClick={() => edit("events", enabled()
                            ? current().events.filter(item => item !== name)
                            : [...current().events, name])}
                        >
                          <span class="notification-row-icon"><Icon size={14} aria-hidden="true" /></span>
                          <span class="notification-row-copy">
                            <span class="notification-row-title" id={`notification-event-${name}`}>{label}</span>
                            <span class="hint" id={`notification-event-${name}-hint`}>{description}</span>
                          </span>
                          <span class="toggle-track" aria-hidden="true"><span class="toggle-thumb" /></span>
                        </button>
                      );
                    }}</For>
                  </div>
                </section>

                <section class="panel notification-timeout" aria-labelledby="notification-timeout-title">
                  <header class="panel-header notification-panel-header">
                    <span class="notification-panel-icon"><Timer size={16} aria-hidden="true" /></span>
                    <div>
                      <h2 class="panel-title" id="notification-timeout-title">Response timeout</h2>
                      <p class="hint">Set what Kai does when a reply is overdue.</p>
                    </div>
                  </header>
                  <div class="panel-body notification-timeout-fields">
                    <div class="notification-field">
                      <label class="form-field-label" for="notification-timeout-minutes">Wait time</label>
                      <div class="notification-timeout-input">
                        <input
                          class="input"
                          id="notification-timeout-minutes"
                          name="timeout_minutes"
                          type="number"
                          min={1}
                          max={2147483647}
                          step={1}
                          required
                          aria-describedby="notification-timeout-unit"
                          value={current().timeout_minutes}
                          onInput={event => edit("timeout_minutes", event.currentTarget.value)}
                        />
                        <span class="hint" id="notification-timeout-unit">minutes</span>
                      </div>
                    </div>
                    <div class="notification-field">
                      <label class="form-field-label" for="notification-fallback-prompt">Fallback prompt</label>
                      <textarea
                        class="textarea"
                        id="notification-fallback-prompt"
                        name="fallback_prompt"
                        rows={2}
                        maxLength={16000}
                        aria-describedby="notification-fallback-hint"
                        value={current().fallback_prompt}
                        onInput={event => edit("fallback_prompt", event.currentTarget.value)}
                      />
                      <p class="hint" id="notification-fallback-hint">
                        Use <code>{"{timeout_minutes}"}</code> to insert the deadline. Leave blank to restore the default.
                      </p>
                    </div>
                  </div>
                </section>
              </fieldset>

              <footer class="notification-footer">
                <span class="hint notification-status" classList={{ "notification-status-dirty": dirty() }} role="status">
                  {saving() ? "Saving…" : dirty() ? "Unsaved changes" : feedback() || "All changes saved"}
                </span>
                <div class="notification-actions">
                  <button class="btn" type="button" disabled={saving() || !dirty()} onClick={discard}>Discard</button>
                  <button class="btn btn-primary" type="submit" disabled={saving() || !dirty()}>Save settings</button>
                </div>
              </footer>
            </form>
          )}</Show>
        }>
          <p class="hint" role="status">Loading notification settings…</p>
        </Show>
        <Show when={error()}><p class="notification-error" role="alert">{error()}</p></Show>
      </div>
    </SettingsLayout>
  );
}
