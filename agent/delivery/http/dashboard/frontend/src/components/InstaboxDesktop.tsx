import { Hand, LoaderCircle, Monitor, MousePointer2, Power, RefreshCw } from "lucide-solid";
import { createEffect, createSignal, on, onCleanup, Show } from "solid-js";

import { errorText, instaboxApi, instaboxViewerUrl, type InstaboxDesktopState } from "@/lib/instabox";
import "@/styles/instabox.css";

type ConnectionState = "idle" | "starting" | "connecting" | "connected" | "closed";
type RfbInstance = import("@novnc/novnc/lib/rfb").default;

const HEARTBEAT_MS = 10_000;
const READY_TIMEOUT_MS = 20_000;

/** Open the relay socket, send the one-time ticket and wait for the server to accept it. */
function openViewerSocket(ticket: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(instaboxViewerUrl());
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

export function InstaboxDesktop(props: { machineId: string; active: boolean; compact?: boolean }) {
  const [state, setState] = createSignal<InstaboxDesktopState | null>(null);
  const [connection, setConnection] = createSignal<ConnectionState>("idle");
  const [controlling, setControlling] = createSignal(false);
  const [working, setWorking] = createSignal(false);
  const [error, setError] = createSignal("");
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

  const handBack = async (machineId = props.machineId) => {
    stopHeartbeat();
    if (rfb) rfb.viewOnly = true;
    if (!controlling()) return;
    setControlling(false);
    try {
      setState(await instaboxApi.handBack(machineId));
    } catch {
      // Control expires on the server after 30 seconds without a heartbeat.
    }
  };

  const connect = async () => {
    const machineId = props.machineId;
    if (!machineId || !screen) return;
    disconnect();
    const current = session;
    setError("");
    try {
      setConnection("starting");
      setState(await instaboxApi.startDesktop(machineId));
      if (current !== session) return;
      setConnection("connecting");
      const [{ ticket }, module] = await Promise.all([
        instaboxApi.viewerTicket(machineId),
        import("@novnc/novnc/lib/rfb"),
      ]);
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
    setWorking(true);
    setError("");
    try {
      setState(await instaboxApi.takeControl(props.machineId));
      setControlling(true);
      if (rfb) {
        rfb.viewOnly = false;
        rfb.focus();
      }
      stopHeartbeat();
      const machineId = props.machineId;
      heartbeat = window.setInterval(() => {
        instaboxApi.heartbeat(machineId).then(setState).catch((err) => {
          stopHeartbeat();
          setControlling(false);
          if (rfb) rfb.viewOnly = true;
          setError(errorText(err, "Desktop control was lost."));
        });
      }, HEARTBEAT_MS);
    } catch (err) {
      setError(errorText(err, "Could not take control."));
    } finally {
      setWorking(false);
    }
  };

  const releaseControl = async () => {
    setWorking(true);
    await handBack();
    setWorking(false);
  };

  createEffect(on(() => [props.machineId, props.active] as const, ([machineId, active], previous) => {
    const previousMachine = previous?.[0];
    if (previousMachine && previousMachine !== machineId) void handBack(previousMachine);
    if (!active || !machineId) {
      void handBack(machineId);
      disconnect();
      return;
    }
    if (connection() === "idle" || previousMachine !== machineId) void connect();
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
    <div class={`instabox-desktop ${props.compact ? "is-compact" : ""}`}>
      <div class="instabox-desktop-toolbar">
        <span class="instabox-desktop-status" role="status">
          <Show when={connection() === "starting" || connection() === "connecting"}>
            <LoaderCircle size={13} class="spinner-animate" />
          </Show>
          <Monitor size={13} />
          {connection() === "starting" ? "Starting desktop…"
            : connection() === "connecting" ? "Connecting…"
            : connection() === "connected" ? controlling() ? "You have control" : otherController() ? `Controlled by ${otherController()}` : "Viewing (agent has control)"
            : connection() === "closed" ? "Disconnected" : "Desktop idle"}
        </span>
        <div class="instabox-desktop-actions">
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
        <p class="instabox-desktop-error" role="alert">{error()}</p>
      </Show>
      <div class={`instabox-desktop-screen ${controlling() ? "is-controlling" : ""}`} ref={screen} />
    </div>
  );
}
