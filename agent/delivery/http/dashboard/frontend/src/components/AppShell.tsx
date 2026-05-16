import { A, useLocation, useNavigate } from "@solidjs/router";
import {
  Activity,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  GitPullRequest,
  History,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  PanelsTopLeft,
  PlaySquare,
  Settings,
  Trash2,
} from "lucide-solid";
import { createSignal, For, JSX, onCleanup, onMount, Show } from "solid-js";

import { apiFetch, deleteJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import { useToast } from "@/components/Toast";

type NavItem = {
  href: string;
  label: string;
  icon: JSX.Element;
};

const navItems: NavItem[] = [
  { href: "/", label: "Overview", icon: <PanelsTopLeft size={15} /> },
  { href: "/chat", label: "Chat", icon: <MessageSquare size={15} /> },
  { href: "/sessions", label: "Active Sessions", icon: <Activity size={15} /> },
  { href: "/repositories", label: "Repositories", icon: <GitPullRequest size={15} /> },
  { href: "/tasks", label: "Background Tasks", icon: <PlaySquare size={15} /> },
  { href: "/scheduler", label: "Scheduler", icon: <CalendarClock size={15} /> },
];

const HISTORY_PAGE_SIZE = 20;

type ThreadSummary = {
  thread_id: string;
  latest_checkpoint_id: string;
  checkpoint_count: number;
  platform: string;
  user_id: string;
  channel_id: string;
};

type ThreadListPayload = {
  threads: ThreadSummary[];
  has_more?: boolean;
  next_offset?: number;
};

export function AppShell(props: {
  title: string;
  subtitle?: string;
  actions?: JSX.Element;
  children: JSX.Element;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [collapsed, setCollapsed] = createSignal(false);
  const [historyOpen, setHistoryOpen] = createSignal(false);
  const [historyThreads, setHistoryThreads] = createSignal<ThreadSummary[]>([]);
  const [historyError, setHistoryError] = createSignal("");
  const [historyHasMore, setHistoryHasMore] = createSignal(true);
  const [historyLoading, setHistoryLoading] = createSignal(false);
  const [historyMenuThreadId, setHistoryMenuThreadId] = createSignal<string | null>(null);
  const [historyNextOffset, setHistoryNextOffset] = createSignal(0);
  const [historyLoaded, setHistoryLoaded] = createSignal(false);

  const isActive = (href: string) => {
    if (href === "/") {
      return location.pathname === "/";
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

  const setSidebarCollapsed = (next: boolean) => {
    setCollapsed(next);
    window.localStorage.setItem("kaka-dashboard-sidebar", next ? "collapsed" : "expanded");
  };

  const toggleSidebar = () => {
    setSidebarCollapsed(!collapsed());
  };

  const loadHistory = async (reset = false) => {
    if (historyLoading()) {
      return;
    }

    const offset = reset ? 0 : historyNextOffset();
    setHistoryError("");
    setHistoryLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(HISTORY_PAGE_SIZE),
        offset: String(offset),
      });
      const payload = await apiFetch<ThreadListPayload>(
        `/dashboard-api/chat-history?${params.toString()}`,
      );
      setHistoryThreads((current) => (
        reset ? payload.threads : [...current, ...payload.threads]
      ));
      setHistoryHasMore(Boolean(payload.has_more));
      setHistoryNextOffset(payload.next_offset ?? offset + payload.threads.length);
      setHistoryLoaded(true);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Failed to load chat history");
    } finally {
      setHistoryLoading(false);
    }
  };

  const toggleHistory = () => {
    if (collapsed()) {
      setSidebarCollapsed(false);
    }

    const next = !historyOpen();
    setHistoryOpen(next);

    if (next && !historyLoaded()) {
      void loadHistory(true);
    }
  };

  const historyHref = (threadId: string) => `/history/${encodeURIComponent(threadId)}`;
  const isHistoryActive = () => location.pathname.startsWith("/history");
  const isThreadActive = (threadId: string) => location.pathname === historyHref(threadId);
  const threadTitle = (thread: ThreadSummary) => truncateText(thread.thread_id, 30);
  const threadMeta = (thread: ThreadSummary) => {
    const owner = thread.channel_id || thread.user_id;
    return [
      thread.platform,
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
    setHistoryMenuThreadId((current) => (current === threadId ? null : threadId));
  };
  const deleteHistoryThread = async (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();

    if (!confirm(`Delete thread "${thread.thread_id}"?`)) {
      return;
    }

    setHistoryMenuThreadId(null);
    try {
      await deleteJson(`/dashboard-api/chat-history/${encodeURIComponent(thread.thread_id)}`);
      setHistoryThreads((current) => (
        current.filter((item) => item.thread_id !== thread.thread_id)
      ));
      setHistoryNextOffset((current) => Math.max(0, current - 1));
      showToast("Thread deleted.", "success");

      if (isThreadActive(thread.thread_id)) {
        navigate("/history");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    }
  };

  onMount(() => {
    if (window.localStorage.getItem("kaka-dashboard-sidebar") === "collapsed") {
      setCollapsed(true);
    }
    document.addEventListener("click", handleClickOutside);
  });

  onCleanup(() => {
    document.removeEventListener("click", handleClickOutside);
  });

  return (
    <div class={`app-layout ${collapsed() ? "sidebar-collapsed" : ""}`}>
      <aside class="sidebar">
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
              <div class="brand-title">Kaka Dashboard</div>
              <div class="brand-subtitle">Agent control plane</div>
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
                class={`nav-link ${isActive(item.href) ? "active" : ""}`}
                title={item.label}
              >
                {item.icon}
                <span class="nav-label">{item.label}</span>
              </A>
            )}
          </For>
          <div class={`nav-history-group ${historyOpen() && !collapsed() ? "open" : ""}`}>
            <button
              class={`nav-link nav-history-toggle ${isHistoryActive() ? "active" : ""}`}
              type="button"
              onClick={toggleHistory}
              aria-expanded={historyOpen()}
              title="History"
            >
              <History size={15} />
              <span class="nav-label">History</span>
              <span class="nav-history-caret">
                <Show when={historyOpen()} fallback={<ChevronRight size={14} />}>
                  <ChevronDown size={14} />
                </Show>
              </span>
            </button>
            <Show when={historyOpen() && !collapsed()}>
              <div
                class="nav-history-list"
                aria-label="Chat history"
                onScroll={handleHistoryScroll}
              >
                <A
                  href="/history"
                  class={`nav-history-link ${location.pathname === "/history" ? "active" : ""}`}
                  title="All history"
                >
                  <span class="nav-history-title">All history</span>
                </A>
                <For each={historyThreads()}>
                  {(thread) => (
                    <div class="nav-history-item-wrapper">
                      <div
                        class={`nav-history-item ${isThreadActive(thread.thread_id) ? "active" : ""}`}
                      >
                        <A
                          href={historyHref(thread.thread_id)}
                          class="nav-history-link"
                          title={`${thread.thread_id} - ${threadMeta(thread)}`}
                        >
                          <span class="nav-history-title">{threadTitle(thread)}</span>
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
                        <div class="nav-history-menu">
                          <button
                            class="nav-history-menu-item nav-history-menu-danger"
                            type="button"
                            onClick={(event) => void deleteHistoryThread(thread, event)}
                          >
                            <Trash2 size={13} />
                            <span>Delete</span>
                          </button>
                        </div>
                      </Show>
                    </div>
                  )}
                </For>
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
            </Show>
          </div>
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
          <div>
            <h1 class="page-title">{props.title}</h1>
            {props.subtitle ? <p class="page-subtitle">{props.subtitle}</p> : null}
          </div>
          <div class="row-wrap">{props.actions}</div>
        </header>
        <div class="content">{props.children}</div>
      </main>
    </div>
  );
}
