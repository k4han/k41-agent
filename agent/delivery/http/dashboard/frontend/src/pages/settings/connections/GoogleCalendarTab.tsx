import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import {
  CalendarDays,
  CheckCircle2,
  Copy,
  ExternalLink,
  RefreshCw,
  TriangleAlert,
  Unplug,
} from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { API_PATHS } from "@/lib/endpoints";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { GoogleCalendarAuthUrlPayload, GoogleCalendarPayload } from "@/types";

function formatScopeName(scope: string): string {
  if (!scope) return "";
  const prefix = "https://www.googleapis.com/auth/";
  if (scope.startsWith(prefix)) {
    return scope.slice(prefix.length);
  }
  return scope;
}

function formatUpdatedTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoStr;
  }
}

export function GoogleCalendarTab() {
  const [payload, setPayload] = createSignal<GoogleCalendarPayload>();
  const [loadError, setLoadError] = createSignal("");
  const [busy, setBusy] = createSignal<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = createSignal(false);
  const { showToast } = useToast();

  const load = async () => {
    setLoadError("");
    setBusy("load");
    try {
      const data = await apiFetch<GoogleCalendarPayload>(API_PATHS.googleCalendar);
      setPayload(data);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load Google Calendar");
    } finally {
      setBusy(null);
    }
  };

  const onConnectedMessage = (event: MessageEvent) => {
    const data = event.data as { type?: string } | null;
    if (data && typeof data === "object" && data.type === "GOOGLE_CALENDAR_CONNECTED") {
      showToast("Google Calendar connected successfully.");
      void load();
    }
  };

  onMount(() => {
    void load();
    window.addEventListener("message", onConnectedMessage);
  });

  onCleanup(() => {
    window.removeEventListener("message", onConnectedMessage);
  });

  const toggleEnabled = async (next: boolean) => {
    setBusy("toggle");
    const previous = payload();
    if (previous) {
      setPayload({ ...previous, enabled: next });
    }
    try {
      await putJson(API_PATHS.googleCalendarConfig, {
        enabled: next,
      });
      showToast(`Google Calendar integration ${next ? "enabled" : "disabled"}.`);
      await load();
    } catch (err) {
      if (previous) {
        setPayload(previous);
      }
      showToast(err instanceof Error ? err.message : "Failed to update setting", "error");
    } finally {
      setBusy(null);
    }
  };

  const connect = async () => {
    setBusy("connect");
    try {
      const params = new URLSearchParams();
      const redirect = payload()?.redirect_uri?.trim();
      if (redirect) params.set("redirect_uri", redirect);
      const query = params.toString();
      const data = await apiFetch<GoogleCalendarAuthUrlPayload>(
        query ? `${API_PATHS.googleCalendarAuthUrl}?${query}` : API_PATHS.googleCalendarAuthUrl,
      );
      window.open(data.url, "_blank", "noopener,noreferrer,width=560,height=700");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to build auth URL", "error");
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    setBusy("disconnect");
    try {
      await postJson(API_PATHS.googleCalendarDisconnect, {});
      showToast("Google Calendar disconnected.", "warning");
      setConfirmDisconnect(false);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to disconnect", "error");
    } finally {
      setBusy(null);
    }
  };

  const copyRedirectUri = async () => {
    const value = payload()?.redirect_uri || "";
    if (!value) return;
    try {
      await writeToClipboard(value);
      showToast("Redirect URI copied to clipboard.");
    } catch {
      showToast("Failed to copy redirect URI.", "error");
    }
  };

  return (
    <div class="channels-grid">
      <article class="channel-card gcal-card">
        <header class="channel-card-header">
          <div class="channel-brand-icon" data-brand="google-calendar" aria-hidden="true">
            <CalendarDays size={20} />
          </div>
          <div class="channel-card-title">
            <div class="channel-card-name">Google Calendar</div>
            <div class="channel-card-subtitle">
              Sync events and let agents manage your schedule.
            </div>
          </div>
          <div class="row-wrap" style={{ gap: "6px" }}>
            <Show
              when={payload()?.connected}
              fallback={<span class="badge badge-warning">not connected</span>}
            >
              <span class="badge badge-success">connected</span>
            </Show>
            <Show when={payload()?.connected}>
              <span class={payload()?.enabled ? "badge badge-success" : "badge badge-warning"}>
                {payload()?.enabled ? "enabled" : "disabled"}
              </span>
            </Show>
          </div>
        </header>

        <div class="channel-card-body">
          <DataGate data={payload()} error={loadError()} onRetry={() => void load()}>
            {(data) => (
              <div class="stack" style={{ gap: "16px" }}>
                {/* Server configuration alert - only shown when OAuth is missing */}
                <Show when={!data.configured}>
                  <div
                    class="channel-card-error"
                    style={{
                      "border-color": "color-mix(in srgb, var(--warning) 35%, var(--border))",
                      background: "color-mix(in srgb, var(--warning) 8%, var(--surface))",
                      color: "var(--fg)",
                    }}
                  >
                    <TriangleAlert
                      size={16}
                      style={{ "color": "var(--warning)", "flex-shrink": "0", "margin-top": "2px" }}
                    />
                    <div class="stack" style={{ gap: "2px", "font-size": "12.5px" }}>
                      <strong>Google OAuth is not configured on the server</strong>
                      <span class="hint">
                        Environment variables <code>GOOGLE_CALENDAR_CLIENT_ID</code> and{" "}
                        <code>GOOGLE_CALENDAR_CLIENT_SECRET</code> are required to authenticate.
                      </span>
                    </div>
                  </div>
                </Show>

                {/* State 1: Connected */}
                <Show when={data.connected}>
                  <div class="gcal-account-box">
                    <div class="gcal-account-info">
                      <div class="gcal-account-avatar">
                        <CheckCircle2 size={18} />
                      </div>
                      <div class="gcal-account-details">
                        <span class="gcal-account-label">Connected Account</span>
                        <span class="gcal-account-email" title={data.email || undefined}>
                          {data.email || "Primary Calendar"}
                        </span>
                        <span class="gcal-account-status">
                          Agent calendar tools (reading & managing events) are active.
                        </span>
                      </div>
                    </div>

                    <div class="gcal-account-actions">
                      <button
                        class="btn btn-sm"
                        type="button"
                        disabled={busy() === "connect"}
                        onClick={() => void connect()}
                        title="Switch or reconnect Google account"
                      >
                        <ExternalLink size={13} />
                        {busy() === "connect" ? "Opening..." : "Switch Account"}
                      </button>
                      <button
                        class="btn btn-sm btn-danger"
                        type="button"
                        disabled={busy() === "disconnect"}
                        onClick={() => setConfirmDisconnect(true)}
                        title="Disconnect Google Calendar"
                      >
                        <Unplug size={13} />
                        {busy() === "disconnect" ? "Disconnecting..." : "Disconnect"}
                      </button>
                    </div>
                  </div>

                  {/* Toggle integration active state */}
                  <div class="gcal-toggle-row">
                    <div class="gcal-toggle-desc">
                      <span class="gcal-toggle-title">Enable Calendar Tools</span>
                      <span class="hint">
                        Allow AI agents to read, schedule, and update events on this calendar.
                      </span>
                    </div>
                    <button
                      id="gcal-enabled"
                      class={`toggle-control ${data.enabled ? "active" : ""}`}
                      type="button"
                      role="switch"
                      aria-checked={data.enabled}
                      disabled={busy() === "toggle"}
                      onClick={() => void toggleEnabled(!data.enabled)}
                    >
                      <span class="toggle-track">
                        <span class="toggle-thumb" />
                      </span>
                      <span class="toggle-label">{data.enabled ? "Enabled" : "Disabled"}</span>
                    </button>
                  </div>
                </Show>

                {/* State 2: Not connected */}
                <Show when={!data.connected}>
                  <div class="gcal-connect-cta">
                    <p class="gcal-connect-desc">
                      Connect your Google Calendar account to let AI agents check your schedule,
                      find free slots, and manage meetings.
                    </p>
                    <button
                      class="btn btn-primary"
                      type="button"
                      disabled={!data.configured || busy() === "connect"}
                      onClick={() => void connect()}
                    >
                      <ExternalLink size={14} />
                      {busy() === "connect" ? "Connecting..." : "Connect Google Calendar"}
                    </button>
                  </div>
                </Show>

                {/* Collapsible Technical & OAuth Setup Details */}
                <details class="gcal-details-dropdown">
                  <summary class="gcal-details-summary">
                    <span>OAuth & Technical Details</span>
                    <span class="hint" style={{ "font-weight": "400" }}>
                      Redirect URI, Client ID, Scopes
                    </span>
                  </summary>
                  <div class="gcal-details-content">
                    {/* Redirect URI with copy button */}
                    <div class="field">
                      <label class="channel-card-meta-label">Authorized Redirect URI</label>
                      <div class="channel-webhook-helper-value" style={{ "margin-top": "4px" }}>
                        <code class="gcal-code-break" title={data.redirect_uri}>
                          {data.redirect_uri || "—"}
                        </code>
                        <button
                          class="btn btn-sm"
                          type="button"
                          onClick={() => void copyRedirectUri()}
                        >
                          <Copy size={13} />
                          Copy
                        </button>
                      </div>
                      <p class="hint" style={{ "margin-top": "4px" }}>
                        Add this exact URI to <strong>Authorized redirect URIs</strong> in your
                        Google Cloud Console credentials.
                      </p>
                    </div>

                    {/* Client ID & secret status */}
                    <div class="channel-card-meta">
                      <div class="channel-card-meta-row">
                        <span class="channel-card-meta-label">Client ID</span>
                        <span class="mono gcal-text-break">
                          {data.client_id || "Not set in server environment"}
                        </span>
                      </div>
                      <div class="channel-card-meta-row">
                        <span class="channel-card-meta-label">Client Secret</span>
                        <span class="mono">
                          {data.client_secret_configured ? "Set (server env)" : "Not set"}
                        </span>
                      </div>
                    </div>

                    {/* Permissions (Scopes) as neat badges */}
                    <Show when={data.scopes.length > 0}>
                      <div class="field">
                        <label class="channel-card-meta-label">OAuth Permissions (Scopes)</label>
                        <div class="gcal-scopes-list">
                          <For each={data.scopes}>
                            {(scope) => (
                              <span class="gcal-scope-pill" title={scope}>
                                {formatScopeName(scope)}
                              </span>
                            )}
                          </For>
                        </div>
                      </div>
                    </Show>

                    {/* Troubleshooting checklist */}
                    <details style={{ "margin-top": "4px" }}>
                      <summary class="hint" style={{ cursor: "pointer" }}>
                        Fix 403 access_denied? Google Console checklist
                      </summary>
                      <ol
                        class="hint"
                        style={{
                          "padding-left": "18px",
                          display: "grid",
                          gap: "4px",
                          "margin-top": "6px",
                        }}
                      >
                        <li>
                          OAuth consent screen → While Publishing status is Testing, add your
                          account under <strong>Test users</strong>.
                        </li>
                        <li>APIs & Services → Enable <strong>Google Calendar API</strong>.</li>
                        <li>
                          Credentials → Add the exact Redirect URI above to Authorized redirect URIs.
                        </li>
                      </ol>
                    </details>
                  </div>
                </details>
              </div>
            )}
          </DataGate>
        </div>

        <footer class="channel-card-footer">
          <div class="channel-card-actions">
            <button
              class="btn btn-sm"
              type="button"
              disabled={busy() === "load"}
              onClick={() => void load()}
              title="Reload status"
            >
              <RefreshCw size={13} class={busy() === "load" ? "animate-spin" : ""} />
              {busy() === "load" ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <Show when={payload()?.updated_at}>
            <span class="hint" style={{ "font-size": "11px" }}>
              Synced: {formatUpdatedTime(payload()?.updated_at)}
            </span>
          </Show>
        </footer>
      </article>

      <ConfirmDialog
        open={confirmDisconnect()}
        title="Disconnect Google Calendar?"
        message="This will revoke access and delete stored credentials. Agents will no longer be able to read or manage your events until reconnected."
        confirmLabel="Disconnect"
        confirmVariant="danger"
        loading={busy() === "disconnect"}
        onClose={() => setConfirmDisconnect(false)}
        onConfirm={() => void disconnect()}
      />
    </div>
  );
}
