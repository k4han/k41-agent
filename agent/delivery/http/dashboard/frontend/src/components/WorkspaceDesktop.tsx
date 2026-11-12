import { Hand, LoaderCircle, Monitor, MousePointer2, Power, RefreshCw } from "lucide-solid";
import { createEffect, createSignal, on, onCleanup, onMount, Show } from "solid-js";

import { computerApi, computerViewerUrl, type ComputerState } from "@/lib/computers";
import { errorText } from "@/lib/instabox";
import "@/styles/workspace-desktop.css";

type ConnectionState = "idle" | "starting" | "connecting" | "connected" | "closed";
type RfbInstance = import("@novnc/novnc/lib/rfb").default;

const HEARTBEAT_MS = 10_000;
const READY_TIMEOUT_MS = 20_000;

/** Open the relay socket, send the one-time ticket and wait for the server to accept it. */
function openViewerSocket(ticket: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(computerViewerUrl());
    socket.binaryType = "arraybuffer";
    const timer = window.setTimeout(() => {
      socket.close();
      reject(new Error("The desktop did not answer in time."));
    }, READY_TIMEOUT_MS);
    const fail = (message: string) => {
      window.clearTimeout(timer);
      socket.onmessage = null;
      socket.onclose = null;
      reject(new Error(message));
    };
    socket.onopen = () => socket.send(JSON.stringify({ ticket }));
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const payload = JSON.parse(event.data) as { type?: string; message?: string };
        if (payload.type === "ready") {
          window.clearTimeout(timer);
          socket.onmessage = null;
          socket.onclose = null;
          socket.onopen = null;
          resolve(socket);
        } else if (payload.type === "error") {
          fail(payload.message || "The desktop connection was refused.");
        }
      } catch {
        fail("Unexpected desktop response.");
      }
    };
    socket.onclose = () => fail("The desktop connection closed.");
  });
}

/** Live view and human control of the desktop of any workspace backend with the "desktop" capability. */
export function WorkspaceDesktop(props: { backend: string; locator: string; active: boolean; compact?: boolean }) {
  const [state, setState] = createSignal<ComputerState | null>(null);
  const [connection, setConnection] = createSignal<ConnectionState>("idle");
  const [controlling, setControlling] = createSignal(false);
  const [working, setWorking] = createSignal(false);
  const [error, setError] = createSignal("");
  const [documentVisible, setDocumentVisible] = createSignal(document.visibilityState !== "hidden");
  onMount(() => {
    const changed = () => setDocumentVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", changed);
    onCleanup(() => document.removeEventListener("visibilitychange", changed));
  });
  let screen: HTMLDivElement | undefined;
  let rfb: RfbInstance | undefined;
  let heartbeat: number | undefined;
  let session = 0;

  const stopHeartbeat = () => {
    if (heartbeat !== undefined) window.clearInterval(heartbeat);
    heartbeat = undefined;
  };

  const disconnect = () => {
    session += 1;
    stopHeartbeat();
    if (rfb) {
      try {
        rfb.disconnect();
      } catch {
        // The socket may already be closed.
      }
    }
    rfb = undefined;
    setConnection("idle");
  };

  const handBack = async (backend = props.backend, locator = props.locator) => {
    stopHeartbeat();
    if (rfb) rfb.viewOnly = true;
    if (!controlling()) return;
    setControlling(false);
    try {
      const next = await computerApi.handBack(backend, locator);
      if (props.backend === backend && props.locator === locator) setState(next);
    } catch {
      // Control expires on the server after 30 seconds without a heartbeat.
    }
  };

  const connect = async () => {
    const { backend, locator } = props;
    if (!backend || !locator || !screen) return;
    disconnect();
    const current = session;
    setError("");
    try {
      setConnection("starting");
      const next = await computerApi.start(backend, locator);
      if (current !== session) return;
      setState(next);
      setConnection("connecting");
      const [{ ticket }, module] = await Promise.all([
        computerApi.viewerTicket(backend, locator),
        import("@novnc/novnc/lib/rfb"),
      ]);
      if (current !== session) return;
      const socket = await openViewerSocket(ticket);
      if (current !== session || !screen) {
        socket.close();
        return;
      }
      const RFB = module.default;
      const client = new RFB(screen, socket, { shared: true });
      client.viewOnly = true;
      client.scaleViewport = true;
      client.resizeSession = false;
      client.background = "transparent";
      client.addEventListener("connect", () => {
        if (current === session) setConnection("connected");
      });
      client.addEventListener("disconnect", () => {
        if (current !== session) return;
        stopHeartbeat();
        setControlling(false);
        setConnection("closed");
      });
      rfb = client;
    } catch (err) {
      if (current !== session) return;
      setConnection("closed");
      setError(errorText(err, "Could not open the desktop."));
    }
  };

  const takeControl = async () => {
    const current = session;
    const { backend, locator } = props;
    setWorking(true);
    setError("");
    try {
      const next = await computerApi.takeControl(backend, locator);
      if (current !== session) {
        await computerApi.handBack(backend, locator);
        return;
      }
      setState(next);
      setControlling(true);
      if (rfb) {
        rfb.viewOnly = false;
        rfb.focus();
      }
      stopHeartbeat();
      let renewing = false;
      heartbeat = window.setInterval(() => {
        if (renewing) return;
        renewing = true;
        computerApi.heartbeat(backend, locator).then(next => {
          if (current === session && controlling()) setState(next);
        }).catch((err) => {
          if (current !== session || !controlling()) return;
          stopHeartbeat();
          setControlling(false);
          if (rfb) rfb.viewOnly = true;
          setError(errorText(err, "Desktop control was lost."));
        }).finally(() => { renewing = false; });
      }, HEARTBEAT_MS);
    } catch (err) {
      if (current === session) setError(errorText(err, "Could not take control."));
    } finally {
      setWorking(false);
    }
  };

  const releaseControl = async () => {
    setWorking(true);
    await handBack();
    setWorking(false);
  };

  createEffect(on(() => [props.backend, props.locator, props.active && documentVisible()] as const, ([backend, locator, active], previous) => {
    const changed = previous !== undefined && (previous[0] !== backend || previous[1] !== locator);
    if (changed && previous[0] && previous[1]) void handBack(previous[0], previous[1]);
    if (!active || !backend || !locator) {
      void handBack(backend, locator);
      disconnect();
      return;
    }
    if (connection() === "idle" || changed) void connect();
  }));

  onCleanup(() => {
    void handBack();
    disconnect();
  });

  const otherController = () => {
    const owner = state()?.app_controller;
    return owner && !controlling() ? owner : null;
  };

  return (
    <div class={`workspace-desktop ${props.compact ? "is-compact" : ""}`}>
      <div class="workspace-desktop-toolbar">
        <span class="workspace-desktop-status" role="status">
          <Show when={connection() === "starting" || connection() === "connecting"}>
            <LoaderCircle size={13} class="spinner-animate" />
          </Show>
          <Monitor size={13} />
          {connection() === "starting" ? "Starting desktop…"
            : connection() === "connecting" ? "Connecting…"
            : connection() === "connected" ? controlling() ? "You have control" : otherController() ? `Controlled by ${otherController()}` : "Viewing (agent has control)"
            : connection() === "closed" ? "Disconnected" : "Desktop idle"}
        </span>
        <div class="workspace-desktop-actions">
          <Show when={connection() === "connected"}>
            <Show
              when={controlling()}
              fallback={
                <button class="btn btn-sm" type="button" disabled={working() || Boolean(otherController())} onClick={() => void takeControl()}
                  title="Pause the agent's desktop tools and control the desktop yourself">
                  <Hand size={13} /> Take control
                </button>
              }
            >
              <button class="btn btn-sm btn-primary" type="button" disabled={working()} onClick={() => void releaseControl()}>
                <MousePointer2 size={13} /> Hand back
              </button>
            </Show>
          </Show>
          <Show when={connection() === "closed" || connection() === "idle"}>
            <button class="btn btn-sm" type="button" onClick={() => void connect()}>
              <Show when={connection() === "idle"} fallback={<RefreshCw size={13} />}><Power size={13} /></Show>
              {connection() === "idle" ? "Open desktop" : "Reconnect"}
            </button>
          </Show>
        </div>
      </div>
      <Show when={error()}>
        <p class="workspace-desktop-error" role="alert">{error()}</p>
      </Show>
      <div class={`workspace-desktop-screen ${controlling() ? "is-controlling" : ""}`} ref={screen} />
    </div>
  );
}
