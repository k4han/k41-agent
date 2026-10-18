import { createEffect, createSignal, For, onCleanup, Show } from "solid-js";

import { apiFetch, postJson, putJson } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Dialog } from "@/components/Dialog";
import type { ChatGPTSession } from "@/types";

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

  const signIn = async () => {
    if (busy()) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true);
    setMessage("Waiting for ChatGPT sign-in...");
    try {
      const result = await postJson<{ authorization_url: string; attempt_id: string }>(`${endpoint()}/sign-in`, {});
      const url = new URL(result.authorization_url, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname !== `${endpoint()}/authorize/${encodeURIComponent(result.attempt_id)}`) throw new Error("Invalid ChatGPT authorization URL.");
      setAuthorizationUrl(url.href);
      if (popup) popup.location.href = url.href;

      const poll = async () => {
        if (disposed) return;
        try {
          const state = await apiFetch<ChatGPTSession & { status: string; message: string }>(`${endpoint()}/sign-in/${encodeURIComponent(result.attempt_id)}`);
          if (disposed) return;
          setMessage(state.message);
          if (state.status === "pending") {
            timer = setTimeout(poll, 1500);
            return;
          }
          setAuthorizationUrl("");
          setSession(state);
          if (state.status === "completed") {
            setWelcome(!hasSeenNotice() && state.sharing);
            await props.onReload();
            if (state.sharing) await discoverModels();
          } else {
            showToast(state.message, "error");
          }
        } catch (err) {
          showToast(err instanceof Error ? err.message : "ChatGPT connection failed.", "error");
        }
        if (!disposed) { setBusy(false); setAuthorizationUrl(""); }
      };
      timer = setTimeout(poll, 1000);
    } catch (err) {
      popup?.close();
      setBusy(false);
      setAuthorizationUrl("");
      setMessage(err instanceof Error ? err.message : "ChatGPT sign-in failed.");
      showToast(message(), "error");
    }
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
      <p class="hint">Use an eligible ChatGPT Plus or Pro plan. Complete sign-in in a browser on the same computer running k41.</p>
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
      <Show when={message()}><p class="hint" role="status">{message()}</p></Show>
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
