import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Bot, Download, RefreshCw } from "lucide-solid";

import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import { type AcpInstallJob, installProgressLabel } from "@/lib/acp";
import { API_PATHS } from "@/lib/endpoints";
import { SettingsLayout } from "./SettingsLayout";

type AuthMethod = { id: string; name: string; description: string };
type AuthStatus = {
  signed_in: boolean;
  state: "not_installed" | "installing" | "signed_out" | "signed_in" | "error";
  error?: string;
  plan?: string;
  uses_api_key?: boolean;
  can_sign_out?: boolean;
  methods: AuthMethod[];
};
type SignInAttempt = {
  attempt_id: string;
  method_id: string;
  status: "pending" | "succeeded" | "failed" | "cancelled";
  error: string;
  authorization_url: string;
  expires_at: number;
};
type AcpAgent = {
  id: string;
  display_name: string;
  terms_url: string;
  installed: boolean;
  version: string | null;
  license: string;
  job: AcpInstallJob | null;
  auth: AuthStatus;
  sign_in: SignInAttempt | null;
};

const API_KEY_METHOD = "gemini-api-key";
const POLL_MS = 1500;

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function AcpAgentsPage() {
  const [agents, setAgents] = createSignal<AcpAgent[]>();
  const [loadError, setLoadError] = createSignal("");
  const [refreshing, setRefreshing] = createSignal(false);
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const load = async (refresh = false) => {
    clearTimeout(timer);
    try {
      const payload = await apiFetch<{ agents: AcpAgent[] }>(`${API_PATHS.acpAgents}${refresh ? "?refresh=true" : ""}`);
      if (disposed) return;
      setAgents(payload.agents);
      setLoadError("");
    } catch (err) {
      if (!disposed) setLoadError(errorText(err, "Could not load ACP agents."));
    }
    const active = agents()?.some((agent) => agent.job?.state === "running" || agent.sign_in?.status === "pending");
    if (!disposed && active) timer = setTimeout(() => void load(), POLL_MS);
  };

  const refresh = async () => {
    setRefreshing(true);
    try { await load(true); } finally { if (!disposed) setRefreshing(false); }
  };

  onMount(() => void load());
  onCleanup(() => { disposed = true; clearTimeout(timer); });

  return (
    <SettingsLayout title="ACP Agents" contentWidth="medium">
      <div class="stack settings-page-stack">
        <div class="settings-page-heading">
          <p class="settings-page-description">
            Run external coding agents over the Agent Client Protocol. They use their own tools and their own account.
          </p>
        </div>
        <Show when={loadError()}><p class="hint" role="alert">{loadError()}</p></Show>
        <Show when={agents()} fallback={<Show when={!loadError()}><p class="hint" role="status">Starting the agent to check its status…</p></Show>}>
          <For each={agents()}>{(agent) => <AcpAgentPanel agent={agent} refreshing={refreshing()} onRefresh={refresh} onChanged={() => load(true)} onPoll={() => load()} />}</For>
        </Show>
      </div>
    </SettingsLayout>
  );
}

function AcpAgentPanel(props: {
  agent: AcpAgent;
  refreshing: boolean;
  onRefresh: () => Promise<void>;
  onChanged: () => Promise<void>;
  onPoll: () => Promise<void>;
}) {
  const { showToast } = useToast();
  const [accepted, setAccepted] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [apiKey, setApiKey] = createSignal("");
  const [callbackUrl, setCallbackUrl] = createSignal("");
  const base = () => API_PATHS.acpAgent(props.agent.id);
  const installing = () => props.agent.job?.state === "running";
  const signIn = () => (props.agent.sign_in?.status === "pending" ? props.agent.sign_in : null);
  const methods = createMemo(() => props.agent.auth.methods);
  const browserMethods = createMemo(() => methods().filter((method) => method.id !== API_KEY_METHOD));
  const acceptsApiKey = createMemo(() => methods().some((method) => method.id === API_KEY_METHOD));

  const run = async (action: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    try {
      await action();
      await props.onChanged();
    } catch (err) {
      showToast(errorText(err, fallback), "error");
      await props.onPoll();
    } finally {
      setBusy(false);
    }
  };

  const install = () => run(() => postJson(`${base()}/install`, { accept_license: true }), "Could not start the install.");
  const startSignIn = (methodId: string) => run(
    () => postJson(`${base()}/sign-in`, { method_id: methodId }),
    "Could not start sign-in.",
  );
  const completeSignIn = () => {
    const attempt = signIn();
    const value = callbackUrl().trim();
    if (!attempt || !value) return;
    // The address holds an authorization code, so it leaves the page state at once.
    setCallbackUrl("");
    return run(() => postJson(`${base()}/sign-in/${attempt.attempt_id}/complete`, { callback_url: value }), "Could not complete sign-in.");
  };
  const cancelSignIn = () => {
    const attempt = signIn();
    return attempt && run(() => postJson(`${base()}/sign-in/${attempt.attempt_id}/cancel`, {}), "Could not cancel sign-in.");
  };
  const saveApiKey = () => {
    const value = apiKey().trim();
    if (!value) return;
    setApiKey("");
    return run(() => postJson(`${base()}/api-key`, { api_key: value }), "Could not save the API key.");
  };
  const signOut = () => run(() => postJson(`${base()}/sign-out`, {}), "Could not sign out.");
  const uninstall = () => {
    if (!window.confirm(`Remove ${props.agent.display_name}, including its sign-in and saved sessions?`)) return;
    return run(() => deleteJson(base()), "Could not remove the agent.");
  };

  const statusText = createMemo(() => {
    const { agent } = props;
    if (installing()) return "Installing…";
    if (!agent.installed) return "Not installed";
    if (agent.auth.state === "signed_in") {
      const account = agent.auth.uses_api_key ? "using an API key" : agent.auth.plan ? `signed in (${agent.auth.plan})` : "signed in";
      return `Version ${agent.version}, ${account}`;
    }
    if (agent.auth.state === "error") return `Version ${agent.version}: ${agent.auth.error || "status unavailable"}`;
    return `Version ${agent.version}, not signed in`;
  });

  return (
    <section class="panel settings-section-card" aria-label={props.agent.display_name}>
      <div class="panel-header split">
        <div class="panel-title"><Bot size={17} /> {props.agent.display_name}</div>
        <button class="btn btn-secondary btn-sm" type="button" disabled={props.refreshing} onClick={() => void props.onRefresh()}>
          <RefreshCw size={13} class={props.refreshing ? "spinner-animate" : ""} /> Refresh
        </button>
      </div>
      <div class="panel-body stack">
      <p role="status">{statusText()}</p>

      <Show when={props.agent.job}>
        {(job) => <p class="hint" role={job().state === "failed" ? "alert" : "status"}>{installProgressLabel(job())}</p>}
      </Show>

      <Show when={!props.agent.installed && !installing()}>
        <p class="hint">
          K41 downloads the official binary from the ACP Registry and checks its checksum. It is {props.agent.license} software from its vendor, and its terms apply to your use.
        </p>
        <label class="row-wrap">
          <input type="checkbox" checked={accepted()} onChange={(event) => setAccepted(event.currentTarget.checked)} />
          <span>I accept the <a href={props.agent.terms_url} target="_blank" rel="noopener noreferrer">vendor's terms</a></span>
        </label>
      </Show>

      <div class="row-wrap">
        <Show when={!installing()}>
          <Show when={props.agent.installed} fallback={
            <button class="btn btn-primary" type="button" disabled={busy() || !accepted()} onClick={install}>
              <Download size={14} /> Install
            </button>
          }>
            <button class="btn" type="button" disabled={busy() || Boolean(signIn())} onClick={install}>Check for updates</button>
            <button class="btn" type="button" disabled={busy() || Boolean(signIn())} onClick={uninstall}>Remove</button>
            <Show when={props.agent.auth.signed_in}>
              <button class="btn" type="button" disabled={busy()} onClick={signOut}>Sign out</button>
            </Show>
          </Show>
        </Show>
      </div>

      <Show when={props.agent.installed && !props.agent.auth.signed_in && !signIn() && !installing()}>
        <div class="stack">
          <h4>Sign in</h4>
          <div class="row-wrap">
            <For each={browserMethods()}>{(method) => (
              <button class="btn btn-primary" type="button" disabled={busy()} title={method.description} onClick={() => startSignIn(method.id)}>
                {method.name}
              </button>
            )}</For>
          </div>
          <Show when={props.agent.sign_in?.status === "failed"}>
            <p class="hint" role="alert">{props.agent.sign_in?.error || "Sign-in failed."}</p>
          </Show>
          <Show when={acceptsApiKey()}>
            <label class="stack">Gemini API key
              <input class="input" type="password" autocomplete="off" spellcheck={false} aria-label="Gemini API key"
                value={apiKey()} disabled={busy()} onInput={(event) => setApiKey(event.currentTarget.value)} />
            </label>
            <div class="row-wrap">
              <button class="btn" type="button" disabled={busy() || !apiKey().trim()} onClick={saveApiKey}>Use API key</button>
            </div>
            <p class="hint">The key is stored on this server and checked with the agent before it is kept.</p>
          </Show>
        </div>
      </Show>

      <Show when={signIn()}>
        {(attempt) => (
          <section class="stack" aria-label="Complete sign-in">
            <h4>Finish signing in</h4>
            <div class="row-wrap">
              <a class="btn btn-primary" href={attempt().authorization_url} target="_blank" rel="noopener noreferrer">Open sign-in page</a>
              <button class="btn" type="button" disabled={busy()} onClick={cancelSignIn}>Cancel</button>
            </div>
            <p class="hint">
              The sign-in page may already have opened on the machine running K41. The agent stops waiting 5 minutes after you start.
            </p>
            <p class="hint">
              If K41 runs on another machine, your browser ends on a 127.0.0.1 address that cannot load. Copy that whole address and paste it here. It only works once.
            </p>
            <label class="stack">Final address
              <textarea class="input" rows={3} aria-label="Sign-in callback address" autocomplete="off" spellcheck={false}
                value={callbackUrl()} disabled={busy()} placeholder="http://127.0.0.1:…/?state=…&code=…"
                onInput={(event) => setCallbackUrl(event.currentTarget.value)} />
            </label>
            <div class="row-wrap">
              <button class="btn" type="button" disabled={busy() || !callbackUrl().trim()} onClick={completeSignIn}>Complete sign-in</button>
            </div>
          </section>
        )}
      </Show>

      <Show when={props.agent.installed}>
        <p class="hint">
          This agent's tools run on the machine running K41, with the same access as K41 itself. Keep approvals on unless you trust everything the agent reads.
        </p>
      </Show>
      </div>
    </section>
  );
}
