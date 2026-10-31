import { ArrowRight, Camera, ChevronDown, Globe, LoaderCircle, Power, RefreshCw, Settings2, ShieldCheck } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, on, onCleanup, Show } from "solid-js";
import { apiFetch, postJson } from "@/lib/api";
import { browserModifiers, browserPointer, type BrowserHandoffResume } from "@/lib/browserViewer";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import "@/styles/browser.css";

type Profile = { profile_id: string; name: string };
type BrowserStatus = {
  open: boolean;
  profile_id: string | null;
  generation: string | null;
  mode: "agent" | "human";
  tabs: { id: string; title: string; url: string }[];
  handoff: { handoff_id: string; reason: string } | null;
  handoff_expired: boolean;
  images: { artifact_id: string; image_url: string }[];
};

export function BrowserPanel(props: {
  threadId: string | null;
  open: boolean;
  busy: boolean;
  onOpen: () => void;
  onResume: (payload: BrowserHandoffResume) => void;
  onHandoffChange: (pending: boolean, threadId: string) => void;
  onSessionChange?: (active: boolean, threadId: string | null) => void;
}) {
  const [status, setStatus] = createSignal<BrowserStatus | null>(null);
  const [profiles, setProfiles] = createSignal<Profile[]>([]);
  const [profileName, setProfileName] = createSignal("");
  const [error, setError] = createSignal("");
  const [working, setWorking] = createSignal(false);
  const [frame, setFrame] = createSignal("");
  const [connected, setConnected] = createSignal(false);
  const [canControl, setCanControl] = createSignal(false);
  const [attempt, setAttempt] = createSignal(0);
  const [selectedTab, setSelectedTab] = createSignal("");
  const [url, setUrl] = createSignal("");
  const [profilesOpen, setProfilesOpen] = createSignal(false);
  const sessionGeneration = createMemo(() => status()?.generation);
  const sessionActive = createMemo(() => Boolean(status()?.open));
  const currentPageUrl = createMemo(() => status()?.tabs.find((tab) => tab.id === selectedTab())?.url || "");
  const connectionLabel = () => !status()?.open ? "No active session"
    : connected() ? canControl() ? "You have control" : "Agent has control" : "Connecting";
  let socket: WebSocket | undefined;
  let screen: HTMLImageElement | undefined;
  let keyboard: HTMLTextAreaElement | undefined;
  let width = 1280;
  let height = 800;
  let announcedHandoff = "";
  const endpoint = () => `/dashboard-api/browser/threads/${encodeURIComponent(props.threadId || "")}`;

  createEffect(on(currentPageUrl, (value) => setUrl(value)));

  createEffect(() => {
    const thread = props.threadId;
    setStatus(null);
    setFrame("");
    setError("");
    setSelectedTab("");
    setUrl("");
    announcedHandoff = "";
    if (!thread) return;
    let disposed = false;
    let polling = false;
    let refreshQueued = false;
    let handoffRevision = 0;
    const refresh = async () => {
      if (polling) { refreshQueued = true; return; }
      polling = true;
      const requestRevision = handoffRevision;
      try {
        const next = await apiFetch<BrowserStatus>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}`);
        // Discard status captured before a newly streamed handoff.
        if (disposed || requestRevision !== handoffRevision) return;
        setStatus(next);
        if (next.handoff || !props.busy) props.onHandoffChange(Boolean(next.handoff), thread);
        if (next.handoff && next.handoff.handoff_id !== announcedHandoff) {
          announcedHandoff = next.handoff.handoff_id;
          props.onOpen();
        }
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : "Could not load browser status.");
      } finally {
        polling = false;
        if (refreshQueued && !disposed) {
          refreshQueued = false;
          void refresh();
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      if (props.open || props.busy || status()?.open || status()?.handoff) void refresh();
    }, 2000);
    const handoff = (event: Event) => {
      if ((event as CustomEvent).detail?.threadId === thread) {
        handoffRevision += 1;
        void refresh();
      }
    };
    const runStopped = (event: Event) => {
      if ((event as CustomEvent).detail?.threadId === thread) void refresh();
    };
    window.addEventListener("k41-browser-handoff", handoff);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, runStopped);
    onCleanup(() => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("k41-browser-handoff", handoff);
      window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, runStopped);
    });
  });

  createEffect(() => props.onSessionChange?.(sessionActive(), props.threadId));

  createEffect(() => {
    if (!props.open) return;
    let disposed = false;
    void apiFetch<{ profiles: Profile[] }>("/dashboard-api/browser/profiles")
      .then((data) => { if (!disposed) setProfiles(data.profiles); })
      .catch((cause) => { if (!disposed) setError(String(cause)); });
    onCleanup(() => { disposed = true; });
  });

  createEffect(() => {
    const visible = props.open;
    const thread = props.threadId;
    const generation = sessionGeneration();
    attempt();
    setConnected(false);
    setCanControl(false);
    if (!visible || !thread || !generation) { setFrame(""); return; }
    let disposed = false;
    let ownSocket: WebSocket | undefined;
    let retry: number | undefined;
    void postJson<{ ticket: string }>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}/viewer-ticket`, {})
      .then(({ ticket }) => {
        if (disposed) return;
        const address = new URL("/dashboard-api/browser/viewer", window.location.href);
        address.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        ownSocket = new WebSocket(address);
        socket = ownSocket;
        ownSocket.onopen = () => ownSocket?.send(JSON.stringify({ ticket }));
        ownSocket.onmessage = (event) => {
          if (disposed) return;
          const data = JSON.parse(event.data);
          if (data.type === "frame") {
            width = data.metadata.deviceWidth || 1280;
            height = data.metadata.deviceHeight || 800;
            setFrame(`data:image/jpeg;base64,${data.data}`);
            setConnected(true);
          } else if (data.type === "status") {
            setCanControl(Boolean(data.can_control));
            setStatus((previous) => previous ? { ...previous, tabs: data.tabs, mode: data.mode } : previous);
            setSelectedTab(data.tab_id || data.tabs[0]?.id || "");
          } else if (data.type === "error") setError(data.message);
        };
        ownSocket.onclose = () => {
          if (!disposed) {
            setConnected(false);
            setCanControl(false);
            retry = window.setTimeout(() => setAttempt((value) => value + 1), 2000);
          }
        };
      }).catch((cause) => { if (!disposed) setError(String(cause)); });
    onCleanup(() => {
      disposed = true;
      if (retry !== undefined) window.clearTimeout(retry);
      ownSocket?.close();
      if (socket === ownSocket) socket = undefined;
    });
  });

  const send = (event: Record<string, unknown>) => {
    if (socket?.readyState === WebSocket.OPEN && canControl()) socket.send(JSON.stringify(event));
  };
  const action = async (operation: () => Promise<unknown>, refreshThread = true) => {
    const thread = props.threadId;
    setWorking(true);
    setError("");
    try {
      await operation();
      if (refreshThread && thread && props.threadId === thread) {
        const next = await apiFetch<BrowserStatus>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}`);
        if (props.threadId === thread) setStatus(next);
      }
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Browser operation failed."); }
    finally { setWorking(false); }
  };
  const mouse = (event: PointerEvent | WheelEvent, type: string) => {
    if (!canControl() || !screen) return;
    event.preventDefault();
    if (type === "mousePressed") keyboard?.focus({ preventScroll: true });
    const point = browserPointer(event.clientX, event.clientY, screen.getBoundingClientRect(), width, height);
    send({ type, ...point, modifiers: browserModifiers(event),
      buttons: event.buttons,
      button: type === "mouseWheel" ? "none" : ["left", "middle", "right"][event.button] || "none",
      clickCount: event.detail || 1,
      ...(event instanceof WheelEvent ? { deltaX: event.deltaX, deltaY: event.deltaY } : {}) });
  };
  const key = (event: KeyboardEvent, type: string) => {
    if (!canControl() || event.isComposing || event.key === "Process") return;
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") return;
    event.preventDefault();
    event.stopPropagation();
    send({ type, key: event.key, code: event.code, keyCode: event.keyCode, modifiers: browserModifiers(event) });
  };

  return <div class="browser-panel">
      <header class="browser-session-header">
        <div class="browser-session-title"><Globe size={17} /><strong>Browser</strong>
          <span class="browser-connection" classList={{ "is-connected": connected(), "is-interactive": canControl() }} role="status">
            <span class="browser-status-dot" />{connectionLabel()}
          </span>
        </div>
        <div class="browser-actions">
          <button class="workspace-icon-btn" type="button" title="Login profiles" aria-label="Login profiles"
            aria-expanded={profilesOpen()} aria-controls="browser-profiles" onClick={() => setProfilesOpen((value) => !value)}><Settings2 size={16} /></button>
          <button class="workspace-icon-btn" type="button" title="Reconnect viewer" aria-label="Reconnect viewer" disabled={!status()?.open}
            onClick={() => { setError(""); setAttempt((value) => value + 1); }}><RefreshCw size={15} /></button>
          <button class="btn btn-sm" disabled={props.busy || working() || !props.threadId}
            onClick={() => void action(() => apiFetch(endpoint(), { method: status()?.open ? "DELETE" : "POST" }))}>
            <Power size={14} />{status()?.open ? "Close browser" : "Open browser"}
          </button>
        </div>
      </header>
      <Show when={error()}><div class="thread-banner thread-banner-error" role="alert">{error()}</div></Show>
      <Show when={status()?.handoff}>{(pending) => <div class="browser-handoff" role="status">
        <div class="browser-handoff-heading"><ShieldCheck size={17} /><strong>{status()?.handoff_expired ? "The previous browser session ended" : "Your turn in the browser"}</strong></div>
        <p>{pending().reason}</p>
        <div class="browser-actions">
          <button class="btn btn-sm btn-primary" disabled={props.busy || working()} onClick={() => props.onResume({ action: "browser_handoff", handoff_id: pending().handoff_id, decision: "return" })}>
            {status()?.handoff_expired ? "Continue after session loss" : "Return control to agent"}
          </button>
          <button class="btn btn-sm" disabled={props.busy || working()} onClick={() => props.onResume({ action: "browser_handoff", handoff_id: pending().handoff_id, decision: "cancel" })}>Cancel browser task</button>
        </div>
      </div>}</Show>
      <Show when={profilesOpen()}><section class="browser-profiles" id="browser-profiles" aria-label="Login profile settings">
        <div class="browser-profiles-heading"><strong>Login profiles</strong><span>Reuse a saved login across conversations.</span></div>
        <div class="browser-actions">
        <label class="browser-profile-select">Login profile <select aria-label="Login profile" value={status()?.profile_id || ""} disabled={!props.threadId || status()?.open || props.busy || working()}
          onChange={(event) => void action(() => apiFetch(endpoint(), { method: "PUT", json: { profile_id: event.currentTarget.value || null } }))}>
          <option value="">This conversation only</option>
          <For each={profiles()}>{(profile) => <option value={profile.profile_id}>{profile.name}</option>}</For>
        </select></label>
        <input aria-label="New login profile name" placeholder="New profile name" value={profileName()} onInput={(event) => setProfileName(event.currentTarget.value)} />
        <button class="btn btn-sm" disabled={!profileName().trim() || working()} onClick={() => void action(async () => {
          const profile = await postJson<Profile>("/dashboard-api/browser/profiles", { name: profileName().trim() });
          setProfiles((values) => [...values, profile]); setProfileName("");
        }, false)}>Create profile</button>
        <Show when={status()?.profile_id}><button class="btn btn-sm" disabled={status()?.open || props.busy || working()} onClick={() => void action(async () => {
          const profileId = status()?.profile_id;
          await apiFetch(`/dashboard-api/browser/profiles/${profileId}`, { method: "DELETE" });
          setProfiles((values) => values.filter((profile) => profile.profile_id !== profileId));
          setStatus((previous) => previous && previous.profile_id === profileId ? { ...previous, profile_id: null } : previous);
        }, false)}>Delete profile</button></Show>
        </div>
        <p class="browser-profile-hint">Close the browser before changing or deleting its profile.</p>
      </section></Show>
      <div class="browser-chrome">
        <Show when={status()?.tabs.length}><div class="browser-page-tabs" role="tablist" aria-label="Browser pages">
          <For each={status()?.tabs}>{(tab) => <button type="button" class="browser-page-tab" role="tab"
            aria-selected={selectedTab() === tab.id} title={tab.url} disabled={!connected()}
            onClick={() => {
              if (socket?.readyState !== WebSocket.OPEN) return;
              setSelectedTab(tab.id);
              socket.send(JSON.stringify({ type: "select_tab", tab_id: tab.id }));
            }}><Globe size={13} /><span>{tab.title || tab.url || "New tab"}</span></button>}</For>
        </div></Show>
        <form class="browser-address" onSubmit={(event) => { event.preventDefault(); if (url().trim()) send({ type: "navigate", url: url().trim() }); }}>
          <Globe size={14} aria-hidden="true" />
          <input aria-label="Browser address" placeholder="Enter a URL"
            disabled={!canControl()} value={url()} onInput={(event) => setUrl(event.currentTarget.value)} />
          <button class="workspace-icon-btn" type="submit" title="Navigate" aria-label="Navigate" disabled={!canControl() || !url().trim()}><ArrowRight size={16} /></button>
        </form>
      </div>
      <div class="browser-viewport" aria-label="Live browser viewport" aria-busy={Boolean(status()?.open && !frame())}>
      <Show when={frame()} fallback={<div class="browser-empty">
        <div class="browser-empty-icon"><Show when={status()?.open} fallback={<Globe size={28} />}><LoaderCircle size={28} class="browser-spinner" /></Show></div>
        <strong>{!props.threadId ? "Start a conversation to browse" : status()?.open ? "Connecting to your browser" : "A browser for your agent"}</strong>
        <p>{!props.threadId ? "Your browser session will stay with this conversation." : status()?.open ? "The live page will appear here. You can keep chatting while it connects." : "Open a session or ask your agent to browse. Follow its work here and take control when it needs you."}</p>
      </div>}>
        <div class="browser-screen" classList={{ "browser-screen-interactive": canControl() }}>
          <img ref={screen} src={frame()} alt="Live browser page" draggable={false}
            onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); mouse(event, "mousePressed"); }}
            onPointerUp={(event) => mouse(event, "mouseReleased")}
            onPointerMove={(event) => mouse(event, "mouseMoved")}
            onContextMenu={(event) => event.preventDefault()} onWheel={(event) => mouse(event, "mouseWheel")} />
          <textarea ref={keyboard} class="browser-keyboard" aria-label="Browser keyboard" disabled={!canControl()}
            autocomplete="off" autocapitalize="off" spellcheck={false}
            onKeyDown={(event) => key(event, "keyDown")} onKeyUp={(event) => key(event, "keyUp")}
            onInput={(event) => {
              if ((event as InputEvent).isComposing) return;
              const text = event.currentTarget.value; if (text) send({ type: "text", text }); event.currentTarget.value = "";
            }} onPaste={(event) => { event.preventDefault(); const text = event.clipboardData?.getData("text/plain"); if (text) send({ type: "text", text }); }} />
        </div>
      </Show>
      </div>
      <footer class="browser-footer"><ShieldCheck size={14} /><span>{canControl() ? "Enter credentials directly in the browser, then return control to your agent." : "Ask your agent to hand over control when you need to sign in."}</span></footer>
      <Show when={status()?.images?.length}><details class="browser-captures"><summary><Camera size={14} />Screenshots <span>{status()?.images.length}</span><ChevronDown size={14} /></summary><div class="browser-images"><For each={status()?.images}>{(image) =>
        <a href={image.image_url} target="_blank" rel="noreferrer"><img src={image.image_url} alt="Agent browser screenshot" /></a>
      }</For></div></details></Show>
    </div>;
}
