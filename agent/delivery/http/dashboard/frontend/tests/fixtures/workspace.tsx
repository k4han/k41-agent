import { handleProtocolEvent } from "@/lib/protocolStreamHandler";
import { createSignal, Show } from "solid-js";
import { render } from "solid-js/web";

import { ToastProvider } from "@/components/Toast";
import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { fetchCatalog } from "@/lib/catalogStore";
import { readNDJSONStream } from "@/lib/chatStreamHandler";
import type { StreamCallbacks } from "@/lib/chatStreamHandler";
import "@/styles.css";

document.getElementById("root")!.style.height = "640px";

const entry = (path: string, kind = "file", size = 10) => ({
  path, name: path.split("/").pop(), kind, size, modified_at: 1,
});
const serviceTests = new URLSearchParams(window.location.search).has("services");
const largeWorkspace = new URLSearchParams(window.location.search).has("large");
let entries = [entry("src", "directory"), ...Array.from({ length: largeWorkspace ? 5000 : 200 }, (_, i) => entry(`file-${i}.txt`))];
let changes = serviceTests ? [] : Array.from({ length: largeWorkspace ? 5000 : 1 }, (_, i) => ({ path: `file-${i}.txt`, status: "modified", additions: 1, deletions: 0 }));
let browserStatus = { open: false, generation: null as string | null, revision: 0, profile_id: null,
  mode: "agent", tabs: [], handoff: null as { handoff_id: string; reason: string; expired: boolean } | null,
  handoff_expired: false, images: [] };
let computerRunning = false;
const serviceRequests: { kind: string; method: string }[] = [];
const requests: { kind: string; path: string; locator: string | null }[] = [];
const held = new Set<string>();
const pending = new Map<string, (() => void)[]>();
const nativeFetch = window.fetch.bind(window);
let aborted = 0;
let contentRevision = 0;
let nestedImageRevision = 1;
let previewEntriesAdded = false;
const previewFiles: Record<string, { content: string; binary?: boolean }> = {
  "readme.md": { content: '# Workspace document\n\n**Formatted text**\n\n<div><style>body { --workspace-injected-style: injected; }</style><span style="position:fixed;inset:0">Untrusted styles</span></div>' },
  "page.html": { content: '<h1>HTML document</h1><script>parent.workspaceCompromised = true</script>' },
  "data.csv": { content: 'name,value\n"one,two","<script>text only</script>"' },
  "image.svg": { content: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="green"/></svg>' },
  "src/image.svg": { content: "", binary: true },
  "report.pdf": { content: "", binary: true },
  "song.mp3": { content: "", binary: true },
  "movie.mp4": { content: "", binary: true },
  "archive.zip": { content: "", binary: true },
};
let emptyWorkspace = false;
const activeRequests = new Map<string, number>();
const pollTimers = new Map<number, () => void>();
const pollIntervals = new Map<number, number>();
const nativeNow = Date.now.bind(Date);
let clockOffset = 0;
Date.now = () => nativeNow() + clockOffset;
const nativeSetTimeout = window.setTimeout.bind(window);
const nativeClearTimeout = window.clearTimeout.bind(window);
window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
  let id: number;
  const callback = () => { nativeClearTimeout(id); pollTimers.delete(id); pollIntervals.delete(id); if (typeof handler === "function") handler(...args); };
  const isPoll = (timeout === 120_000 || timeout === 60_000 || timeout === 5_000 || timeout === 2_000) && typeof handler === "function";
  id = nativeSetTimeout(isPoll ? callback : handler, timeout, ...args);
  if (isPoll) {
    pollTimers.set(id, callback);
    pollIntervals.set(id, timeout!);
  }
  return id;
}) as typeof window.setTimeout;
window.clearTimeout = (id) => { pollTimers.delete(id!); pollIntervals.delete(id!); nativeClearTimeout(id); };

window.fetch = async (input, init) => {
  const url = new URL(String(input), window.location.href);
  if (serviceTests && url.pathname === "/dashboard-api/catalog") return new Response(JSON.stringify({ backends: [
    { name: "local", title: "Local", enabled: true, capabilities: ["desktop"] },
  ] }), { headers: { "content-type": "application/json" } });
  if (serviceTests && /^\/dashboard-api\/browser\/threads\/[^/]+$/.test(url.pathname)) {
    serviceRequests.push({ kind: "browser", method: init?.method || "GET" });
    return new Response(JSON.stringify(browserStatus), { headers: { "content-type": "application/json" } });
  }
  if (serviceTests && url.pathname.startsWith("/dashboard-api/computers/")) {
    serviceRequests.push({ kind: "computer", method: init?.method || "GET" });
    return new Response(JSON.stringify({ running: computerRunning, app_controller: null }), { headers: { "content-type": "application/json" } });
  }
  if (!url.pathname.startsWith("/dashboard-api/workspace/")) return nativeFetch(input, init);
  const kind = url.pathname.split("/").pop()!;
  const path = url.searchParams.get("path") || "";
  const locator = url.searchParams.get("locator");
  requests.push({ kind, path, locator });
  let payload: unknown;
  if (kind === "tree") {
    payload = {
      root: locator, path,
      entries: emptyWorkspace ? [] : locator === "/next" ? [entry("next.txt")]
        : path === "src" ? [entry("src/nested.txt"), ...(previewEntriesAdded ? [{ ...entry("src/image.svg"), modified_at: nestedImageRevision }] : [])] : entries,
      truncated: false,
    };
  } else if (kind === "changes") {
    payload = { root: locator, is_git_repo: !emptyWorkspace, changes: emptyWorkspace ? [] : changes, message: emptyWorkspace ? "Workspace is not a Git repository." : "" };
  } else if (kind === "file") {
    payload = { root: locator, path, content: `Preview of ${path} revision ${contentRevision}`, binary: false, truncated: false, size: 20, modified_at: path === "src/image.svg" ? nestedImageRevision : 1, mime_type: "text/plain", message: "", ...previewFiles[path] };
  } else if (kind === "diff") {
    payload = { root: locator, path, diff: largeWorkspace ? `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n@@ -1 +1 @@\n-old\n+new\n` : "", message: `Diff of ${path} revision ${contentRevision}`, truncated: false, is_git_repo: true, status: "modified" };
  } else {
    throw new Error(`Unexpected workspace request: ${kind}`);
  }
  const body = JSON.stringify(payload);
  const key = `${kind}:${path}`;
  activeRequests.set(key, (activeRequests.get(key) || 0) + 1);
  try {
    if (held.has(key)) {
      await new Promise<void>((resolve, reject) => {
        const abort = () => {
          aborted += 1;
          reject(new DOMException("Aborted", "AbortError"));
        };
        if (init?.signal?.aborted) return abort();
        init?.signal?.addEventListener("abort", abort, { once: true });
        pending.set(key, [...(pending.get(key) || []), () => {
          init?.signal?.removeEventListener("abort", abort);
          resolve();
        }]);
      });
    }
    return new Response(body, { headers: { "content-type": "application/json" } });
  } finally {
    activeRequests.set(key, (activeRequests.get(key) || 1) - 1);
  }
};

function WorkspaceFixture() {
  const [locator, setLocator] = createSignal("/project");
  const [mounted, setMounted] = createSignal(true);
  const [revision, setRevision] = createSignal(0);
  const [visible, setVisible] = createSignal(true);
  const [running, setRunning] = createSignal(false);
  const [threadId, setThreadId] = createSignal("thread-one");
  Object.assign(window, {
    workspaceTest: {
      requests,
      serviceRequests,
      isVisible: visible,
      setThreadId,
      advanceTime: (ms: number) => { clockOffset += ms; },
      setChanges: (changed: boolean) => { changes = changed ? [{ path: "file-0.txt", status: "modified", additions: 1, deletions: 0 }] : []; },
      setBrowser: (generation: string | null, handoff = false) => {
        browserStatus = { ...browserStatus, open: Boolean(generation), generation,
          handoff: handoff ? { handoff_id: "handoff-one", reason: "Sign in", expired: true } : null };
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, { detail: { thread_id: threadId() } }));
      },
      setComputer: (running: boolean) => {
        computerRunning = running;
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { detail: { backend: "local", locator: locator() } }));
      },
      addPreviewEntries: () => {
        previewEntriesAdded = true;
        entries = [entries[0], ...Object.keys(previewFiles).filter(path => !path.includes("/")).map(path => entry(path)), ...entries.slice(1)];
      },
      updateNestedImage: () => { nestedImageRevision += 1; },
      refreshing: () => [...activeRequests].some(([key, count]) => count > 0 && (key.startsWith("tree:") || key.startsWith("changes:"))),
      emptyWorkspace: () => { emptyWorkspace = true; },
      setVisible,
      setRunning,
      setDocumentVisible: (visible: boolean) => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: visible ? "visible" : "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      },
      poll: (interval?: number) => { for (const [id, callback] of [...pollTimers]) if (!interval || pollIntervals.get(id) === interval) callback(); },
      pollIntervals: () => [...pollIntervals.values()],
      pollCount: () => pollTimers.size,
      updateContent: () => { contentRevision += 1; },
      toolResult: (threadId = "thread-one") => readNDJSONStream(
        new ReadableStream({ start(controller) {
          const encoded = new TextEncoder().encode(JSON.stringify({ method: "custom:k41", params: {
            data: { projection: "ui", type: "tool_result", name: "shell", content: "done" },
          } }) + "\n");
          controller.enqueue(encoded.slice(0, 20));
          controller.enqueue(encoded.slice(20));
          controller.close();
        } }),
        (event) => handleProtocolEvent(
          event, { id: null }, { received: false }, { id: threadId, message: "" },
          { updateToolResult: () => {} } as unknown as StreamCallbacks,
        ),
      ),
      setLocator,
      unmount: () => setMounted(false),
      replaceWorkspaceObject: () => setRevision((value) => value + 1),
      hold: (key: string) => held.add(key),
      release: (key: string) => {
        held.delete(key);
        for (const resolve of pending.get(key) || []) resolve();
        pending.delete(key);
      },
      pendingCount: (key: string) => pending.get(key)?.length || 0,
      aborted: () => aborted,
      updateEntries: () => {
        entries = [entries[0], entries.at(-1)!, { ...entries[1], size: 4096 }, ...entries.slice(2, -1).reverse()];
        changes = [{ ...changes[0], additions: 5 }];
      },
      removeEntry: () => { entries = entries.filter((item) => item.path !== "file-199.txt"); },
      refresh: (threadId = "thread-one") => window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.WORKSPACE_CHANGED, { detail: { threadId } })),
    },
  });
  return (
    <ToastProvider>
      <Show when={mounted()}>
        <WorkspaceExplorer
          visible={visible()}
          running={running()}
          disabled={running()}
          threadId={threadId()}
          onBrowserResume={serviceTests ? () => {} : undefined}
          onBrowserOpen={serviceTests ? () => setVisible(true) : undefined}
          workingDir={locator()}
          workspace={{ backend: "local", locator: locator(), label: `Project ${revision()}`, metadata: {} }}
          onWorkingDirChange={() => {}}
        />
      </Show>
    </ToastProvider>
  );
}

if (serviceTests) void fetchCatalog();
render(() => <WorkspaceFixture />, document.getElementById("root")!);
