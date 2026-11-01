export type BrowserHandoffResume = {
  action: "browser_handoff";
  handoff_id: string;
  decision: "return" | "cancel";
};

type BrowserViewerMessage =
  | { type: "status"; tabs: { id: string; title: string; url: string }[]; tab_id?: string;
      mode: "agent" | "human"; can_control: boolean; follow_agent?: boolean; reset?: boolean }
  | { type: "error"; message: string };

export type BrowserFrame = { id: number; width: number; height: number; image: Blob };

export function decodeBrowserFrame(data: ArrayBuffer): BrowserFrame {
  if (data.byteLength <= 12) throw new Error("Invalid browser frame.");
  const header = new DataView(data);
  const id = header.getUint32(0), width = header.getUint32(4), height = header.getUint32(8);
  if (!id || !width || !height) throw new Error("Invalid browser frame header.");
  return { id, width, height, image: new Blob([new Uint8Array(data, 12)], { type: "image/jpeg" }) };
}

export function createBrowserFrameRenderer(canvas: () => HTMLCanvasElement | undefined) {
  let revision = 0;
  let cancelPaint: (() => void) | undefined;
  const reset = () => { revision++; cancelPaint?.(); cancelPaint = undefined; };
  return {
    reset,
    async draw(frame: BrowserFrame): Promise<boolean> {
      reset();
      const current = revision;
      let bitmap: ImageBitmap;
      try { bitmap = await createImageBitmap(frame.image); }
      catch (cause) { if (current !== revision) return false; throw cause; }
      if (current !== revision) { bitmap.close(); return false; }
      return new Promise((resolve, reject) => {
        const paint = window.requestAnimationFrame(() => {
          cancelPaint = undefined;
          try {
            if (current !== revision) { resolve(false); return; }
            const target = canvas();
            const context = target?.getContext("2d");
            if (!target || !context) throw new Error("Browser canvas is unavailable.");
            if (target.width !== bitmap.width) target.width = bitmap.width;
            if (target.height !== bitmap.height) target.height = bitmap.height;
            context.drawImage(bitmap, 0, 0);
            resolve(true);
          } catch (cause) { reject(cause); }
          finally { bitmap.close(); }
        });
        cancelPaint = () => { window.cancelAnimationFrame(paint); bitmap.close(); resolve(false); };
      });
    },
  };
}

export function createBrowserInputSender(send: (event: Record<string, unknown>) => void) {
  let pending: Record<string, unknown> | undefined;
  let timer: number | undefined;
  const reset = () => { window.clearTimeout(timer); timer = undefined; pending = undefined; };
  const flush = () => { const event = pending; reset(); if (event) send(event); };
  return {
    reset,
    send(event: Record<string, unknown>) {
      if (event.type === "mouseMoved") {
        pending = event;
        timer ??= window.setTimeout(flush, 40);
      } else { flush(); send(event); }
    },
  };
}

export function connectBrowserViewer(options: {
  requestTicket: (signal: AbortSignal) => Promise<{ ticket: string }>;
  onSocket: (socket: WebSocket | undefined) => void;
  onMessage: (message: BrowserViewerMessage) => void;
  onFrame: (frame: BrowserFrame) => Promise<unknown>;
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
      current.binaryType = "arraybuffer";
      socket = current;
      options.onSocket(current);
      current.onopen = () => { if (socket === current) current.send(JSON.stringify({ ticket })); };
      current.onmessage = (event) => {
        if (disposed || socket !== current) return;
        try {
          if (typeof event.data !== "string") {
            const frame = decodeBrowserFrame(event.data as ArrayBuffer);
            clearConnectionTimeout();
            failures = 0;
            void options.onFrame(frame).then(() => {
              if (!disposed && socket === current && current.readyState === WebSocket.OPEN) {
                current.send(JSON.stringify({ type: "frame_ack", frame_id: frame.id }));
              }
            }).catch((cause) => { if (!disposed && socket === current) failed(cause); });
            return;
          }
          const message = JSON.parse(event.data) as BrowserViewerMessage;
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
