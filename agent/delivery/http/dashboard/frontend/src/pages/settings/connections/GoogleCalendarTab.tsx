import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import {
  CalendarDays,
  CheckCircle2,
  Copy,
  ExternalLink,
  PlugZap,
  RefreshCw,
  Save,
  TriangleAlert,
  Unplug,
  XCircle,
} from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { API_PATHS } from "@/lib/endpoints";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { GoogleCalendarAuthUrlPayload, GoogleCalendarPayload } from "@/types";

type Drafts = {
  enabled: boolean;
};

function draftsFromPayload(payload: GoogleCalendarPayload): Drafts {
  return {
    enabled: Boolean(payload.enabled),
  };
}

export function GoogleCalendarTab() {
  const [payload, setPayload] = createSignal<GoogleCalendarPayload>();
  const [loadError, setLoadError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Drafts>({
    enabled: true,
  });
  const [busy, setBusy] = createSignal<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = createSignal(false);
  const { showToast } = useToast();

  const load = async () => {
    setLoadError("");
    try {
      const data = await apiFetch<GoogleCalendarPayload>(API_PATHS.googleCalendar);
      setPayload(data);
      setDrafts(draftsFromPayload(data));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load Google Calendar");
    }
  };

  const onConnectedMessage = (event: MessageEvent) => {
    const data = event.data as { type?: string } | null;
    if (data && typeof data === "object" && data.type === "GOOGLE_CALENDAR_CONNECTED") {
      showToast("Google Calendar connected.");
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

  const pendingCount = createMemo(() => {
    const current = payload();
    if (!current) return 0;
    const next = drafts();
    let count = 0;
    if (Boolean(next.enabled) !== Boolean(current.enabled)) count += 1;
    return count;
  });

  const setDraft = <K extends keyof Drafts>(key: K, value: Drafts[K]) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const toggleEnabled = async (next: boolean) => {
    setDraft("enabled", next);
    setBusy("toggle");
    try {
      await putJson(API_PATHS.googleCalendarConfig, {
        enabled: next,
      });
      showToast(`Google Calendar ${next ? "enabled" : "disabled"}.`);
      await load();
    } catch (err) {
      setDrafts((current) => ({
        ...current,
        enabled: Boolean(payload()?.enabled ?? !next),
      }));
      showToast(err instanceof Error ? err.message : "Failed to update setting", "error");
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (pendingCount() === 0) return;
    setBusy("save");
    try {
      const next = drafts();
      await putJson(API_PATHS.googleCalendarConfig, {
        enabled: next.enabled,
      });
      showToast("Google Calendar settings saved.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save settings", "error");
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
      showToast("Redirect URI copied.");
    } catch {
      showToast("Failed to copy redirect URI.", "error");
    }
  };

  return (
    <div class="channels-grid">
      <article class="channel-card">
        <header class="channel-card-header">
          <div class="channel-brand-icon" data-brand="google-calendar" aria-hidden="true">
            <CalendarDays size={18} />
          </div>
          <div class="channel-card-title">
            <div class="channel-card-name">Google Calendar</div>
            <div class="hint">Single shared account, OAuth with encrypted refresh token.</div>
          </div>
          <div class="row-wrap">
            <Show
              when={payload()?.connected}
              fallback={<span class="badge badge-warning">not connected</span>}
            >
              <span class="badge badge-success">connected</span>
            </Show>
            <Show when={payload()}>
              <span
                class={payload()?.enabled ? "badge badge-success" : "badge badge-warning"}
                title={payload()?.enabled ? "Enabled" : "Disabled"}
              >
                {payload()?.enabled ? "enabled" : "disabled"}
              </span>
            </Show>
          </div>
        </header>

        <div class="channel-card-body">
          <div class="channel-card-empty-hint">
            <TriangleAlert size={14} />
            <div>
              Google OAuth credentials (<strong>GOOGLE_CALENDAR_CLIENT_ID</strong>,{" "}
              <strong>GOOGLE_CALENDAR_CLIENT_SECRET</strong>) are managed by the server
              environment. End users only press <strong>Connect</strong>.
            </div>
          </div>
          <Show when={!payload()?.configured && (payload() || loadError())}>
            <div class="channel-card-empty-hint">
              <TriangleAlert size={14} />
              <div>
                Server is missing Google OAuth env. Ask your operator to set{" "}
                <strong>GOOGLE_CALENDAR_CLIENT_ID</strong> and{" "}
                <strong>GOOGLE_CALENDAR_CLIENT_SECRET</strong>, then{" "}
                <strong>Connect</strong>.
              </div>
            </div>
          </Show>

          <DataGate data={payload()} error={loadError()} onRetry={() => void load()}>
            {(data) => (
              <div class="stack" style={{ gap: "16px" }}>
                <div class="channel-card-meta">
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Account</span>
                    <span class="mono">{data.email || "Not connected"}</span>
                  </div>
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Scopes</span>
                    <span class="mono">{data.scopes.length ? data.scopes.join(" ") : "—"}</span>
                  </div>
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Updated</span>
                    <span class="mono">{data.updated_at || "—"}</span>
                  </div>
                </div>

                <div class="field">
                  <label for="gcal-enabled">Enabled</label>
                  <button
                    id="gcal-enabled"
                    class={`toggle-control ${drafts().enabled ? "active" : ""}`}
                    type="button"
                    role="switch"
                    aria-checked={drafts().enabled}
                    disabled={busy() === "toggle"}
                    onClick={() => void toggleEnabled(!drafts().enabled)}
                  >
                    <span class="toggle-track">
                      <span class="toggle-thumb" />
                    </span>
                    <span class="toggle-label">{drafts().enabled ? "Enabled" : "Disabled"}</span>
                  </button>
                </div>

                <div class="channel-card-meta">
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Client ID</span>
                    <span class="mono">{data.client_id || "Not set (server env)"}</span>
                  </div>
                  <div class="channel-card-meta-row">
                    <span class="channel-card-meta-label">Client Secret</span>
                    <span class="mono">
                      {data.client_secret_configured ? "Set (server env)" : "Not set"}
                    </span>
                  </div>
                </div>

                <div class="field">
                  <label for="gcal-redirect-uri">Redirect URI (server env, read-only)</label>
                  <div class="channel-webhook-helper-value">
                    <code>{data.redirect_uri || "—"}</code>
                    <button class="btn btn-sm" type="button" onClick={() => void copyRedirectUri()}>
                      <Copy size={13} />
                      Copy
                    </button>
                  </div>
                  <p class="hint">
                    Must match exactly one authorized redirect URI in Google Cloud Console.
                  </p>
                </div>

                <details class="field">
                  <summary class="hint" style={{ cursor: "pointer" }}>
                    Fix 403 access_denied? Console checklist
                  </summary>
                  <ol class="hint" style={{ "padding-left": "18px", display: "grid", gap: "4px" }}>
                    <li>
                      OAuth consent screen → Audience: while Publishing status is Testing, add
                      your sign-in account under Test users (or publish the app).
                    </li>
                    <li>
                      Data Access → add scopes calendar.events, calendar.readonly,
                      userinfo.email, openid.
                    </li>
                    <li>APIs & Services → enable Google Calendar API in the same project.</li>
                    <li>Credentials → this Client ID → Authorized redirect URIs must contain the exact Redirect URI above.</li>
                    <li>Retry in an incognito window with the test-user account.</li>
                  </ol>
                </details>

                <Show when={data.accounts.length > 0}>
                  <div class="field">
                    <label>Connected accounts</label>
                    <div class="stack" style={{ gap: "6px" }}>
                      <For each={data.accounts}>
                        {(account) => (
                          <div class="channel-card-meta-row">
                            <span class="mono">{account.email || account.user_id}</span>
                            <Show when={account.is_primary}>
                              <span class="badge badge-success">primary</span>
                            </Show>
                          </div>
                        )}
                      </For>
                    </div>
                  </div>
                </Show>
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
              title="Reload Google Calendar status"
            >
              <RefreshCw size={13} />
              {busy() === "load" ? "Reloading..." : "Reload"}
            </button>
            <button
              class="btn btn-sm btn-primary"
              type="button"
              disabled={pendingCount() === 0 || busy() === "save"}
              onClick={() => void save()}
              title="Save enabled state"
            >
              <Save size={13} />
              {busy() === "save"
                ? "Saving..."
                : `Save${pendingCount() ? ` (${pendingCount()})` : ""}`}
            </button>
            <button
              class="btn btn-sm"
              type="button"
              disabled={!payload()?.configured || busy() === "connect"}
              onClick={() => void connect()}
              title={
                payload()?.configured
                  ? "Open Google consent screen"
                  : "Configure Client ID first"
              }
            >
              <ExternalLink size={13} />
              {busy() === "connect" ? "Opening..." : "Connect"}
            </button>
            <Show when={payload()?.connected}>
              <button
                class="btn btn-sm"
                type="button"
                disabled={busy() === "disconnect"}
                onClick={() => setConfirmDisconnect(true)}
                title="Revoke and delete stored credentials"
              >
                <Unplug size={13} />
                {busy() === "disconnect" ? "Disconnecting..." : "Disconnect"}
              </button>
            </Show>
          </div>

          <Show when={payload() && !payload()!.connected && payload()!.configured}>
            <div class="hint" style={{ display: "flex", "align-items": "center", gap: "6px" }}>
              <PlugZap size={13} />
              <span>Saved credentials found. Press Connect to link the shared account.</span>
            </div>
          </Show>
          <Show when={payload() && payload()!.connected}>
            <div class="hint" style={{ display: "flex", "align-items": "center", gap: "6px" }}>
              <CheckCircle2 size={13} />
              <span>Linked. Agent tools calendar_* can now read and manage events.</span>
            </div>
          </Show>
          <Show when={payload() && !payload()!.configured}>
            <div class="hint" style={{ display: "flex", "align-items": "center", gap: "6px" }}>
              <XCircle size={13} />
              <span>Missing server env GOOGLE_CALENDAR_CLIENT_ID or GOOGLE_CALENDAR_CLIENT_SECRET.</span>
            </div>
          </Show>
        </footer>
      </article>

      <ConfirmDialog
        open={confirmDisconnect()}
        title="Disconnect Google Calendar?"
        message="This revokes the stored OAuth token and deletes the shared account. Agent calendar tools will stop working until you reconnect."
        confirmLabel="Disconnect"
        confirmVariant="danger"
        loading={busy() === "disconnect"}
        onClose={() => setConfirmDisconnect(false)}
        onConfirm={() => void disconnect()}
      />
    </div>
  );
}
