export type BrowserHandoffResume = {
  action: "browser_handoff";
  handoff_id: string;
  decision: "return" | "cancel";
};

export type BrowserViewerMessage =
  | { type: "status"; tabs: { id: string; title: string; url: string }[]; tab_id?: string;
      mode: "agent" | "human"; can_control: boolean; follow_agent?: boolean; reset?: boolean;
      generation?: string; revision?: number; sequence?: number; open?: boolean;
      controller?: "agent" | "available" | "you" | "other"; paused?: boolean;
      visibility_revision?: number;
      handoff?: { handoff_id: string; reason: string } | null; handoff_expired?: boolean;
      images?: { artifact_id: string; image_url: string }[] }
  | { type: "error"; message: string }
  | { type: "session_closed" };

export type BrowserConnectionState = "connecting" | "connected" | "reconnecting" | "paused";
export type BrowserStreamMetrics = {
  firstFrameMs: number; decodeMs: number; renderMs: number; ackMs: number; heartbeatMs: number;
  bytesPerSecond: number; framesPerSecond: number; reconnects: number; droppedFrames: number;
  inputAckMs: number; inputToFrameMs: number; quality: number;
};

export type BrowserFrame = { id: number; width: number; height: number; image: Blob };

export function decodeBrowserFrame(data: ArrayBuffer): BrowserFrame {
  if (data.byteLength <= 12 || data.byteLength > 16 * 1024 * 1024) throw new Error("Invalid browser frame.");
  const header = new DataView(data);
  const id = header.getUint32(0), width = header.getUint32(4), height = header.getUint32(8);
  if (!id || !width || !height || width > 8192 || height > 8192) throw new Error("Invalid browser frame header.");
  return { id, width, height, image: new Blob([new Uint8Array(data, 12)], { type: "image/jpeg" }) };
}

export function createBrowserFrameRenderer(canvas: () => HTMLCanvasElement | undefined) {
  let revision = 0;
  let cancelPaint: (() => void) | undefined;
  let timing = { decodeMs: 0, renderMs: 0 };
  const reset = () => { revision++; cancelPaint?.(); cancelPaint = undefined; };
  return {
    reset,
    get timing() { return timing; },
    async draw(frame: BrowserFrame): Promise<boolean> {
      reset();
      const current = revision;
      const started = performance.now();
      let bitmap: ImageBitmap;
      try { bitmap = await createImageBitmap(frame.image); }
      catch (cause) { if (current !== revision) return false; throw cause; }
      if (current !== revision) { bitmap.close(); return false; }
      const decoded = performance.now();
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
            timing = { decodeMs: decoded - started, renderMs: performance.now() - decoded };
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

export type BrowserViewerConnection = ReturnType<typeof connectBrowserViewer>;

export function connectBrowserViewer(options: {
  requestTicket: (signal: AbortSignal) => Promise<{ ticket: string }>;
  onSocket?: (socket: WebSocket | undefined) => void;
  onMessage: (message: BrowserViewerMessage) => void;
  onFrame: (frame: BrowserFrame) => Promise<boolean | void>;
  onDisconnect: () => void;
  onError: (cause: unknown) => void;
  onStateChange?: (state: BrowserConnectionState) => void;
  onPause?: (paused: boolean) => void;
  onMetrics?: (metrics: BrowserStreamMetrics) => void;
  frameTiming?: () => { decodeMs: number; renderMs: number };
  selection?: { tabId: string; followAgent: boolean };
  width?: number;
}) {
  const page = typeof document === "undefined" ? undefined : document;
  let visible = page?.visibilityState !== "hidden";
  let disposed = false, authenticated = false, established = false;
  let socket: WebSocket | undefined, request: AbortController | undefined;
  let retry: number | undefined, timeout: number | undefined, frameTimeout: number | undefined;
  let heartbeat: number | undefined, paintTimeout: number | undefined;
  let failures = 0, revision = 0, messageSequence = 0, inputSequence = 0, pingSequence = 0;
  let selection = options.selection || { tabId: "", followAgent: true };
  let selectionPending = false, awaitingResume = false, visibilityRevision = 0;
  let width = options.width || 1280, handoffId: string | undefined;
  let pending: { frame: BrowserFrame; received: number } | undefined;
  let drawing = false, firstFrame = true, started = performance.now();
  let sampleAt = started, receivedBytes = 0, paintedFrames = 0;
  let ping: { id: number; sent: number } | undefined;
  const inputs = new Map<number, number>();
  let appliedInput: { sent: number; acknowledged: number } | undefined;
  const metrics: BrowserStreamMetrics = { firstFrameMs: 0, decodeMs: 0, renderMs: 0, ackMs: 0,
    heartbeatMs: 0, bytesPerSecond: 0, framesPerSecond: 0, reconnects: 0, droppedFrames: 0,
    inputAckMs: 0, inputToFrameMs: 0, quality: 65 };
  const publishMetrics = () => {
    const elapsed = (performance.now() - sampleAt) / 1000;
    if (elapsed >= 1) {
      metrics.bytesPerSecond = receivedBytes / elapsed;
      metrics.framesPerSecond = paintedFrames / elapsed;
      sampleAt = performance.now(); receivedBytes = 0; paintedFrames = 0;
    }
    options.onMetrics?.({ ...metrics });
  };
  const send = (payload: Record<string, unknown>) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  };
  const clearFrameTimeout = () => { window.clearTimeout(frameTimeout); frameTimeout = undefined; };
  const expectFrame = () => {
    clearFrameTimeout();
    if (visible) frameTimeout = window.setTimeout(() => failed(new Error("Browser image timed out. Reconnecting.")), 15000);
  };
  const acknowledge = (frame: BrowserFrame, drawn: boolean, renderMs = 0) => {
    if (!drawn) metrics.droppedFrames++;
    send({ type: "frame_ack", frame_id: frame.id, drawn, render_ms: renderMs });
  };
  const disconnect = () => {
    window.clearTimeout(timeout); timeout = undefined;
    window.clearTimeout(heartbeat); heartbeat = undefined;
    window.clearTimeout(paintTimeout); paintTimeout = undefined;
    clearFrameTimeout();
    revision++; pending = undefined; drawing = false; authenticated = false;
    inputs.clear(); appliedInput = undefined; ping = undefined;
    request?.abort(); request = undefined;
    const previous = socket; socket = undefined;
    previous?.close();
    options.onSocket?.(undefined);
    options.onDisconnect();
  };
  const failed = (cause?: unknown) => {
    if (disposed) return;
    disconnect();
    options.onStateChange?.(visible ? established ? "reconnecting" : "connecting" : "paused");
    if (cause) options.onError(cause);
    if (retry === undefined) {
      const delay = Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)) + Math.random() * 250;
      retry = window.setTimeout(() => { retry = undefined; metrics.reconnects++; void connect(); }, delay);
    }
  };
  const paintNext = async () => {
    if (drawing || !pending || !visible || disposed) return;
    const current = socket, version = revision, item = pending;
    pending = undefined; drawing = true;
    const began = performance.now();
    paintTimeout = window.setTimeout(() => failed(new Error("Browser rendering timed out. Reconnecting.")), 10000);
    try {
      const drawn = await options.onFrame(item.frame) !== false;
      if (disposed || socket !== current || version !== revision) return;
      window.clearTimeout(paintTimeout); paintTimeout = undefined;
      acknowledge(item.frame, drawn, performance.now() - began);
      if (drawn) {
        clearFrameTimeout();
        failures = 0;
        paintedFrames++;
        const timing = options.frameTiming?.();
        metrics.decodeMs = timing?.decodeMs || 0;
        metrics.renderMs = timing?.renderMs ?? performance.now() - began;
        if (firstFrame) { metrics.firstFrameMs = performance.now() - started; firstFrame = false; }
        if (appliedInput && item.received >= appliedInput.acknowledged) {
          metrics.inputToFrameMs = performance.now() - appliedInput.sent;
          appliedInput = undefined;
        }
        publishMetrics();
      }
    } catch (cause) {
      if (!disposed && socket === current && version === revision) failed(cause);
    } finally {
      if (socket === current && version === revision) { drawing = false; void paintNext(); }
    }
  };
  const scheduleHeartbeat = () => {
    window.clearTimeout(heartbeat);
    heartbeat = window.setTimeout(() => {
      if (!socket || disposed) return;
      if (visible && ping && performance.now() - ping.sent >= 15000) {
        failed(new Error("Browser connection stopped responding. Reconnecting.")); return;
      }
      if (!ping || !visible) {
        ping = { id: ++pingSequence % 0xFFFFFFFF, sent: performance.now() };
        send({ type: "ping", id: ping.id });
      }
      publishMetrics();
      scheduleHeartbeat();
    }, 5000);
  };
  const connect = async () => {
    const controller = new AbortController();
    request = controller;
    started = performance.now(); firstFrame = true; messageSequence = 0;
    options.onStateChange?.(visible ? established ? "reconnecting" : "connecting" : "paused");
    timeout = window.setTimeout(() => failed(new Error("Browser connection timed out. Retrying.")), 10000);
    try {
      const { ticket } = await options.requestTicket(controller.signal);
      if (disposed || controller.signal.aborted) return;
      const address = new URL("/dashboard-api/browser/viewer", window.location.href);
      address.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const current = new WebSocket(address);
      current.binaryType = "arraybuffer";
      socket = current;
      options.onSocket?.(current);
      current.onopen = () => {
        if (socket !== current) return;
        send({ ticket, visible, width, tab_id: selection.tabId, follow_agent: selection.followAgent,
          visibility_revision: visibilityRevision });
        scheduleHeartbeat();
      };
      current.onmessage = (event) => {
        if (disposed || socket !== current) return;
        try {
          if (typeof event.data !== "string") {
            const frame = decodeBrowserFrame(event.data as ArrayBuffer);
            receivedBytes += event.data.byteLength;
            if (!visible || awaitingResume) { acknowledge(frame, false); return; }
            if (pending) acknowledge(pending.frame, false);
            pending = { frame, received: performance.now() };
            void paintNext();
            return;
          }
          const message = JSON.parse(event.data);
          if (message.type === "pong") {
            const outstanding = ping;
            if (outstanding && outstanding.id === message.id) { metrics.heartbeatMs = performance.now() - outstanding.sent; ping = undefined; }
            metrics.ackMs = message.metrics?.ack_ms || 0;
            metrics.quality = message.metrics?.quality || 65;
            publishMetrics(); return;
          }
          if (message.type === "input_ack") {
            const sent = inputs.get(message.input_id);
            if (sent !== undefined) {
              inputs.delete(message.input_id);
              metrics.inputAckMs = performance.now() - sent;
              appliedInput = { sent, acknowledged: performance.now() };
            }
            return;
          }
          if (message.type === "status") {
            if (message.sequence !== undefined && message.sequence <= messageSequence) return;
            messageSequence = message.sequence ?? messageSequence + 1;
            const firstStatus = !authenticated;
            if (firstStatus) {
              authenticated = true; established = true;
              window.clearTimeout(timeout); timeout = undefined;
              expectFrame();
            }
            handoffId = message.handoff?.handoff_id;
            if (firstStatus || !selectionPending || (message.tab_id === selection.tabId && message.follow_agent === selection.followAgent)) {
              selection = { tabId: message.tab_id || "", followAgent: message.follow_agent ?? true };
              selectionPending = false;
            }
            if (message.paused === false && (message.visibility_revision ?? 0) >= visibilityRevision) awaitingResume = false;
            if (message.reset) {
              revision++; drawing = false;
              if (pending) acknowledge(pending.frame, false);
              pending = undefined;
              window.clearTimeout(paintTimeout); paintTimeout = undefined;
              expectFrame();
            }
            options.onStateChange?.(visible ? "connected" : "paused");
          }
          if (message.type === "session_closed") {
            disconnect();
            options.onMessage(message);
            return;
          }
          options.onMessage(message as BrowserViewerMessage);
        } catch (cause) { failed(cause); }
      };
      current.onclose = () => { if (socket === current) failed(); };
      current.onerror = () => { if (socket === current) failed(new Error("Browser connection failed. Retrying.")); };
    } catch (cause) {
      if (!disposed && !controller.signal.aborted) failed(cause);
    }
  };
  const visibilityChanged = () => {
    const next = page?.visibilityState !== "hidden";
    if (next === visible) return;
    visible = next;
    visibilityRevision++;
    awaitingResume = visible;
    revision++; drawing = false;
    if (pending) acknowledge(pending.frame, false);
    pending = undefined; appliedInput = undefined; inputs.clear();
    window.clearTimeout(paintTimeout); paintTimeout = undefined;
    clearFrameTimeout(); ping = undefined;
    options.onPause?.(!visible);
    send({ type: "visibility", visible, width, revision: visibilityRevision });
    options.onStateChange?.(!visible ? "paused" : authenticated ? "connected" : "reconnecting");
    if (visible) {
      if (authenticated) expectFrame();
      if (socket?.readyState === WebSocket.OPEN) {
        ping = { id: ++pingSequence % 0xFFFFFFFF, sent: performance.now() };
        send({ type: "ping", id: ping.id }); scheduleHeartbeat();
      }
    }
  };
  page?.addEventListener("visibilitychange", visibilityChanged);
  void connect();
  return {
    dispose() {
      disposed = true;
      window.clearTimeout(retry);
      page?.removeEventListener("visibilitychange", visibilityChanged);
      disconnect();
    },
    sendInput(event: Record<string, unknown>) {
      if (!visible || !authenticated) return;
      const id = ++inputSequence % 0xFFFFFFFF || ++inputSequence % 0xFFFFFFFF;
      if (inputs.size >= 64) inputs.delete(inputs.keys().next().value!);
      inputs.set(id, performance.now());
      send({ ...event, input_id: id, ...(handoffId ? { handoff_id: handoffId } : {}) });
    },
    selectTab(tabId: string) {
      selection = { tabId, followAgent: false };
      selectionPending = true;
      send({ type: "select_tab", tab_id: tabId });
    },
    followAgent() { selection.followAgent = true; selectionPending = true; send({ type: "follow_agent" }); },
    resize(value: number) {
      const next = Math.max(320, Math.min(1280, Math.round(value / 80) * 80));
      if (width === next || !Number.isFinite(next)) return;
      width = next;
      send({ type: "resize", width });
    },
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
