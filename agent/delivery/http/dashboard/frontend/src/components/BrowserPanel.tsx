import { ArrowLeft, ArrowRight, Camera, ChevronDown, Globe, LoaderCircle, Power, RefreshCw, Settings2, ShieldCheck } from "lucide-solid";
import { A } from "@solidjs/router";
import { createEffect, createMemo, createSignal, For, on, onCleanup, Show, untrack } from "solid-js";
import { apiFetch } from "@/lib/api";
import { browserModifiers, browserPointer, connectBrowserViewer, createBrowserFrameRenderer, createBrowserInputSender,
  type BrowserConnectionState, type BrowserHandoffResume, type BrowserViewerConnection } from "@/lib/browserViewer";
import { API_PATHS } from "@/lib/endpoints";
import { asyncTaskPath } from "@/lib/asyncSubagents";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { useBrowserSession, type BrowserSession } from "@/lib/useBrowserSession";
import { useBrowserSelection } from "@/lib/useBrowserSelection";
import type { BrowserProfile } from "@/types";
import "@/styles/browser.css";

export function BrowserPanel(props: {
  threadId: string | null;
  open: boolean;
  busy: boolean;
  onOpen: () => void;
  onResume: (payload: BrowserHandoffResume) => void;
  onHandoffChange: (pending: boolean, threadId: string) => void;
  onSessionChange?: (active: boolean, threadId: string | null) => void;
  session?: BrowserSession;
}) {
  const rootSession = props.session ?? useBrowserSession({ threadId: () => props.threadId, visible: () => props.open,
    active: () => props.open, busy: () => props.busy, onOpen: () => props.onOpen(),
    onHandoffChange: (pending, thread) => props.onHandoffChange(pending, thread),
    onSessionChange: (active, thread) => props.onSessionChange?.(active, thread) });
  const selection = useBrowserSelection(() => props.threadId, rootSession);
  const childSession = useBrowserSession({
    threadId: () => selection.isMain() ? null : selection.threadId(),
    visible: () => props.open, active: () => props.open, busy: () => props.busy,
    onOpen() {}, onHandoffChange() {},
  });
  const session = () => selection.isMain() ? rootSession : childSession;
  const status = () => session().status();
  const childSessions = createMemo(() => rootSession.sessions().filter(item => item.thread_id !== props.threadId));
  const childLabel = (thread: string, name: string | null) => {
    const label = name || "Subagent";
    return childSessions().filter(item => (item.agent_name || "Subagent") === label).length > 1
      ? `${label} · ${thread.split(":").at(-1)?.slice(0, 8)}` : label;
  };
  const [profiles, setProfiles] = createSignal<BrowserProfile[]>([]);
  const [error, setError] = createSignal("");
  const [working, setWorking] = createSignal(false);
  const [hasFrame, setHasFrame] = createSignal(false);
  const [connected, setConnected] = createSignal(false);
  const [controlAllowed, setControlAllowed] = createSignal(false);
  const [frameFresh, setFrameFresh] = createSignal(false);
  const [connectionState, setConnectionState] = createSignal<BrowserConnectionState>("connecting");
  const [controller, setController] = createSignal("agent");
  const [viewportSize, setViewportSize] = createSignal({ width: 0, height: 0 });
  const [frameAspect, setFrameAspect] = createSignal(1280 / 720);
  const screenWidth = createMemo(() => {
    const size = viewportSize();
    return Math.max(0, Math.min(size.width, Math.max(0, size.height - 2) * frameAspect() + 2));
  });
  const canControl = createMemo(() => connected() && controlAllowed() && frameFresh() && connectionState() !== "paused");
  const childHandoff = createMemo(() => rootSession.handoffs().find(item => item.thread_id === selection.threadId()
    && item.handoff_id === status()?.handoff?.handoff_id));
  const scheduledHandoff = createMemo(() => status()?.scheduled_run?.interrupts?.find(item =>
    item.value.handoff_id === status()?.handoff?.handoff_id));
  const resumeDisabled = () => working() || (status()?.scheduled_run ? !scheduledHandoff()
    : selection.isMain() ? props.busy : !childHandoff());
  const [now, setNow] = createSignal(Date.now());
  createEffect(() => {
    if (!props.open || !status()?.expires_at) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    onCleanup(() => window.clearInterval(timer));
  });
  const lifecycleLabel = () => {
    const value = status();
    if (!value?.open) return "Session closed";
    const remaining = value.expires_at ? Math.max(0, Math.ceil(value.expires_at - now() / 1000)) : null;
    const duration = remaining === null ? "" : ` · ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")} remaining`;
    return `${value.state === "waiting_for_human" ? "Waiting for you" : value.state === "active" ? "Task running" : "Kept for review"}${duration}`;
  };
  const [followAgent, setFollowAgent] = createSignal(true);
  const [attempt, setAttempt] = createSignal(0);
  const [selectedTab, setSelectedTab] = createSignal("");
  const [url, setUrl] = createSignal("");
  const [profilesOpen, setProfilesOpen] = createSignal(false);
  const selectedProfileId = createMemo(() => status()?.profile_mode === "inherit" ? "" : status()?.selected_profile_id ?? status()?.profile_id ?? "");
  const sessionGeneration = createMemo(() => status()?.generation);
  const currentPageUrl = createMemo(() => status()?.tabs.find((tab) => tab.id === selectedTab())?.url || "");
  const connectionLabel = () => !status()?.open ? "No active session"
    : connectionState() === "paused" ? "Stream paused"
    : !connected() ? hasFrame() ? "Reconnecting" : "Connecting"
    : !frameFresh() ? "Updating browser image"
    : canControl() ? controller() === "you" ? "You have control" : "Ready for your input"
    : status()?.mode === "human" ? "Another window has control" : "Agent has control";
  let connection: BrowserViewerConnection | undefined;
  let screen: HTMLCanvasElement | undefined;
  let viewport: HTMLDivElement | undefined;
  let keyboard: HTMLTextAreaElement | undefined;
  let profileSelect: HTMLSelectElement | undefined;
  let sessionSelect: HTMLSelectElement | undefined;
  let width = 1280;
  let height = 720;
  let viewerSession = "";
  const endpoint = () => `/dashboard-api/browser/threads/${encodeURIComponent(props.threadId || "")}`;
  const selectedEndpoint = () => `/dashboard-api/browser/threads/${encodeURIComponent(selection.threadId() || "")}`;
  const renderer = createBrowserFrameRenderer(() => screen);
  const input = createBrowserInputSender((event) => {
    if (canControl()) connection?.sendInput(event);
  });
  const send = input.send;
  const resetFrame = () => { renderer.reset(); input.reset(); setHasFrame(false); setFrameFresh(false); };
  const staleFrame = () => { renderer.reset(); input.reset(); setFrameFresh(false); };
  const applyStatus = (next: Parameters<BrowserSession["applyStatus"]>[0], thread: string) => session().applyStatus(next, thread);
  createEffect(() => { const message = session().error() || rootSession.error(); if (message) setError(message); });
  createEffect(() => {
    rootSession.setConnected(connected());
    childSession.setConnected(!selection.isMain() && connected());
  });
  onCleanup(() => { rootSession.setConnected(false); childSession.setConnected(false); });
  onCleanup(resetFrame);
  createEffect(() => { if (!canControl()) input.reset(); });

  createEffect(on(currentPageUrl, (value) => setUrl(value)));

  createEffect(on(selection.threadId, () => {
    resetFrame();
    setError("");
    setWorking(false);
    setSelectedTab("");
    setUrl("");
  }));

  createEffect(() => {
    // Opening the settings shows which conversations hold each profile now.
    props.threadId;
    if (!props.open || !profilesOpen() || !selection.isMain()) return;
    let disposed = false;
    void apiFetch<{ profiles: BrowserProfile[] }>(API_PATHS.browserProfiles)
      .then((data) => { if (!disposed) setProfiles(data.profiles); })
      .catch((cause) => { if (!disposed) setError(String(cause)); });
    onCleanup(() => { disposed = true; });
  });

  const showSelectedProfile = () => { if (profileSelect) profileSelect.value = selectedProfileId(); };
  // Replacing the options makes the select fall back to its first one, so apply the value afterwards.
  createEffect(on([profilesOpen, profiles, selectedProfileId], showSelectedProfile));
  createEffect(on([childSessions, selection.threadId], () => {
    if (sessionSelect) sessionSelect.value = selection.threadId() || "";
  }));

  createEffect(() => {
    const visible = props.open;
    const thread = selection.threadId();
    const generation = sessionGeneration();
    attempt();
    setConnected(false);
    setControlAllowed(false);
    const viewerKey = `${thread || ""}:${generation || ""}`;
    if (viewerSession !== viewerKey) {
      resetFrame(); setSelectedTab(""); setFollowAgent(true);
      viewerSession = viewerKey;
    } else staleFrame();
    if (!visible || !thread || !generation) return;
    let disposed = false;
    const isCurrent = () => !disposed && selection.threadId() === thread && sessionGeneration() === generation;
    const activeConnection = connectBrowserViewer({
      requestTicket: (signal) => apiFetch<{ ticket: string }>(`/dashboard-api/browser/threads/${encodeURIComponent(thread)}/viewer-ticket`,
        { method: "POST", json: {}, signal }),
      selection: untrack(() => ({ tabId: selectedTab(), followAgent: followAgent() })),
      onDisconnect: () => { if (isCurrent()) { setConnected(false); setControlAllowed(false); staleFrame(); } },
      onStateChange: (value) => { if (isCurrent()) { setConnectionState(value); setConnected(value === "connected" || value === "paused"); } },
      onPause: () => { if (isCurrent()) staleFrame(); },
      frameTiming: () => renderer.timing,
      onError: (cause) => { if (isCurrent()) setError(cause instanceof Error ? cause.message : String(cause)); },
      onFrame: async (frame) => {
        if (!isCurrent() || !await renderer.draw(frame) || !isCurrent()) return false;
        width = frame.width;
        height = frame.height;
        setFrameAspect(frame.width / frame.height);
        setHasFrame(true);
        setFrameFresh(true);
        setError("");
        setConnected(true);
        return true;
      },
      onMessage: (data) => {
        if (!isCurrent()) return;
        if (data.type === "status") {
          if (data.generation && data.generation !== generation) return;
          const nextTab = data.tab_id || data.tabs[0]?.id || "";
          if (nextTab !== selectedTab()) resetFrame();
          else if (data.reset) staleFrame();
          setControlAllowed(Boolean(data.can_control));
          setController(data.controller || (data.mode === "agent" ? "agent" : data.can_control ? "available" : "other"));
          setFollowAgent(data.follow_agent ?? true);
          const previous = status();
          if (previous) applyStatus({ ...previous, ...data }, thread);
          setSelectedTab(nextTab);
        } else if (data.type === "session_closed") {
          resetFrame(); setControlAllowed(false);
          const previous = status();
          if (previous) applyStatus({ ...previous, open: false, generation: null }, thread);
          session().invalidate();
          rootSession.invalidate();
        } else if (data.type === "error") setError(data.message);
      },
    });
    connection = activeConnection;
    onCleanup(() => { disposed = true; if (connection === activeConnection) connection = undefined; activeConnection.dispose(); });
  });

  createEffect(() => {
    if (!props.open || !viewport) return;
    const update = (width: number, height: number) => {
      if (width <= 0 || height <= 0) return;
      setViewportSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect;
      if (rect) update(rect.width, rect.height);
    });
    observer.observe(viewport);
    const style = getComputedStyle(viewport);
    update(viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
      viewport.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom));
    onCleanup(() => observer.disconnect());
  });

  const action = async (operation: () => Promise<unknown>, refreshThread = true) => {
    const thread = props.threadId;
    setWorking(true);
    setError("");
    try {
      await operation();
      if (refreshThread && thread && props.threadId === thread) {
        await rootSession.refresh(true);
      }
    }
    catch (cause) { if (props.threadId === thread) setError(cause instanceof Error ? cause.message : "Browser operation failed."); }
    finally { if (props.threadId === thread) setWorking(false); }
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
  const resumeHandoff = async (decision: BrowserHandoffResume["decision"]) => {
    const pending = status()?.handoff;
    if (!pending || resumeDisabled()) return;
    const payload: BrowserHandoffResume = { action: "browser_handoff", handoff_id: pending.handoff_id, decision };
    const scheduled = status()?.scheduled_run;
    const interrupt = scheduledHandoff();
    if (scheduled && interrupt) {
      setWorking(true);
      setError("");
      try {
        await apiFetch(API_PATHS.schedulerRun(scheduled.run_id, "resume"), {
          method: "POST", json: { interrupt_id: interrupt.id, payload },
        });
        await session().refresh(true);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Scheduled browser task could not be resumed.");
      } finally { setWorking(false); }
      return;
    }
    if (selection.isMain()) { props.onResume(payload); return; }
    const target = childHandoff()!;
    setWorking(true);
    setError("");
    try {
      const task = await apiFetch(`${asyncTaskPath(target.parent_thread_id, target.task_id)}/resume`, {
        method: "POST", json: { run_id: target.run_id, interrupt_id: target.interrupt_id, payload },
      });
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.SUBAGENT_TASK_UPDATED, { detail: task }));
      await rootSession.refresh(true);
      if (selection.threadId() === target.thread_id) await childSession.refresh(true);
    } catch (cause) {
      if (selection.threadId() === target.thread_id) setError(cause instanceof Error ? cause.message : "Browser handoff could not be resumed.");
    } finally {
      if (selection.threadId() === target.thread_id) setWorking(false);
    }
  };

  return <div class="browser-panel">
      <header class="browser-session-header">
        <div class="browser-header-left">
          <Show when={childSessions().length}>
            <label class="browser-session-select">Session
              <select ref={sessionSelect} aria-label="Browser session" value={selection.threadId() || ""}
                onChange={(event) => selection.select(event.currentTarget.value)}>
                <option value={props.threadId || ""}>Main agent</option>
                <For each={childSessions()}>{(item) => <option value={item.thread_id}>{childLabel(item.thread_id, item.agent_name)}</option>}</For>
              </select>
            </label>
          </Show>
          <Show when={status()?.tabs.length}>
            <div class="browser-page-tabs" role="tablist" aria-label="Browser pages">
              <For each={status()?.tabs}>{(tab) => <button type="button" class="browser-page-tab" role="tab"
                aria-selected={selectedTab() === tab.id} title={tab.url} disabled={!connected()}
                onClick={() => {
                  if (!connected() || !connection) return;
                  resetFrame();
                  setControlAllowed(false);
                  setFollowAgent(false);
                  setSelectedTab(tab.id);
                  connection.selectTab(tab.id);
                }}><Globe size={13} /><span>{tab.title || tab.url || "New tab"}</span></button>}</For>
            </div>
          </Show>
          <Show when={status()?.open}>
            <span class="hint browser-lifecycle" role="status">
              {status()?.source === "scheduler" ? "Scheduled task" : selection.isMain() ? "Conversation" : "Subagent"} · {lifecycleLabel()}
            </span>
          </Show>
        </div>
        <div class="browser-actions">
          <Show when={status()?.open && status()?.mode === "agent"}>
            <button class="btn btn-sm" type="button" aria-label="Follow agent" aria-pressed={followAgent()}
              title="Follow the agent's active browser tab" disabled={!connected() || followAgent()}
              onClick={() => connection?.followAgent()}>{followAgent() ? "Following agent" : "Follow agent"}</button>
          </Show>
          <Show when={selection.isMain()}><button class="workspace-icon-btn" type="button" title="Login profiles" aria-label="Login profiles"
            aria-expanded={profilesOpen()} aria-controls="browser-profiles" onClick={() => setProfilesOpen((value) => !value)}><Settings2 size={16} /></button>
          </Show>
          <button class="workspace-icon-btn" type="button" title="Reconnect viewer" aria-label="Reconnect viewer" disabled={!status()?.open}
            onClick={() => { setError(""); setAttempt((value) => value + 1); }}><RefreshCw size={15} /></button>
          <Show when={selection.isMain() || status()?.open}><button class="btn btn-sm" disabled={working() || !selection.threadId() || (props.busy && !status()?.open)}
            onClick={() => void action(() => apiFetch(selectedEndpoint(), { method: status()?.open ? "DELETE" : "POST" }))}>
            <Power size={14} />{status()?.open ? "Close browser" : "Open browser"}
          </button></Show>
        </div>
      </header>
      <Show when={error()}><div class="thread-banner thread-banner-error" role="alert">{error()}</div></Show>
      <Show when={status()?.handoff}>{(pending) => <div class="browser-handoff" role="status">
        <div class="browser-handoff-heading"><ShieldCheck size={17} /><strong>{pending().expired ? "Browser handoff timed out" : status()?.handoff_expired ? "The previous browser session ended" : "Your turn in the browser"}</strong></div>
        <p>{pending().expired
          ? "The browser closed automatically because the handoff timed out. Continue to notify the agent, or cancel this task."
          : status()?.handoff_expired ? "The browser session ended before control was returned. Continue to notify the agent, or cancel this task."
          : pending().reason}</p>
        <div class="browser-actions">
          <button class="btn btn-sm btn-primary" disabled={resumeDisabled()} onClick={() => void resumeHandoff("return")}>
            {pending().expired ? "Continue after timeout" : status()?.handoff_expired ? "Continue after session loss" : "Return control to agent"}
          </button>
          <button class="btn btn-sm" disabled={resumeDisabled()} onClick={() => void resumeHandoff("cancel")}>Cancel browser task</button>
        </div>
      </div>}</Show>
      <Show when={profilesOpen() && selection.isMain()}><section class="browser-profiles" id="browser-profiles" aria-label="Login profile settings">
        <div class="browser-profiles-heading"><strong>Login profiles</strong><span>Reuse a saved login across conversations.</span></div>
        <div class="browser-actions">
        <label class="browser-profile-select">Login profile <select ref={profileSelect} aria-label="Login profile" disabled={!props.threadId || status()?.open || props.busy || working()}
          onChange={(event) => {
            const profileId = event.currentTarget.value || null;
            // A rejected choice changes no state, so nothing else would restore the saved option.
            void action(() => apiFetch(endpoint(), { method: "PUT", json: { mode: profileId ? "profile" : "inherit", profile_id: profileId } })).then(showSelectedProfile);
          }}>
          <option value="">Use project or temporary conversation setting</option>
          <For each={profiles()}>{(profile) => <option value={profile.profile_id}>{profile.name}</option>}</For>
        </select></label>
        <A class="btn btn-sm" href="/settings/browser#login-profiles">Manage login profiles</A>
        </div>
        <p class="browser-profile-hint">Current profile: {profiles().find(profile => profile.profile_id === status()?.profile_id)?.name ?? "Default"}. Close the browser before changing this conversation's choice.</p>
      </section></Show>
      <div class="browser-chrome">
        <div class="browser-navigation" role="group" aria-label="Browser navigation">
          <button class="workspace-icon-btn" type="button" title="Go back" aria-label="Go back" disabled={!canControl()}
            onClick={() => send({ type: "back" })}><ArrowLeft size={16} /></button>
          <button class="workspace-icon-btn" type="button" title="Go forward" aria-label="Go forward" disabled={!canControl()}
            onClick={() => send({ type: "forward" })}><ArrowRight size={16} /></button>
          <button class="workspace-icon-btn" type="button" title="Reload page" aria-label="Reload page" disabled={!canControl()}
            onClick={() => send({ type: "reload" })}><RefreshCw size={16} /></button>
        </div>
        <form class="browser-address" onSubmit={(event) => { event.preventDefault(); if (url().trim()) send({ type: "navigate", url: url().trim() }); }}>
          <Globe size={14} aria-hidden="true" />
          <input aria-label="Browser address" placeholder="Enter a URL"
            disabled={!canControl()} value={url()} onInput={(event) => setUrl(event.currentTarget.value)} />
          <button class="workspace-icon-btn" type="submit" title="Navigate" aria-label="Navigate" disabled={!canControl() || !url().trim()}><ArrowRight size={16} /></button>
        </form>
      </div>
      <div ref={viewport} class="browser-viewport" aria-label="Live browser viewport" aria-description={connectionLabel()} aria-busy={Boolean(status()?.open && !frameFresh())}>
      <Show when={!hasFrame()}><div class="browser-empty">
        <div class="browser-empty-icon"><Show when={status()?.open} fallback={<Globe size={28} />}><LoaderCircle size={28} class="browser-spinner" /></Show></div>
        <strong>{!props.threadId ? "Start a conversation to browse" : status()?.open ? "Connecting to your browser" : "A browser for your agent"}</strong>
        <p>{!props.threadId ? "Your browser session will stay with this conversation." : status()?.open ? "The live page will appear here. You can keep chatting while it connects." : "Open a session or ask your agent to browse. Follow its work here and take control when it needs you."}</p>
      </div></Show>
        <div class="browser-screen" hidden={!hasFrame()} style={{ width: `${screenWidth()}px` }}
          classList={{ "browser-screen-interactive": canControl(), "browser-screen-stale": !frameFresh() }}>
          <Show when={!frameFresh()}><div class="browser-stream-overlay" role="status">{connectionLabel()}</div></Show>
          <canvas ref={screen} role="img" aria-label="Live browser page"
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
      </div>
      <Show when={selection.isMain() && status()?.open && !status()?.handoff}>
        <footer class="browser-footer"><ShieldCheck size={14} /><span>{canControl() ? "Enter credentials directly in the browser, then return control to your agent." : "Ask your agent to hand over control when you need to sign in."}</span></footer>
      </Show>
      <Show when={status()?.images?.length}><details class="browser-captures"><summary><Camera size={14} />Screenshots <span>{status()?.images.length}</span><ChevronDown size={14} /></summary><div class="browser-images"><For each={status()?.images}>{(image) =>
        <a href={image.image_url} target="_blank" rel="noreferrer"><img src={image.image_url} alt="Agent browser screenshot" /></a>
      }</For></div></details></Show>
    </div>;
}
