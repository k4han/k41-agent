import { createEffect, createSignal, For, onCleanup, Show } from "solid-js";

import { apiFetch, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Dialog } from "@/components/Dialog";
import type { ChatGPTSession } from "@/types";

type ChatGPTSignInState = ChatGPTSession & { status: string; message: string };

export function ChatGPTConnectionPanel(props: {
  name: string;
  session?: ChatGPTSession;
  defaultModel: string;
  configuredModels: string[];
  onReload: () => Promise<unknown>;
}) {
  const { showToast } = useToast();
  const [session, setSession] = createSignal<ChatGPTSession>({ connected: false, sharing: false, email: "" });
  const [busy, setBusy] = createSignal(false);
  const [message, setMessage] = createSignal("");
  const [authorizationUrl, setAuthorizationUrl] = createSignal("");
  const [attemptId, setAttemptId] = createSignal("");
  const [callbackUrl, setCallbackUrl] = createSignal("");
  const [completing, setCompleting] = createSignal(false);
  const [welcome, setWelcome] = createSignal(false);
  const [models, setModels] = createSignal<string[]>([]);
  const [modelLabels, setModelLabels] = createSignal<Record<string, string>>({});
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  const endpoint = () => `/dashboard-api/providers/${encodeURIComponent(props.name)}/chatgpt`;
  const noticeKey = () => `k41.chatgpt-plan-notice.${props.name}`;
  const dismissWelcome = () => {
    setWelcome(false);
    try { localStorage.setItem(noticeKey(), "seen"); } catch { /* The notice still works when storage is unavailable. */ }
  };
  const hasSeenNotice = () => {
    try { return localStorage.getItem(noticeKey()) === "seen"; } catch { return false; }
  };

  createEffect(() => { if (props.session) setSession(props.session); });
  createEffect(() => { setModels(props.configuredModels); });
  onCleanup(() => { disposed = true; clearTimeout(timer); });

  const discoverModels = async () => {
    const result = await postJson<{ ok: boolean; message: string; models: string[]; details?: { model_labels?: Record<string, string> } }>(
      "/dashboard-api/providers/verify", { name: props.name, type: "chatgpt" },
    );
    if (!result.ok) throw new Error(result.message);
    setModels(result.models);
    setModelLabels(result.details?.model_labels || {});
    const prefix = `llm.providers.${props.name}`;
    const values: Record<string, unknown> = { [`${prefix}.models`]: result.models };
    if (!result.models.includes(props.defaultModel)) values[`${prefix}.default_model`] = result.models[0] || "";
    await putJson("/settings", { values });
    await props.onReload();
  };

  const clearSignIn = () => {
    clearTimeout(timer);
    setAttemptId("");
    setCallbackUrl("");
    setAuthorizationUrl("");
  };

  const finishSignIn = async (id: string, state: ChatGPTSignInState) => {
    if (disposed || id !== attemptId()) return;
    clearSignIn();
    setMessage(state.message);
    setSession(state);
    try {
      if (state.status === "completed") {
        setWelcome(!hasSeenNotice() && state.sharing);
        await props.onReload();
        if (state.sharing) await discoverModels();
      } else {
        showToast(state.message, "error");
      }
    } finally { if (!disposed) setBusy(false); }
  };

  const pollSignIn = async (id: string) => {
    if (disposed || id !== attemptId()) return;
    try {
      const state = await apiFetch<ChatGPTSignInState>(`${endpoint()}/sign-in/${encodeURIComponent(id)}`);
      if (disposed || id !== attemptId()) return;
      setMessage(state.message);
      if (state.status === "pending") {
        timer = setTimeout(() => void pollSignIn(id), 1500);
        return;
      }
      await finishSignIn(id, state);
    } catch (err) {
      if (disposed) return;
      clearSignIn();
      setBusy(false);
      showToast(err instanceof Error ? err.message : "ChatGPT connection failed.", "error");
    }
  };

  const signIn = async () => {
    if (busy()) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true);
    setMessage("Waiting for ChatGPT sign-in...");
    try {
      const result = await postJson<{ authorization_url: string; attempt_id: string }>(`${endpoint()}/sign-in`, { resume: true });
      const url = new URL(result.authorization_url, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname !== `${endpoint()}/authorize/${encodeURIComponent(result.attempt_id)}`) throw new Error("Invalid ChatGPT authorization URL.");
      setAttemptId(result.attempt_id);
      setAuthorizationUrl(url.href);
      if (popup) popup.location.href = url.href;
      timer = setTimeout(() => void pollSignIn(result.attempt_id), 1000);
    } catch (err) {
      popup?.close();
      setBusy(false);
      clearSignIn();
      setMessage(err instanceof Error ? err.message : "ChatGPT sign-in failed.");
      showToast(message(), "error");
    }
  };

  const completeRemoteSignIn = async () => {
    const id = attemptId();
    const callback = callbackUrl().trim();
    if (!id || !callback || completing()) return;
    setCompleting(true);
    setCallbackUrl("");
    try {
      const state = await postJson<ChatGPTSignInState>(`${endpoint()}/sign-in/${encodeURIComponent(id)}/complete`, { callback_url: callback });
      await finishSignIn(id, state);
    } catch (err) {
      if (!disposed) showToast(err instanceof Error ? err.message : "Could not complete ChatGPT sign-in.", "error");
    } finally { if (!disposed) setCompleting(false); }
  };

  const cancelSignIn = async () => {
    const id = attemptId();
    if (!id || completing()) return;
    setCompleting(true);
    clearTimeout(timer);
    try {
      const state = await postJson<ChatGPTSignInState>(`${endpoint()}/sign-in/${encodeURIComponent(id)}/cancel`, {});
      if (state.status === "completed") await finishSignIn(id, state);
      else {
        clearSignIn();
        setMessage(state.message);
      }
    } catch (err) {
      clearSignIn();
      if (!disposed) showToast(err instanceof Error ? err.message : "Could not cancel ChatGPT sign-in.", "error");
    } finally { if (!disposed) { setCompleting(false); setBusy(false); } }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      const result = await postJson<{ revocation_confirmed: boolean; message: string }>(`${endpoint()}/sign-out`, {});
      setSession({ connected: false, sharing: false, email: session().email });
      setModels([]);
      setMessage(result.message);
      showToast(result.message, result.revocation_confirmed ? "success" : "warning");
      await props.onReload();
    } catch (err) { showToast(err instanceof Error ? err.message : "ChatGPT sign-out failed.", "error"); }
    finally { if (!disposed) setBusy(false); }
  };

  const refreshModels = async () => {
    setBusy(true);
    try { await discoverModels(); showToast("ChatGPT models refreshed."); }
    catch (err) { showToast(err instanceof Error ? err.message : "Could not load ChatGPT models.", "error"); }
    finally { if (!disposed) setBusy(false); }
  };

  const selectModel = async (model: string) => {
    setBusy(true);
    try {
      await putJson("/settings", { values: { [`llm.providers.${props.name}.default_model`]: model } });
      await props.onReload();
    } catch (err) { showToast(err instanceof Error ? err.message : "Could not save model.", "error"); }
    finally { if (!disposed) setBusy(false); }
  };

  return (
    <section class="panel provider-detail-panel stack" aria-label="ChatGPT connection">
      <h3>ChatGPT account</h3>
      <p class="hint">Use an eligible ChatGPT Plus or Pro plan. Connect from this dashboard; no local k41 installation is needed. If the callback page cannot open, return here and paste its full URL to finish.</p>
      <Show when={session().email}><span>{session().email}</span></Show>
      <p role="status">{session().sharing ? "Using ChatGPT plan" : session().connected ? "Connected. ChatGPT plan usage is not enabled; enable access in ChatGPT Settings and sign in again." : "ChatGPT is not connected."}</p>
      <Dialog open={welcome()} title="You're using your ChatGPT plan" size="sm" onClose={dismissWelcome}
        footer={<button class="btn btn-primary" type="button" onClick={dismissWelcome}>Got it</button>}>
        <p>Eligible requests in k41 use your plan allowance. Manage usage and limits in ChatGPT Settings.</p>
      </Dialog>
      <div class="row-wrap">
        <button class="btn btn-primary" type="button" disabled={busy()} onClick={signIn}>{busy() ? "Please wait..." : "Continue with ChatGPT"}</button>
        <a class="btn" href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a>
        <Show when={session().connected}><button class="btn" type="button" disabled={busy()} onClick={signOut}>Sign out</button></Show>
        <Show when={session().sharing}><button class="btn" type="button" disabled={busy()} onClick={refreshModels}>Refresh ChatGPT models</button></Show>
      </div>
      <Show when={authorizationUrl()}><a href={authorizationUrl()} target="_blank" rel="noopener noreferrer">Open ChatGPT sign-in</a></Show>
      <Show when={attemptId()}>
        <section class="stack" aria-label="Complete ChatGPT sign-in">
          <p class="hint">If the browser redirects to 127.0.0.1 and cannot connect, copy the entire URL from its address bar and paste it here. Keep this k41 page open.</p>
          <label class="stack">Callback URL
            <textarea class="input" rows={3} aria-label="ChatGPT callback URL" autocomplete="off" spellcheck={false}
              value={callbackUrl()} disabled={completing()} placeholder="http://127.0.0.1:.../auth/callback?code=...&state=...&client_id=..."
              onInput={(event) => setCallbackUrl(event.currentTarget.value)} />
          </label>
          <div class="row-wrap">
            <button class="btn btn-primary" type="button" disabled={completing() || !callbackUrl().trim()} onClick={completeRemoteSignIn}>{completing() ? "Please wait..." : "Complete sign-in"}</button>
            <button class="btn" type="button" disabled={completing()} onClick={cancelSignIn}>Cancel sign-in</button>
          </div>
        </section>
      </Show>
      <Show when={message()}><p class="hint" role="status">{message()}</p></Show>
      <details class="stack">
        <summary>Alternative: transfer an existing local connection</summary>
        <p class="hint">If you already use k41 on your own computer, you can transfer that connection instead. Stop the local runtime before exporting. Export removes local tokens without revoking the session.</p>
        <code>uv run k41 chatgpt export-session --provider LOCAL_PROVIDER --output chatgpt-session.k41</code>
        <p class="hint">Choose a strong passphrase when prompted and copy the encrypted file to the server over SSH. Run this command on the server as the user running k41, then enter the same passphrase:</p>
        <code>uv run k41 chatgpt import-session --provider {props.name} --input chatgpt-session.k41</code>
        <p class="hint">For containers, run the import inside the container. Reload this page and refresh ChatGPT models after import. Delete the transfer file after verifying the connection.</p>
        <a href="https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms" target="_blank" rel="noopener noreferrer">OpenAI self-hosted VM instructions</a>
      </details>
      <Show when={session().sharing && models().length}>
        <label class="stack">ChatGPT model
          <select class="input" value={props.defaultModel} disabled={busy()} onChange={(event) => void selectModel(event.currentTarget.value)}>
            <For each={models()}>{(model) => <option value={model}>{modelLabels()[model] || model}</option>}</For>
          </select>
        </label>
      </Show>
    </section>
  );
}
