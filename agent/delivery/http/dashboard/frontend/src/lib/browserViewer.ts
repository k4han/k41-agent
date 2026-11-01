export type BrowserHandoffResume = {
  action: "browser_handoff";
  handoff_id: string;
  decision: "return" | "cancel";
};

type BrowserViewerMessage =
  | { type: "frame"; data: string; metadata: { deviceWidth?: number; deviceHeight?: number } }
  | { type: "status"; tabs: { id: string; title: string; url: string }[]; tab_id?: string;
      mode: "agent" | "human"; can_control: boolean; follow_agent?: boolean; reset?: boolean }
  | { type: "error"; message: string };

export function connectBrowserViewer(options: {
  requestTicket: (signal: AbortSignal) => Promise<{ ticket: string }>;
  onSocket: (socket: WebSocket | undefined) => void;
  onMessage: (message: BrowserViewerMessage) => void;
  onDisconnect: () => void;
  onError: (cause: unknown) => void;
}) {
  let disposed = false;
  let socket: WebSocket | undefined;
  let request: AbortController | undefined;
  let retry: number | undefined;
  let timeout: number | undefined;
  let failures = 0;
  const clearConnectionTimeout = () => { window.clearTimeout(timeout); timeout = undefined; };
  const disconnect = () => {
    clearConnectionTimeout();
    request?.abort();
    request = undefined;
    const previous = socket;
    socket = undefined;
    previous?.close();
    options.onSocket(undefined);
    options.onDisconnect();
  };
  const failed = (cause?: unknown) => {
    if (disposed) return;
    disconnect();
    if (cause) options.onError(cause);
    if (retry === undefined) {
      const delay = Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)) + Math.random() * 250;
      retry = window.setTimeout(() => { retry = undefined; void connect(); }, delay);
    }
  };
  const connect = async () => {
    const controller = new AbortController();
    request = controller;
    timeout = window.setTimeout(() => failed(new Error("Browser connection timed out. Retrying.")), 10000);
    try {
      const { ticket } = await options.requestTicket(controller.signal);
      if (disposed || controller.signal.aborted) return;
      const address = new URL("/dashboard-api/browser/viewer", window.location.href);
      address.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const current = new WebSocket(address);
      socket = current;
      options.onSocket(current);
      current.onopen = () => { if (socket === current) current.send(JSON.stringify({ ticket })); };
      current.onmessage = (event) => {
        if (disposed || socket !== current) return;
        try {
          const message = JSON.parse(event.data) as BrowserViewerMessage;
          if (message.type === "frame") { clearConnectionTimeout(); failures = 0; }
          options.onMessage(message);
        } catch (cause) { failed(cause); }
      };
      current.onclose = () => { if (socket === current) failed(); };
      current.onerror = () => { if (socket === current) failed(new Error("Browser connection failed. Retrying.")); };
    } catch (cause) {
      if (!disposed && !controller.signal.aborted) failed(cause);
    }
  };
  void connect();
  return () => {
    disposed = true;
    window.clearTimeout(retry);
    disconnect();
  };
}

export function browserPointer(clientX: number, clientY: number,
  rect: { left: number; top: number; width: number; height: number }, width = 1280, height = 800) {
  return {
    x: Math.max(0, Math.min(width, (clientX - rect.left) * width / Math.max(1, rect.width))),
    y: Math.max(0, Math.min(height, (clientY - rect.top) * height / Math.max(1, rect.height))),
  };
}

export function browserModifiers(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
  return Number(event.altKey) | (Number(event.ctrlKey) << 1) | (Number(event.metaKey) << 2) | (Number(event.shiftKey) << 3);
}

export function browserImageUrl(result: unknown): string | null {
  if (typeof result !== "string") return null;
  return result.match(/Browser image: (\/dashboard-api\/browser\/artifacts\/[a-f0-9]{32})\b/)?.[1] || null;
}
