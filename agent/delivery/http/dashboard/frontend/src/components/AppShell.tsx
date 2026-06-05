import { A, useLocation, useNavigate } from "@solidjs/router";
import {
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FolderOpen,
  GitPullRequest,
  History,
  Home,
  LogOut,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  PlaySquare,
  RefreshCw,
  Settings,
  Square,
  Trash2,
} from "lucide-solid";
import { createMemo, createSignal, For, JSX, onCleanup, onMount, Show } from "solid-js";

import { DeleteThreadDialog } from "@/components/DeleteThreadDialog";
import { InlineRenameInput } from "@/components/InlineRenameInput";
import { SelectControl } from "@/components/SelectControl";
import { apiFetch, deleteJson, patchJson, postJson } from "@/lib/api";
import {
  chatThreadHref,
  groupThreadsByWorkspace,
  threadApiPath,
  threadWorkspaceKey,
  threadWorkspaceLabel,
} from "@/lib/chatThreads";
import type { ThreadListPayload, ThreadSummary, ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { CUSTOM_DOM_EVENTS, SESSION_EVENTS } from "@/lib/eventConstants";
import { API_PATHS, SSE_URLS } from "@/lib/endpoints";
import {
  HISTORY_MENU_MIN_SPACE_PX,
  HISTORY_PAGE_SIZE,
  SSE_RECONNECT_DELAY_MS,
  STORAGE_KEYS,
} from "@/lib/uiConstants";
import { useMobileDrawer } from "@/lib/useMobileDrawer";
import { ALL_WORKSPACES_KEY } from "@/lib/workspaceConstants";
import { truncateText } from "@/lib/utils";
import { useToast } from "@/components/Toast";
import type { ActiveSession, WorkspaceRef } from "@/types";

type NavItem = {
  href: string;
  label: string;
  icon: JSX.Element;
};

const navItems: NavItem[] = [
  { href: "/", label: "Home", icon: <Home size={15} /> },
  { href: "/chat", label: "Chat", icon: <MessageSquare size={15} /> },
  { href: "/repositories", label: "Repositories", icon: <GitPullRequest size={15} /> },
  { href: "/tasks", label: "Background Tasks", icon: <PlaySquare size={15} /> },
  { href: "/scheduler", label: "Scheduler", icon: <CalendarClock size={15} /> },
];

const HISTORY_PANEL_STORAGE_KEY = STORAGE_KEYS.HISTORY_PANEL;

type HistoryCache = {
  threads: ThreadSummary[];
  hasMore: boolean;
  nextOffset: number;
  loaded: boolean;
};

type HistoryLoadResult = {
  payload: ThreadListPayload;
  offset: number;
  reset: boolean;
};

const historyCache: HistoryCache = {
  threads: [],
  hasMore: true,
  nextOffset: 0,
  loaded: false,
};

let historyLoadPromise: Promise<HistoryLoadResult> | null = null;
let historyLoadKey = "";
const optimisticLocks = new Map<string, { state: "running" | "stopped"; timestamp: number }>();

export function AppShell(props: {
  title: string;
  subtitle?: JSX.Element;
  actions?: JSX.Element;
  children: JSX.Element;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [collapsed, setCollapsed] = createSignal(false);
  const [historyOpen, setHistoryOpen] = createSignal(false);
  const [historyThreads, setHistoryThreads] = createSignal<ThreadSummary[]>(historyCache.threads);
  const [historyError, setHistoryError] = createSignal("");
  const [historyHasMore, setHistoryHasMore] = createSignal(historyCache.hasMore);
  const [historyLoading, setHistoryLoading] = createSignal(false);
  const [historyMenuThreadId, setHistoryMenuThreadId] = createSignal<string | null>(null);
  const [historyMenuOpensUp, setHistoryMenuOpensUp] = createSignal(false);
  const [historyNextOffset, setHistoryNextOffset] = createSignal(historyCache.nextOffset);
  const [historyLoaded, setHistoryLoaded] = createSignal(historyCache.loaded);
  const [editingHistoryThreadId, setEditingHistoryThreadId] = createSignal<string | null>(null);
  const [editingHistoryTitle, setEditingHistoryTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [activeSessions, setActiveSessions] = createSignal<ActiveSession[]>([]);
  const {
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer,
    handleAppLayoutClick,
    handleKeydown,
  } = useMobileDrawer({ sidebarId: "app-shell-sidebar" });
  let disposed = false;

  let sessionEventSource: EventSource | null = null;

  const runningThreadIds = createMemo(() => {
    const activeIds = new Set<string>();
    for (const s of activeSessions()) {
      activeIds.add(s.thread_id);
    }
    
    const now = Date.now();
    for (const [tid, lock] of optimisticLocks.entries()) {
      if (now - lock.timestamp > 4000) {
        optimisticLocks.delete(tid);
        continue;
      }
      if (lock.state === "running") {
        activeIds.add(tid);
      } else if (lock.state === "stopped") {
        activeIds.delete(tid);
      }
    }
    return activeIds;
  });

  const connectSessionEvents = () => {
    if (disposed) return;
    if (sessionEventSource) {
      sessionEventSource.close();
    }
    
    const source = new EventSource(SSE_URLS.sessions);
    sessionEventSource = source;
    
    source.addEventListener(SESSION_EVENTS.SNAPSHOT, (event) => {
      try {
        const payload = JSON.parse(event.data) as { sessions: ActiveSession[] };
        if (payload && Array.isArray(payload.sessions)) {
          setActiveSessions(payload.sessions);
        }
      } catch (err) {
        console.error("Failed to parse sessions snapshot", err);
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_STARTED, (event) => {
      try {
        const session = JSON.parse(event.data) as ActiveSession;
        setActiveSessions((prev) => {
          const exists = prev.some((s) => s.session_id === session.session_id);
          if (exists) {
            return prev.map((s) => s.session_id === session.session_id ? session : s);
          }
          return [...prev, session];
        });
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.SESSION_STARTED, { detail: session }));
        window.dispatchEvent(
          new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
            detail: {
              threadId: session.thread_id,
              title: session.current_step,
              agent_name: session.agent_name,
            },
          }),
        );
      } catch (err) {
        console.error("Failed to parse session_started event", err);
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_STOPPED, (event) => {
      try {
        const stoppedData = JSON.parse(event.data) as { session_id: string; thread_id: string };
        setActiveSessions((prev) => prev.filter((s) => s.session_id !== stoppedData.session_id));
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.SESSION_STOPPED, { detail: stoppedData }));
        window.dispatchEvent(
          new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, {
            detail: { threadId: stoppedData.thread_id },
          }),
        );
      } catch (err) {
        console.error("Failed to parse session_stopped event", err);
      }
    });

    source.addEventListener(SESSION_EVENTS.SESSION_UPDATED, (event) => {
      try {
        const session = JSON.parse(event.data) as ActiveSession;
        setActiveSessions((prev) => prev.map((s) => s.session_id === session.session_id ? session : s));
        window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.SESSION_UPDATED, { detail: session }));
        window.dispatchEvent(
          new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, {
            detail: {
              threadId: session.thread_id,
              title: session.current_step,
              agent_name: session.agent_name,
            },
          }),
        );
      } catch (err) {
        console.error("Failed to parse session_updated event", err);
      }
    });

    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED && !disposed) {
        setTimeout(connectSessionEvents, SSE_RECONNECT_DELAY_MS);
      }
    };
  };

  const handleThreadStartRunning = (event: Event) => {
    const customEvent = event as CustomEvent<{
      threadId: string;
      title?: string;
      workspace?: WorkspaceRef | null;
      agent_name?: string;
    }>;
    const { threadId, title, workspace, agent_name } = customEvent.detail;
    
    optimisticLocks.set(threadId, { state: "running", timestamp: Date.now() });
    
    setActiveSessions((prev) => [...prev]);

    const exists = historyThreads().some((t) => t.thread_id === threadId);
    if (exists) {
      const updatedAt = new Date().toISOString();
      applyHistoryState({
        threads: historyThreads().map((thread) => (
          thread.thread_id === threadId
            ? {
                ...thread,
                agent_name: agent_name || thread.agent_name,
                workspace: workspace !== undefined ? workspace : thread.workspace,
                updated_at: updatedAt,
              }
            : thread
        )),
      });
    } else {
      const placeholderThread: ThreadSummary = {
        thread_id: threadId,
        title: title || threadId,
        latest_checkpoint_id: "",
        channel_id: "",
        checkpoint_count: 1,
        platform: "dashboard",
        user_id: "dashboard",
        agent_name: agent_name || "default",
        workspace: workspace || null,
        kind: "interactive",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      applyHistoryState({
        threads: [placeholderThread, ...historyThreads()],
      });
    }
  };

  const handleThreadStopRunning = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string }>;
    const { threadId } = customEvent.detail;
    
    optimisticLocks.set(threadId, { state: "stopped", timestamp: Date.now() });
    
    setActiveSessions((prev) => [...prev]);
  };

  const applyHistoryState = (next: Partial<HistoryCache>) => {
    if (next.threads !== undefined) {
      historyCache.threads = next.threads;
    }
    if (next.hasMore !== undefined) {
      historyCache.hasMore = next.hasMore;
    }
    if (next.nextOffset !== undefined) {
      historyCache.nextOffset = next.nextOffset;
    }
    if (next.loaded !== undefined) {
      historyCache.loaded = next.loaded;
    }

    if (disposed) {
      return;
    }

    if (next.threads !== undefined) {
      setHistoryThreads(next.threads);
    }
    if (next.hasMore !== undefined) {
      setHistoryHasMore(next.hasMore);
    }
    if (next.nextOffset !== undefined) {
      setHistoryNextOffset(next.nextOffset);
    }
    if (next.loaded !== undefined) {
      setHistoryLoaded(next.loaded);
    }
  };

  const selectedChatThreadId = () => {
    const match = location.pathname.match(/^\/c\/(.+)$/);
    if (!match) {
      return "";
    }
    try {
      return decodeURIComponent(match[1]);
    } catch {
      return match[1];
    }
  };

  const isActive = (href: string) => {
    if (href === "/") {
      return location.pathname === "/";
    }
    if (href === "/chat") {
      return location.pathname === "/chat";
    }
    return location.pathname.startsWith(href);
  };

  const handleClickOutside = (event: MouseEvent) => {
    const target = event.target as HTMLElement;
    if (!target.closest(".user-menu-wrapper")) {
      setMenuOpen(false);
    }
    if (!target.closest(".nav-history-item-wrapper")) {
      setHistoryMenuThreadId(null);
    }
  };

  const handleThreadsChanged = () => {
    if (editingHistoryThreadId()) {
      return;
    }

    if ((historyOpen() && !collapsed()) || isHistoryActive()) {
      void loadHistory(true);
      return;
    }

    applyHistoryState({ loaded: false });
  };

  const setSidebarCollapsed = (next: boolean) => {
    setCollapsed(next);
    window.localStorage.setItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, next ? "collapsed" : "expanded");

    if (!next && historyOpen() && !historyLoaded()) {
      void loadHistory(true);
    }
  };

  const toggleSidebar = () => {
    setSidebarCollapsed(!collapsed());
  };

  const loadHistory = async (reset = false) => {
    if (disposed || historyLoading()) {
      return;
    }

    const offset = reset ? 0 : historyNextOffset();
    const requestKey = `${reset ? "reset" : "append"}:${offset}`;
    setHistoryError("");
    setHistoryLoading(true);
    try {
      let result: HistoryLoadResult;
      if (historyLoadPromise && historyLoadKey === requestKey) {
        result = await historyLoadPromise;
      } else {
        const params = new URLSearchParams({
          limit: String(HISTORY_PAGE_SIZE),
          offset: String(offset),
        });
        let sharedRequest: Promise<HistoryLoadResult>;
        sharedRequest = apiFetch<ThreadListPayload>(
          `${API_PATHS.chatHistory}?${params.toString()}`,
        )
          .then((payload) => ({ payload, offset, reset }))
          .finally(() => {
            if (historyLoadPromise === sharedRequest) {
              historyLoadPromise = null;
              historyLoadKey = "";
            }
          });
        historyLoadPromise = sharedRequest;
        historyLoadKey = requestKey;
        result = await sharedRequest;
      }

      const nextThreads = result.reset
        ? result.payload.threads
        : [...historyCache.threads, ...result.payload.threads];
      applyHistoryState({
        threads: nextThreads,
        hasMore: Boolean(result.payload.has_more),
        nextOffset: result.payload.next_offset ?? result.offset + result.payload.threads.length,
        loaded: true,
      });
    } catch (err) {
      if (!disposed) {
        setHistoryError(err instanceof Error ? err.message : "Failed to load chat history");
      }
    } finally {
      if (!disposed) {
        setHistoryLoading(false);
      }
    }
  };

  const isHistoryActive = () => (
    location.pathname.startsWith("/history") || Boolean(selectedChatThreadId())
  );
  const isThreadActive = (threadId: string) => selectedChatThreadId() === threadId;
  const historyGroups = createMemo(() => groupThreadsByWorkspace(historyThreads()));

  const [selectedWorkspaceFilter, setSelectedWorkspaceFilter] = createSignal<string>(
    window.localStorage.getItem(STORAGE_KEYS.WORKSPACE_FILTER) || ALL_WORKSPACES_KEY,
  );

  const runningThreads = createMemo(() => {
    return historyThreads().filter((t) => runningThreadIds().has(t.thread_id));
  });

  const filteredThreads = createMemo(() => {
    const filter = selectedWorkspaceFilter();
    const nonRunning = historyThreads().filter((t) => !runningThreadIds().has(t.thread_id));
    if (filter === ALL_WORKSPACES_KEY) {
      return nonRunning;
    }
    return nonRunning.filter((t) => threadWorkspaceKey(t) === filter);
  });

  const availableWorkspaces = createMemo(() => {
    return historyGroups().map((group) => {
      const isRepo = group.threads.some((t) => t.workspace?.metadata?.repository_full_name);
      return {
        key: group.key,
        label: group.label,
        isRepo,
      };
    });
  });

  const workspaceRunningCounts = createMemo(() => {
    const counts = new Map<string, number>();
    for (const thread of historyThreads()) {
      if (runningThreadIds().has(thread.thread_id)) {
        const key = threadWorkspaceKey(thread);
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    }
    return counts;
  });

  const historyWorkspaceFilterOptions = createMemo(() => [
    { value: ALL_WORKSPACES_KEY, label: "🗂 All Workspaces" },
    ...availableWorkspaces().map((ws) => {
      const runningCount = workspaceRunningCounts().get(ws.key) || 0;
      const icon = ws.isRepo ? "⎇" : "🗀";
      const prefix = runningCount > 0 ? "↻ " : "";
      const suffix = runningCount > 0 ? ` (${runningCount} running)` : "";
      return {
        value: ws.key,
        label: `${prefix}${icon} ${ws.label}${suffix}`,
      };
    }),
  ]);

  const updateSelectedWorkspaceFilter = (value: string) => {
    setSelectedWorkspaceFilter(value);
    window.localStorage.setItem(STORAGE_KEYS.WORKSPACE_FILTER, value);
  };

  const setHistoryPanelOpen = (next: boolean) => {
    setHistoryOpen(next);
    window.localStorage.setItem(HISTORY_PANEL_STORAGE_KEY, next ? "open" : "closed");

    if (next && !collapsed() && !historyLoaded()) {
      void loadHistory(true);
    }
  };

  const toggleHistory = () => {
    if (collapsed()) {
      setSidebarCollapsed(false);
    }

    setHistoryPanelOpen(!historyOpen());
  };
  const renderHistoryItem = (thread: ThreadSummary) => {
    return (
      <div class="nav-history-item-wrapper">
        <div
          class={`nav-history-item ${isThreadActive(thread.thread_id) ? "active" : ""} ${editingHistoryThreadId() === thread.thread_id ? "editing" : ""} ${runningThreadIds().has(thread.thread_id) ? "running" : ""}`}
        >
          <Show
            when={editingHistoryThreadId() === thread.thread_id}
            fallback={
              <A
                href={chatThreadHref(thread.thread_id)}
                activeClass=""
                inactiveClass=""
                class="nav-history-link"
                title={`${thread.thread_id} - ${threadMeta(thread)}`}
              >
                <Show
                  when={runningThreadIds().has(thread.thread_id)}
                  fallback={
                    <Show
                      when={isBackgroundThread(thread)}
                      fallback={<MessageSquare size={12} class="nav-history-kind-icon" />}
                    >
                      <PlaySquare size={12} class="nav-history-kind-icon task" />
                    </Show>
                  }
                >
                  <RefreshCw size={12} class="nav-history-kind-icon spinner-animate" />
                </Show>
                <span class="nav-history-title">{threadTitle(thread)}</span>
              </A>
            }
          >
            <InlineRenameInput
              class="nav-history-rename-input"
              value={editingHistoryTitle()}
              onInput={setEditingHistoryTitle}
              onBlur={() => void finishRenameHistoryThread(thread)}
              onCancel={cancelRenameHistoryThread}
            />
          </Show>
          <Show when={editingHistoryThreadId() !== thread.thread_id}>
            <button
              class={`nav-history-action ${historyMenuThreadId() === thread.thread_id ? "active" : ""}`}
              type="button"
              title="Thread actions"
              aria-label="Thread actions"
              aria-expanded={historyMenuThreadId() === thread.thread_id}
              onClick={(event) => toggleHistoryMenu(thread.thread_id, event)}
            >
              <MoreHorizontal size={14} />
            </button>
          </Show>
        </div>
        <Show when={historyMenuThreadId() === thread.thread_id}>
          <div class={`nav-history-menu ${historyMenuOpensUp() ? "open-up" : ""}`}>
            <Show when={runningThreadIds().has(thread.thread_id)}>
              <button
                class="nav-history-menu-item nav-history-menu-danger"
                type="button"
                onClick={(event) => stopThreadExecution(thread, event)}
              >
                <Square size={13} fill="currentColor" />
                <span>Stop Execution</span>
              </button>
            </Show>
            <button
              class="nav-history-menu-item"
              type="button"
              onClick={(event) => startRenameHistoryThread(thread, event)}
            >
              <Pencil size={13} />
              <span>Rename</span>
            </button>
            <button
              class="nav-history-menu-item nav-history-menu-danger"
              type="button"
              onClick={(event) => requestDeleteHistoryThread(thread, event)}
            >
              <Trash2 size={13} />
              <span>Delete</span>
            </button>
          </div>
        </Show>
      </div>
    );
  };

  const threadTitle = (thread: ThreadSummary) => truncateText(thread.title || thread.thread_id, 30);
  const isBackgroundThread = (thread: ThreadSummary) => thread.kind === "background";
  const threadMeta = (thread: ThreadSummary) => {
    const owner = thread.channel_id || thread.user_id;
    return [
      isBackgroundThread(thread) ? "background" : "",
      thread.platform,
      thread.agent_name,
      owner ? truncateText(owner, 16) : "",
      `${thread.checkpoint_count} steps`,
    ].filter(Boolean).join(" / ");
  };
  const handleHistoryScroll = (event: Event) => {
    const target = event.currentTarget as HTMLElement;
    const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;

    setHistoryMenuThreadId(null);

    if (distanceToBottom <= 48 && historyHasMore() && !historyLoading()) {
      void loadHistory();
    }
  };
  const toggleHistoryMenu = (threadId: string, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setEditingHistoryThreadId(null);
    setHistoryMenuThreadId((current) => {
      if (current === threadId) {
        setHistoryMenuOpensUp(false);
        return null;
      }

      const target = event.currentTarget as HTMLElement;
      const list = target.closest(".nav-history-list") as HTMLElement | null;
      const targetRect = target.getBoundingClientRect();
      const boundaryBottom = list?.getBoundingClientRect().bottom ?? window.innerHeight;
      setHistoryMenuOpensUp(boundaryBottom - targetRect.bottom < HISTORY_MENU_MIN_SPACE_PX);
      return threadId;
    });
  };
  const requestDeleteHistoryThread = (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setHistoryMenuThreadId(null);
    setDeleteTarget(thread);
  };

  const cancelDelete = () => {
    setDeleteTarget(null);
  };

  const stopThreadExecution = async (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setHistoryMenuThreadId(null);
    
    const threadId = thread.thread_id;
    optimisticLocks.set(threadId, { state: "stopped", timestamp: Date.now() });
    setActiveSessions((prev) => [...prev]);
    
    // Abort locally if viewing
    window.dispatchEvent(
      new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_EXTERNAL_ABORT, {
        detail: { threadId },
      }),
    );
    
    try {
      await postJson(API_PATHS.sessionsStop, { thread_id: threadId });
      showToast("Execution stop request sent.", "success");
    } catch (err) {
      optimisticLocks.delete(threadId);
      setActiveSessions((prev) => [...prev]);
      showToast(err instanceof Error ? err.message : "Failed to stop execution", "error");
    }
  };

  const confirmDeleteHistoryThread = async () => {
    const thread = deleteTarget();
    if (!thread) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJson(threadApiPath(thread.thread_id));
      applyHistoryState({
        threads: historyThreads().filter((item) => item.thread_id !== thread.thread_id),
        nextOffset: Math.max(0, historyNextOffset() - 1),
      });
      showToast("Thread deleted.", "success");

      if (isThreadActive(thread.thread_id)) {
        navigate(location.pathname.startsWith("/history") ? "/history" : "/chat");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };
  const startRenameHistoryThread = (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();

    setHistoryMenuThreadId(null);
    setEditingHistoryThreadId(thread.thread_id);
    setEditingHistoryTitle(thread.title || thread.thread_id);
  };
  const cancelRenameHistoryThread = () => {
    setEditingHistoryThreadId(null);
    setEditingHistoryTitle("");
  };
  const finishRenameHistoryThread = async (thread: ThreadSummary) => {
    if (editingHistoryThreadId() !== thread.thread_id) {
      return;
    }

    const trimmedTitle = editingHistoryTitle().trim();
    if (!trimmedTitle) {
      showToast("Thread name cannot be empty.", "warning");
      cancelRenameHistoryThread();
      return;
    }

    if (trimmedTitle === (thread.title || thread.thread_id).trim()) {
      cancelRenameHistoryThread();
      return;
    }

    try {
      const updated = await patchJson<ThreadSummary>(
        threadApiPath(thread.thread_id),
        { title: trimmedTitle },
      );
      applyHistoryState({
        threads: historyThreads().map((item) =>
          item.thread_id === updated.thread_id ? { ...item, ...updated } : item,
        ),
      });
      cancelRenameHistoryThread();
      showToast("Thread renamed.", "success");
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Rename failed", "error");
    }
  };

  onMount(() => {
    if (window.localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED) === "collapsed") {
      setCollapsed(true);
    }
    const savedHistoryState = window.localStorage.getItem(HISTORY_PANEL_STORAGE_KEY);
    if (savedHistoryState === "open" || isHistoryActive()) {
      setHistoryPanelOpen(true);
    }
    if (!collapsed() && !historyLoaded()) {
      void loadHistory(true);
    }
    document.addEventListener("click", handleClickOutside);
    document.addEventListener("keydown", handleKeydown);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleThreadsChanged);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, handleThreadStartRunning);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunning);

    connectSessionEvents();
  });

  onCleanup(() => {
    disposed = true;
    if (sessionEventSource) {
      sessionEventSource.close();
      sessionEventSource = null;
    }
    document.removeEventListener("click", handleClickOutside);
    document.removeEventListener("keydown", handleKeydown);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleThreadsChanged);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, handleThreadStartRunning);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunning);
  });

  return (
    <div
      class={`app-layout ${collapsed() ? "sidebar-collapsed" : ""} ${isMobileViewport() && mobileDrawerOpen() ? "app-layout--drawer-open" : ""}`}
      onClick={handleAppLayoutClick}
    >
      <aside id="app-shell-sidebar" class="sidebar">
        <div class="brand">
          <Show
            when={!collapsed()}
            fallback={
              <button
                class="brand-mark brand-expand-btn"
                type="button"
                onClick={toggleSidebar}
                title="Expand sidebar"
              >
                <span class="brand-expand-icon"><ChevronsRight size={14} /></span>
                <span class="brand-expand-default"><Bot size={16} /></span>
              </button>
            }
          >
            <div class="brand-mark">
              <Bot size={16} />
            </div>
            <div class="brand-text">
              <div class="brand-title">Kaka Console</div>
              {/* <div class="brand-subtitle">Agent control plane</div> */}
            </div>
            <button
              class="brand-collapse-btn"
              type="button"
              onClick={toggleSidebar}
              title="Collapse sidebar"
            >
              <ChevronsLeft size={14} />
            </button>
          </Show>
        </div>
        <nav class="nav">
          <For each={navItems}>
            {(item) => (
              <A
                href={item.href}
                activeClass=""
                inactiveClass=""
                class={`nav-link ${isActive(item.href) ? "active" : ""}`}
                title={item.label}
              >
                {item.icon}
                <span class="nav-label">{item.label}</span>
              </A>
            )}
          </For>
          <Show
            when={!collapsed()}
            fallback={
              <A
                href="/history"
                activeClass=""
                inactiveClass=""
                class={`nav-link ${location.pathname.startsWith("/history") ? "active" : ""}`}
                title="History"
              >
                <History size={15} />
              </A>
            }
          >
            <div class="nav-history-group open">
              <div class="nav-history-filter-container">
                <SelectControl
                  class="nav-history-filter"
                  value={selectedWorkspaceFilter()}
                  options={historyWorkspaceFilterOptions()}
                  onChange={updateSelectedWorkspaceFilter}
                  ariaLabel="Filter history by workspace"
                />
                <A
                  href="/history"
                  class="nav-history-all-btn"
                  title="View all history"
                  aria-label="View all history"
                >
                  <History size={14} />
                </A>
              </div>

              <div
                class="nav-history-list"
                aria-label="Chat history"
                onScroll={handleHistoryScroll}
              >
                {/* Global Pinned Running Tasks */}
                <Show when={runningThreads().length > 0}>
                  <div class="nav-history-running-section">
                    <div class="nav-history-running-title">⚡ Running Tasks</div>
                    <For each={runningThreads()}>
                      {(thread) => (
                        <div class="nav-history-item-wrapper">
                          <div
                            class={`nav-history-item ${isThreadActive(thread.thread_id) ? "active" : ""} running`}
                          >
                            <A
                              href={chatThreadHref(thread.thread_id)}
                              activeClass=""
                              inactiveClass=""
                              class="nav-history-link"
                              title={`${thread.thread_id} - ${threadMeta(thread)}`}
                            >
                              <RefreshCw size={12} class="nav-history-kind-icon spinner-animate" />
                              <span class="nav-history-title">
                                {threadTitle(thread)}
                              </span>
                            </A>
                            <button
                              class={`nav-history-action ${historyMenuThreadId() === thread.thread_id ? "active" : ""}`}
                              type="button"
                              title="Thread actions"
                              aria-label="Thread actions"
                              aria-expanded={historyMenuThreadId() === thread.thread_id}
                              onClick={(event) => toggleHistoryMenu(thread.thread_id, event)}
                            >
                              <MoreHorizontal size={14} />
                            </button>
                          </div>
                          <Show when={historyMenuThreadId() === thread.thread_id}>
                            <div class={`nav-history-menu ${historyMenuOpensUp() ? "open-up" : ""}`}>
                              <button
                                class="nav-history-menu-item nav-history-menu-danger"
                                type="button"
                                onClick={(event) => stopThreadExecution(thread, event)}
                              >
                                <Square size={13} fill="currentColor" />
                                <span>Stop Execution</span>
                              </button>
                              <button
                                class="nav-history-menu-item"
                                type="button"
                                onClick={(event) => startRenameHistoryThread(thread, event)}
                              >
                                <Pencil size={13} />
                                <span>Rename</span>
                              </button>
                              <button
                                class="nav-history-menu-item nav-history-menu-danger"
                                type="button"
                                onClick={(event) => requestDeleteHistoryThread(thread, event)}
                              >
                                <Trash2 size={13} />
                                <span>Delete</span>
                              </button>
                            </div>
                          </Show>
                        </div>
                      )}
                    </For>
                  </div>
                </Show>

                {/* Filtered Flat History List */}
                <div class="nav-history-flat-threads">
                  <For each={filteredThreads()}>
                    {(thread) => renderHistoryItem(thread)}
                  </For>
                </div>

                <Show when={historyThreads().length === 0 && historyLoading()}>
                  <div class="nav-history-status">Loading...</div>
                </Show>
                <Show when={historyThreads().length === 0 && !historyLoading() && !historyError()}>
                  <div class="nav-history-status">No history</div>
                </Show>
                <Show when={historyThreads().length > 0 && historyLoading()}>
                  <div class="nav-history-status">Loading more...</div>
                </Show>
                <Show when={historyError()}>
                  <button
                    class="nav-history-retry"
                    type="button"
                    onClick={() => void loadHistory(historyThreads().length === 0)}
                  >
                    Retry
                  </button>
                </Show>
                <Show
                  when={
                    historyThreads().length > 0
                    && historyHasMore()
                    && !historyLoading()
                    && !historyError()
                  }
                >
                  <button class="nav-history-retry" type="button" onClick={() => void loadHistory()}>
                    Load more
                  </button>
                </Show>
              </div>
            </div>
          </Show>
        </nav>
        <div class="sidebar-footer">
          <div class="sidebar-footer-row">
            <div class="user-menu-wrapper">
              <button
                class="user-avatar"
                type="button"
                onClick={() => setMenuOpen(!menuOpen())}
                title="Account"
              >
                A
              </button>
              <Show when={menuOpen()}>
                <div class="user-menu">
                  <a class="user-menu-item user-menu-danger" href="/logout">
                    <LogOut size={14} />
                    <span>Logout</span>
                  </a>
                </div>
              </Show>
            </div>
            <A href="/settings" class="btn btn-icon" title="Settings">
              <Settings size={15} />
            </A>
          </div>
        </div>
      </aside>
      <main class="main">
        <header class="topbar">
          <Show when={isMobileViewport()}>
            <button
              class="topbar-menu-toggle"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMobileDrawerOpen(true);
              }}
              aria-label="Open navigation"
              aria-expanded={mobileDrawerOpen()}
              aria-controls="app-shell-sidebar"
            >
              <Menu size={18} />
            </button>
          </Show>
          <div>
            <h1 class="page-title">{props.title}</h1>
            {props.subtitle ? <p class="page-subtitle">{props.subtitle}</p> : null}
          </div>
          <div class="row-wrap">{props.actions}</div>
        </header>
        <Show when={isMobileViewport() && props.subtitle}>
          <div class="topbar-meta">{props.subtitle}</div>
        </Show>
        <div class="content">{props.children}</div>
      </main>

      <DeleteThreadDialog
        open={deleteTarget() !== null}
        thread={deleteTarget()}
        deleting={deleting()}
        onClose={cancelDelete}
        onConfirm={() => void confirmDeleteHistoryThread()}
      />
    </div>
  );
}
