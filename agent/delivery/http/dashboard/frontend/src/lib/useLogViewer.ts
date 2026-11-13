import { createSignal, onCleanup, onMount } from "solid-js";

import { apiFetch } from "./api";
import { API_PATHS } from "./endpoints";

export const LOG_WINDOW_BYTES = 2 * 1024 * 1024;
export const LOG_POLL_MS = 2000;

export type LogPage = {
  source: string;
  text: string;
  bytes: number;
  before: string | null;
  after: string | null;
  has_older: boolean;
  has_newer: boolean;
};

export type LogChunk = { id: number; page: LogPage; size: number };
type Direction = "latest" | "before" | "after";
export type LogSource = { id: string; label: string; available: boolean; error: string | null };

export function trimLogWindow(chunks: LogChunk[], direction: Direction, limit = LOG_WINDOW_BYTES) {
  const result = [...chunks];
  let size = result.reduce((total, chunk) => total + chunk.size, 0);
  while (size > limit && result.length > 1) {
    const removed = direction === "before" ? result.pop()! : result.shift()!;
    size -= removed.size;
  }
  return { chunks: result, trimmed: result.length !== chunks.length };
}

export function useLogViewer(viewport: () => HTMLDivElement | undefined) {
  const [source, setSource] = createSignal("server");
  const [sources, setSources] = createSignal<LogSource[]>([
    { id: "server", label: "Server", available: false, error: null },
    { id: "tray", label: "System tray", available: false, error: null },
    { id: "update", label: "Updater", available: false, error: null },
  ]);
  const [chunks, setChunks] = createSignal<LogChunk[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [loaded, setLoaded] = createSignal(false);
  const [error, setError] = createSignal("");
  const [hasOlder, setHasOlder] = createSignal(false);
  const [hasNewer, setHasNewer] = createSignal(false);
  const [newLogs, setNewLogs] = createSignal(false);
  let boundary: string | null = null;
  let sequence = 0;
  let generation = 0;
  let disposed = false;
  let adjusting = false;
  let following = true;
  let frame: number | undefined;
  let request: AbortController | undefined;
  let failedDirection: Direction = "latest";
  const metadataRequest = new AbortController();

  const nearBottom = () => {
    const element = viewport();
    return !element || element.scrollHeight - element.scrollTop - element.clientHeight <= 48;
  };

  const anchor = () => {
    const element = viewport();
    if (!element) return undefined;
    const top = element.getBoundingClientRect().top;
    for (const node of element.querySelectorAll<HTMLElement>("[data-log-chunk]")) {
      const rect = node.getBoundingClientRect();
      if (rect.bottom >= top) return { node, top: rect.top };
    }
    return undefined;
  };

  const load = async (direction: Direction, probe = false): Promise<void> => {
    if (disposed || loading() || adjusting) return;
    const current = generation;
    if (direction === "before") following = false;
    const wasFollowing = following && nearBottom();
    const cursor = direction === "before" ? chunks()[0]?.page.before : chunks().at(-1)?.page.after ?? boundary;
    if (direction !== "latest" && !cursor) return load("latest");
    request = new AbortController();
    setLoading(true);
    failedDirection = direction;
    try {
      const params = new URLSearchParams();
      if (direction !== "latest" && cursor) params.set(direction, cursor);
      const page = await apiFetch<LogPage>(`${API_PATHS.logContent(source())}?${params}`, {
        signal: request.signal, cache: "no-store",
      });
      if (disposed || current !== generation) return;
      setError("");
      if (probe && !nearBottom()) {
        following = false;
        if (page.text || page.has_newer) {
          setNewLogs(true);
          setHasNewer(true);
        }
        return;
      }
      const follow = direction === "latest" || (direction === "after" && wasFollowing && following && nearBottom());
      following = follow;
      const chunk = { id: ++sequence, page, size: page.bytes };
      const previous = chunks();
      const combined = direction === "latest" ? (page.text ? [chunk] : [])
        : !page.text ? previous
        : direction === "before" ? [chunk, ...previous] : [...previous, chunk];
      const window = trimLogWindow(combined, direction);
      const saved = !follow && combined !== previous ? anchor() : undefined;
      adjusting = true;
      setChunks(window.chunks);
      boundary = page.after;
      if (direction === "latest") {
        setHasOlder(page.has_older);
        setHasNewer(page.has_newer);
        setNewLogs(false);
      } else if (direction === "before") {
        setHasOlder(page.has_older);
        if (window.trimmed) setHasNewer(true);
      } else {
        setHasNewer(page.has_newer);
        if (window.trimmed) setHasOlder(true);
        if (!page.has_newer) setNewLogs(false);
      }
      setLoaded(true);
      frame = requestAnimationFrame(() => {
        if (disposed || current !== generation) return;
        const element = viewport();
        if (element) {
          if (follow) element.scrollTop = element.scrollHeight;
          else if (saved?.node.isConnected) {
            element.scrollTop += saved.node.getBoundingClientRect().top - saved.top;
          }
        }
        adjusting = false;
      });
    } catch (err) {
      if (!disposed && current === generation && !request?.signal.aborted) {
        setError(err instanceof Error ? err.message : "Could not load logs.");
      }
    } finally {
      if (!disposed && current === generation) setLoading(false);
    }
  };

  const resetRequest = () => {
    ++generation;
    request?.abort();
    if (frame !== undefined) cancelAnimationFrame(frame);
    adjusting = false;
    setLoading(false);
    setError("");
  };

  const latest = () => {
    resetRequest();
    following = true;
    void load("latest");
  };

  const selectSource = (value: string) => {
    if (value === source()) return;
    resetRequest();
    following = true;
    setSource(value);
    setChunks([]);
    boundary = null;
    setLoaded(false);
    setHasOlder(false);
    setHasNewer(false);
    setNewLogs(false);
    void load("latest");
  };

  const onScroll = () => {
    const element = viewport();
    if (!element || adjusting) return;
    if (!nearBottom()) following = false;
    else if (!hasNewer()) following = true;
    if (loading() || error()) return;
    if (element.scrollTop < 200 && hasOlder()) void load("before");
    else if (nearBottom() && hasNewer()) void load("after");
  };

  onMount(() => {
    void apiFetch<{ sources: LogSource[] }>(API_PATHS.logs, { signal: metadataRequest.signal, cache: "no-store" })
      .then(payload => { if (!disposed) setSources(payload.sources); })
      .catch(() => { /* Content requests report actionable file errors. */ });
    void load("latest");
    let timer: ReturnType<typeof setInterval> | undefined;
    const poll = () => {
      if (document.visibilityState !== "hidden" && !error()) {
        void load(chunks().length || boundary ? "after" : "latest", true);
      }
    };
    const updatePolling = () => {
      if (timer !== undefined) clearInterval(timer);
      timer = undefined;
      if (document.visibilityState === "hidden") return;
      poll();
      timer = setInterval(poll, LOG_POLL_MS);
    };
    updatePolling();
    document.addEventListener("visibilitychange", updatePolling);
    onCleanup(() => {
      if (timer !== undefined) clearInterval(timer);
      document.removeEventListener("visibilitychange", updatePolling);
    });
  });

  onCleanup(() => {
    disposed = true;
    resetRequest();
    metadataRequest.abort();
  });

  return {
    source, sources, chunks, loading, loaded, error, hasOlder, hasNewer, newLogs,
    selectSource, latest, onScroll,
    older: () => { if (hasOlder() && !error()) void load("before"); },
    retry: () => { setError(""); void load(failedDirection); },
  };
}
