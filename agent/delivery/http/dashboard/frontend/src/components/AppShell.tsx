import "@/styles/routes/shell.css";

import { A, useHref, useLocation, useNavigate, useResolvedPath } from "@solidjs/router";
import {
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Download,
  Folder,
  GitBranch,
  GitPullRequest,
  History,
  Home,
  LogOut,
  Menu,
  MessageSquare,
  MoreVertical,
  Pencil,
  Pin,
  PinOff,
  PlaySquare,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-solid";
import { batch, createEffect, createMemo, createSignal, For, JSX, on, onCleanup, onMount, Show } from "solid-js";
import { Portal } from "solid-js/web";
import { unwrap } from "solid-js/store";

import { DeleteThreadDialog } from "@/components/DeleteThreadDialog";
import { ExportChatDialog } from "@/components/ExportChatDialog";
import { useKeyboardShortcuts, useShortcutAction } from "@/components/KeyboardShortcutsProvider";
import { ProjectActions } from "@/components/ProjectActions";
import { InlineRenameInput } from "@/components/InlineRenameInput";
import { UpdateDialog } from "@/components/UpdateDialog";
import { deleteJson, patchJson, postJson } from "@/lib/api";
import { checkForUpdates, openUpdateDialog, versionInfo } from "@/lib/versionStore";
import { newChatNavigationState, type NewChatNavigationState } from "@/lib/chatNavigation";
import {
  chatThreadHref,
  sortWorkspaceGroups,
  threadApiPath,
  threadWorkspaceKey,
} from "@/lib/chatThreads";
import type { ThreadSummary, ThreadWorkspaceGroup } from "@/lib/chatThreads";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { API_PATHS } from "@/lib/endpoints";
import { hasOpenDialog } from "@/lib/keyboardShortcuts";
import { isSchedulerSession } from "@/lib/schedulerSessions";
import { useSessions } from "@/lib/sessionStore";
import { updateDashboardFavicon } from "@/lib/favicon";
import { STORAGE_KEYS } from "@/lib/uiConstants";
import { navProjectsStore, navProjectThreads, newProjectWorkspace } from "@/lib/navProjectsStore";
import { prepareProjectChatWorkspace } from "@/lib/projects";
import { createMediaQuery } from "@/lib/useMediaQuery";
import { useMobileDrawer } from "@/lib/useMobileDrawer";
import { useSidebarResize } from "@/lib/useSidebarResize";
import { useThreadWorkspaceGroups } from "@/lib/useThreadWorkspaceGroups";
import { useVisualViewport } from "@/lib/useVisualViewport";
import { bindWorkspaceRef, isTempWorkspace } from "@/lib/workspace";
import { cloneValue, truncateText } from "@/lib/utils";
import { useToast } from "@/components/Toast";
import type { WorkspaceBinding, WorkspaceRef } from "@/types";

type NavigationSection = "home" | "scheduler" | "repositories";

type NavLink = {
  section: NavigationSection;
  href: string;
  label: string;
  icon: () => JSX.Element;
};

const navItems: (NavLink | { section: "new-chat"; label: string })[] = [
  { section: "home", href: "/", label: "Home", icon: () => <Home size={15} /> },
  { section: "new-chat", label: "New chat" },
  { section: "scheduler", href: "/scheduler", label: "Scheduler", icon: () => <CalendarClock size={15} /> },
  { section: "repositories", href: "/repositories", label: "Repositories", icon: () => <GitPullRequest size={15} /> },
];
const navigationRailId = "app-shell-nav-rail";
const projectsPanelId = "app-shell-projects";
const NAVIGATION_CLOSE_DELAY_MS = 150;

type DeferredHistoryLoad = {
  kind: "idle" | "timeout";
  id: number;
};
type IdleSchedulerWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout?: number }) => number;
  cancelIdleCallback?: (id: number) => void;
};

const optimisticLocks = new Map<string, { state: "running" | "stopped"; timestamp: number }>();

export function AppShell(props: {
  title: string;
  subtitle?: JSX.Element;
  actions?: JSX.Element;
  children: JSX.Element;
  noHeader?: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const shortcuts = useKeyboardShortcuts();
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [collapsed, setCollapsed] = createSignal(window.localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED) === "collapsed");
  const [temporaryNavigationOpen, setTemporaryNavigationOpen] = createSignal(false);
  const [projectMenuId, setProjectMenuId] = createSignal<string | null>(null);
  const {
    threads: historyThreads,
    error: historyError,
    hasMore: historyHasMore,
    loading: historyLoading,
    loaded: historyLoaded,
  } = navProjectsStore;
  const [historyMenuThreadId, setHistoryMenuThreadId] = createSignal<string | null>(null);
  const [historyMenuPos, setHistoryMenuPos] = createSignal({ top: 0, left: 0, opensUp: false });
  let historyMenuAnchor: HTMLElement | null = null;
  let historyMenuRef: HTMLDivElement | undefined;
  const historyMenuThread = createMemo(() =>
    historyThreads().find((item) => item.thread_id === historyMenuThreadId()) ?? null,
  );
  const [editingHistoryThreadId, setEditingHistoryThreadId] = createSignal<string | null>(null);
  const [editingHistoryTitle, setEditingHistoryTitle] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<ThreadSummary | null>(null);
  const [exportTarget, setExportTarget] = createSignal<ThreadSummary | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const { sessions: activeSessions, invalidate: invalidateSessions } = useSessions();
  const {
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer,
    handleAppLayoutClick,
    handleNavClick,
    handleKeydown: handleDrawerKeydown,
  } = useMobileDrawer({ sidebarId: "app-shell-sidebar" });
  const sidebar = useSidebarResize(() => !collapsed() && !isMobileViewport());
  const chatViewport = useVisualViewport(() => Boolean(props.noHeader) && isMobileViewport());
  const hasHoverPointer = createMediaQuery("(any-hover: hover)");
  const activeSection = createMemo<NavigationSection>(() => {
    const segment = location.pathname.split("/", 2)[1];
    return segment === "repositories" || segment === "scheduler" ? segment : "home";
  });
  const projectsVisible = () => isMobileViewport() ? mobileDrawerOpen() : !collapsed() || temporaryNavigationOpen();
  const availableUpdateTitle = () => {
    const info = versionInfo();
    return info?.has_update ? `Update available: v${info.latest_version}` : undefined;
  };
  let sidebarRef: HTMLElement | undefined;
  let navigationRail: HTMLElement | undefined;
  let projectsPanel: HTMLElement | undefined;
  let accountButton: HTMLButtonElement | undefined;
  let navigationTrigger: HTMLElement | undefined;
  let pointerInNavigation = false;
  let restoringNavigationFocus = false;
  let navigationCloseTimer: number | undefined;
  let disposed = false;
  let deferredHistoryLoad: DeferredHistoryLoad | null = null;
  let threadsChangedTimer: number | undefined;


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

  const schedulerRunning = createMemo(() => activeSessions().some(isSchedulerSession));

  createEffect(() => {
    const isRunning = activeSessions().length > 0 || runningThreadIds().size > 0;
    updateDashboardFavicon(isRunning);
  });

  const handleThreadStartRunning = (event: Event) => {
    const customEvent = event as CustomEvent<{
      threadId: string;
      title?: string;
      workspace?: WorkspaceBinding | WorkspaceRef | null;
      agent_name?: string;
    }>;
    const { threadId, title, workspace, agent_name } = customEvent.detail;
    const workspaceBinding = bindWorkspaceRef(workspace);
    const workspaceSummary = workspace !== undefined ? {
      workspace: workspaceBinding,
      workspace_key: !workspaceBinding || isTempWorkspace(workspaceBinding) ? "no-workspace" : workspaceBinding.scope.key,
      workspace_label: !workspaceBinding || isTempWorkspace(workspaceBinding) ? "No workspace" : workspaceBinding.scope.label,
    } : {};
    
    optimisticLocks.set(threadId, { state: "running", timestamp: Date.now() });
    
    invalidateSessions();

    const now = new Date().toISOString();
    const exists = historyThreads().some((t) => t.thread_id === threadId);
    if (exists) {
      const existing = historyThreads().find((t) => t.thread_id === threadId)!;
      const updatedThread: ThreadSummary = {
        ...existing,
        agent_name: agent_name || existing.agent_name,
        updated_at: now,
        ...workspaceSummary,
      };
      applyHistoryState({
        threads: [updatedThread, ...historyThreads().filter((t) => t.thread_id !== threadId)],
      });
    } else {
      if (workspace === undefined) {
        void loadHistory(true);
        return;
      }
      const placeholderThread: ThreadSummary = {
        thread_id: threadId,
        title: title || threadId,
        latest_checkpoint_id: "",
        channel_id: "",
        checkpoint_count: 1,
        platform: "dashboard",
        user_id: "dashboard",
        agent_name: agent_name || "default",
        ...workspaceSummary,
        kind: "interactive",
        created_at: now,
        updated_at: now,
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
    
    invalidateSessions();
  };

  const handleThreadAgentUpdated = (event: Event) => {
    const { threadId, agent_name } = (event as CustomEvent<{ threadId: string; agent_name: string }>).detail;
    if (!threadId || !agent_name) return;
    const threads = historyThreads();
    const existing = threads.find((thread) => thread.thread_id === threadId);
    if (!existing) return;
    const now = new Date().toISOString();
    const updatedThread: ThreadSummary = {
      ...existing,
      agent_name,
      updated_at: now,
    };
    applyHistoryState({
      threads: [updatedThread, ...threads.filter((thread) => thread.thread_id !== threadId)],
    });
  };

  const handleThreadTitleUpdated = (event: Event) => {
    const customEvent = event as CustomEvent<{ threadId: string; title: string }>;
    const { threadId, title } = customEvent.detail;
    if (!threadId || !title) {
      return;
    }

    const threads = historyThreads();
    const existing = threads.find((item) => item.thread_id === threadId);
    if (!existing) {
      return;
    }

    const now = new Date().toISOString();
    const updatedThread: ThreadSummary = {
      ...existing,
      title,
      updated_at: now,
    };
    applyHistoryState({
      threads: [updatedThread, ...threads.filter((item) => item.thread_id !== threadId)],
    });
  };

  const applyHistoryState = (next: { threads?: ThreadSummary[]; loaded?: boolean }) => {
    if (disposed) return;
    if (next.threads !== undefined) navProjectsStore.updateThreads(next.threads);
    if (next.loaded === false) navProjectsStore.invalidate();
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

  createEffect(() => navProjectsStore.setActiveThreadId(selectedChatThreadId()));

  const clearNavigationClose = () => window.clearTimeout(navigationCloseTimer);
  const hasNavigationFocus = () => {
    const focused = document.activeElement;
    return sidebarRef?.contains(focused) && focused?.matches(":focus-visible");
  };

  const openTemporaryNavigation = (trigger?: HTMLElement) => {
    if (!collapsed() || isMobileViewport() || restoringNavigationFocus || hasOpenDialog()) return;
    clearNavigationClose();
    navigationTrigger = trigger ?? navigationTrigger;
    setTemporaryNavigationOpen(true);
  };

  const dismissTemporaryNavigation = (restoreFocus = false) => {
    clearNavigationClose();
    const focused = document.activeElement;
    if (restoreFocus && (
      projectsPanel?.contains(focused) || focused?.matches(".brand-version-btn")
    )) {
      const target = navigationTrigger ?? navigationRail?.querySelector<HTMLElement>('[aria-current="location"]');
      restoringNavigationFocus = true;
      target?.focus({ preventScroll: true });
      restoringNavigationFocus = false;
    }
    navigationTrigger = undefined;
    setTemporaryNavigationOpen(false);
  };

  const scheduleNavigationClose = () => {
    clearNavigationClose();
    if (!temporaryNavigationOpen()) return;
    navigationCloseTimer = window.setTimeout(() => {
      if (pointerInNavigation || hasNavigationFocus() || menuOpen()
        || historyMenuThreadId() || projectMenuId() || editingHistoryThreadId()) return;
      dismissTemporaryNavigation();
    }, NAVIGATION_CLOSE_DELAY_MS);
  };

  const isHoverPointer = (event: PointerEvent) => (
    !isMobileViewport() && hasHoverPointer() && event.pointerType !== "touch" && event.buttons === 0
  );

  const handleNavigationPointerEnter = (event: PointerEvent) => {
    if (!isHoverPointer(event)) return;
    pointerInNavigation = true;
    clearNavigationClose();
  };

  const handleRailFocusIn = (event: FocusEvent) => {
    if (event.target instanceof HTMLElement && event.target.matches(".nav-link:focus-visible")) {
      openTemporaryNavigation(event.target);
    }
  };

  const handleRailKeydown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowRight" || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!(event.target instanceof HTMLElement) || !event.target.matches(".nav-link")) return;
    event.preventDefault();
    openTemporaryNavigation(event.target);
    projectsPanel?.querySelector<HTMLElement>(".nav-projects-manage")?.focus();
  };

  const handleNavigationPointerLeave = (event: PointerEvent) => {
    if (event.pointerType === "touch") return;
    pointerInNavigation = false;
    scheduleNavigationClose();
  };

  const handleNavigationFocusOut = (event: FocusEvent) => {
    if (event.relatedTarget instanceof Node && sidebarRef?.contains(event.relatedTarget)) return;
    scheduleNavigationClose();
  };

  const handleOutsidePointerDown = (event: PointerEvent) => {
    if (!temporaryNavigationOpen() || editingHistoryThreadId() || !(event.target instanceof Element)) return;
    if (sidebarRef?.contains(event.target) || historyMenuRef?.contains(event.target)) return;
    const projectMenu = projectMenuId();
    if (projectMenu && document.getElementById(projectMenu)?.contains(event.target)) return;
    dismissTemporaryNavigation();
  };

  const handleSidebarClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!(event.target instanceof Element) || !event.target.closest("a[href]")) return;
    handleNavClick(event);
    if (temporaryNavigationOpen()) dismissTemporaryNavigation(true);
  };

  const handleNavigationKeydown = (event: KeyboardEvent) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (hasOpenDialog()) return;
    if (projectMenuId()) return;
    if (historyMenuThreadId()) {
      closeHistoryMenu(true);
    } else if (menuOpen()) {
      setMenuOpen(false);
      accountButton?.focus({ preventScroll: true });
    } else if (temporaryNavigationOpen()) {
      dismissTemporaryNavigation(true);
    } else {
      handleDrawerKeydown(event);
      return;
    }
    event.preventDefault();
  };

  const handleClickOutside = (event: MouseEvent) => {
    const target = event.target as HTMLElement;
    if (!target.closest(".user-menu-wrapper")) {
      setMenuOpen(false);
    }
    if (!target.closest(".nav-history-item-wrapper") && !historyMenuRef?.contains(target)) {
      closeHistoryMenu();
    }
  };

  const handleThreadsChanged = () => {
    window.clearTimeout(threadsChangedTimer);
    threadsChangedTimer = window.setTimeout(() => {
      threadsChangedTimer = undefined;
      if (disposed) return;
      if (projectsVisible() || selectedChatThreadId()) {
        void navProjectsStore.load(true, false);
      } else {
        applyHistoryState({ loaded: false });
      }
    }, 250);
  };

  const toggleSidebar = () => {
    if (isMobileViewport()) {
      setMobileDrawerOpen(!mobileDrawerOpen());
      return;
    }
    const next = !collapsed();
    batch(() => {
      dismissTemporaryNavigation(next);
      setCollapsed(next);
    });
    window.localStorage.setItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, next ? "collapsed" : "expanded");
  };
  useShortcutAction("toggle-sidebar", { run: toggleSidebar });

  const renderSidebarPin = () => {
    const label = () => collapsed() ? "Pin navigation panel" : "Unpin navigation panel";
    const updateTitle = () => projectsVisible() ? undefined : availableUpdateTitle();
    return (
      <button
        class="btn btn-icon btn-ghost app-shell-sidebar-pin"
        type="button"
        onClick={toggleSidebar}
        aria-controls={`${navigationRailId} ${projectsPanelId}`}
        aria-expanded={projectsVisible()}
        aria-label={label()}
        aria-description={updateTitle()}
        data-shortcut-action="toggle-sidebar"
        data-shortcut-label={updateTitle() ? `${label()} · ${updateTitle()}` : undefined}
      >
        <Show when={collapsed()} fallback={<PinOff size={16} aria-hidden="true" />}>
          <Pin size={16} aria-hidden="true" />
        </Show>
        <Show when={updateTitle()}>
          <span class="sidebar-pin-update-dot" aria-hidden="true" />
        </Show>
      </button>
    );
  };

  const handleProjectMenuOpenChange = (id: string, open: boolean) => {
    setProjectMenuId((current) => open ? id : current === id ? null : current);
  };

  const loadHistory = async (reset = false) => {
    if (!disposed) await navProjectsStore.load(reset);
  };

  createEffect(on(projectsVisible, (visible) => {
    cancelDeferredHistoryLoad();
    if (visible && !historyLoaded() && !historyLoading()) {
      void loadHistory(true);
    }
  }, { defer: true }));

  createEffect(on(
    [menuOpen, historyMenuThreadId, projectMenuId, editingHistoryThreadId],
    scheduleNavigationClose,
    { defer: true },
  ));
  createEffect(on(() => location.pathname, () => dismissTemporaryNavigation(), { defer: true }));
  createEffect(on(isMobileViewport, () => {
    dismissTemporaryNavigation();
    pointerInNavigation = false;
  }, { defer: true }));

  const cancelDeferredHistoryLoad = () => {
    if (!deferredHistoryLoad) {
      return;
    }

    const idleWindow = window as IdleSchedulerWindow;
    if (deferredHistoryLoad.kind === "idle") {
      idleWindow.cancelIdleCallback?.(deferredHistoryLoad.id);
    } else {
      window.clearTimeout(deferredHistoryLoad.id);
    }
    deferredHistoryLoad = null;
  };

  const scheduleDeferredHistoryLoad = (reset = true) => {
    if (
      disposed
      || historyLoaded()
      || historyLoading()
      || deferredHistoryLoad
      || !projectsVisible()
    ) {
      return;
    }

    const run = () => {
      deferredHistoryLoad = null;
      void loadHistory(reset);
    };
    const idleWindow = window as IdleSchedulerWindow;
    if (idleWindow.requestIdleCallback) {
      deferredHistoryLoad = {
        kind: "idle",
        id: idleWindow.requestIdleCallback(run, { timeout: 1200 }),
      };
      return;
    }

    deferredHistoryLoad = {
      kind: "timeout",
      id: window.setTimeout(run, 120),
    };
  };

  const isThreadActive = (threadId: string) => selectedChatThreadId() === threadId;
  const workspaceGroups = useThreadWorkspaceGroups(historyThreads, navProjectsStore.groups);
  const historyGroups = createMemo(() => {
    const runningIds = runningThreadIds();
    const isRunning = (group: ThreadWorkspaceGroup) => group.threads.some((thread) => runningIds.has(thread.thread_id));
    return sortWorkspaceGroups(workspaceGroups()).sort((left, right) => Number(isRunning(right)) - Number(isRunning(left)));
  });
  const [collapsedProjects, setCollapsedProjects] = createSignal(new Set<string>());
  const toggleProject = (key: string) => {
    setCollapsedProjects((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const [openingProject, setOpeningProject] = createSignal("");
  const openChatDraft = (context: Omit<NewChatNavigationState, "newChatId"> = {}) => {
    if (temporaryNavigationOpen()) dismissTemporaryNavigation(true);
    navigate("/chat", { state: newChatNavigationState(context) });
    closeMobileDrawer();
  };
  useShortcutAction("new-session", {
    run: () => openChatDraft(),
    enabled: () => !openingProject(),
  });

  const startProjectChat = async (group: ThreadWorkspaceGroup) => {
    if (openingProject()) return;
    let workspace = newProjectWorkspace(group);
    let workspaceBinding = group.workspace || undefined;
    setOpeningProject(group.key);
    try {
      if (workspace && group.id) {
        const ready = await prepareProjectChatWorkspace(workspace, group.id);
        workspaceBinding = "execution" in ready ? ready : undefined;
        workspace = "execution" in ready ? ready.execution : ready;
      }
      if (disposed) return;
      openChatDraft({
        projectId: group.id,
        workspaceBinding: workspaceBinding ? cloneValue(unwrap(workspaceBinding)) : undefined,
        workspace: workspace ? cloneValue(unwrap(workspace)) : null,
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Workspace is unavailable.", "error");
    } finally { setOpeningProject(""); }
  };

  createEffect(() => {
    const activeThread = historyThreads().find((thread) => isThreadActive(thread.thread_id));
    if (!activeThread) return;
    const key = threadWorkspaceKey(activeThread);
    setCollapsedProjects((current) => {
      if (!current.has(key)) return current;
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  });

  const renderNavLink = (item: NavLink) => {
    const href = useHref(useResolvedPath(() => item.href));
    const active = () => activeSection() === item.section;
    const live = () => item.section === "scheduler" && schedulerRunning();
    const label = () => live() ? `${item.label} - task running` : item.label;
    return (
      <a
        href={href() ?? item.href}
        link
        class={`nav-link ${active() ? "active" : ""} ${live() ? "nav-link--live" : ""}`}
        aria-current={active() ? "location" : undefined}
        aria-label={label()}
        title={label()}
      >
        {item.icon()}
        <span class="nav-label">{item.label}</span>
        <Show when={live()}>
          <span class="nav-live-indicator">
            <span class="nav-live-dot" aria-hidden="true" />
            <span class="nav-live-text">Running</span>
          </span>
        </Show>
      </a>
    );
  };

  const renderNavigationItems = () => (
    <>
      <For each={navItems}>
        {(item) => "href" in item ? renderNavLink(item) : (
          <button
            class="nav-link nav-new-chat"
            type="button"
            aria-label={item.label}
            data-shortcut-action="new-session"
            disabled={Boolean(openingProject())}
            onClick={() => openChatDraft()}
          >
            <Plus size={15} aria-hidden="true" />
            <span class="nav-label">{item.label}</span>
          </button>
        )}
      </For>
      <button
        class="nav-link nav-search"
        type="button"
        aria-label="Search conversations"
        aria-haspopup="dialog"
        data-shortcut-action="search-sessions"
        onClick={() => {
          dismissTemporaryNavigation();
          shortcuts.run("search-sessions");
        }}
      >
        <Search size={15} aria-hidden="true" />
        <span class="nav-label">Search conversations</span>
      </button>
    </>
  );

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
                aria-current={isThreadActive(thread.thread_id) ? "page" : undefined}
                title={`${threadTitle(thread)}\n${threadMeta(thread)}`}
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
              <MoreVertical size={14} />
            </button>
          </Show>
        </div>
      </div>
    );
  };

  const threadTitle = (thread: ThreadSummary) => thread.title || thread.thread_id;
  const isBackgroundThread = (thread: ThreadSummary) => thread.kind === "background";
  const threadMeta = (thread: ThreadSummary) => {
    const owner = thread.channel_id || thread.user_id;
    return [
      isBackgroundThread(thread) ? "background" : "",
      thread.platform,
      thread.agent_name,
      owner ? truncateText(owner, 16) : "",
      thread.checkpoint_count !== undefined ? `${thread.checkpoint_count} steps` : "",
    ].filter(Boolean).join(" / ");
  };
  const positionHistoryMenu = () => {
    if (!historyMenuAnchor || !historyMenuThreadId()) return;
    const targetRect = historyMenuAnchor.getBoundingClientRect();
    if (targetRect.bottom < 0 || targetRect.top > window.innerHeight) {
      closeHistoryMenu();
      return;
    }
    const menuWidth = 160;
    const thread = historyMenuThread();
    const isRunning = thread ? runningThreadIds().has(thread.thread_id) : false;
    const estimatedHeight = isRunning ? 176 : 140;
    const spaceBelow = window.innerHeight - targetRect.bottom;
    const spaceAbove = targetRect.top;
    const opensUp = spaceBelow < estimatedHeight + 8 && spaceAbove > spaceBelow;
    let left = targetRect.right - menuWidth;
    left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
    const top = opensUp
      ? Math.max(8, targetRect.top - estimatedHeight - 4)
      : Math.min(targetRect.bottom + 4, window.innerHeight - estimatedHeight - 8);
    setHistoryMenuPos({ top, left, opensUp });
  };
  const handleHistoryScroll = (event: Event) => {
    if (!projectsVisible()) return;
    const target = event.currentTarget as HTMLElement;
    const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;

    if (historyMenuThreadId()) {
      positionHistoryMenu();
    }

    if (distanceToBottom <= 48 && historyHasMore() && !historyLoading()) {
      void loadHistory();
    }
  };
  const closeHistoryMenu = (restoreFocus = false) => {
    const anchor = historyMenuAnchor;
    setHistoryMenuThreadId(null);
    historyMenuAnchor = null;
    if (restoreFocus) anchor?.focus({ preventScroll: true });
  };

  const toggleHistoryMenu = (threadId: string, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setEditingHistoryThreadId(null);
    const current = historyMenuThreadId();
    if (current === threadId) {
      closeHistoryMenu();
      return;
    }
    historyMenuAnchor = event.currentTarget as HTMLElement;
    setHistoryMenuThreadId(threadId);
    positionHistoryMenu();
    if (event.detail === 0) {
      queueMicrotask(() => historyMenuRef?.querySelector("button")?.focus());
    }
  };
  createEffect(() => {
    if (!historyMenuThreadId()) return;
    positionHistoryMenu();
    const handler = () => positionHistoryMenu();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && historyMenuRef?.contains(target)) return;
      if (target && (target as Element).closest?.(".nav-history-action")) return;
      closeHistoryMenu();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    onCleanup(() => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
      document.removeEventListener("pointerdown", handlePointerDown);
    });
  });
  const requestDeleteHistoryThread = (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    closeHistoryMenu();
    setDeleteTarget(thread);
  };

  const cancelDelete = () => {
    setDeleteTarget(null);
  };

  const stopThreadExecution = async (thread: ThreadSummary, event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    closeHistoryMenu();
    
    const threadId = thread.thread_id;
    optimisticLocks.set(threadId, { state: "stopped", timestamp: Date.now() });
    invalidateSessions();
    
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
      invalidateSessions();
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
      });
      void loadHistory(true);
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

    closeHistoryMenu();
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
      window.dispatchEvent(
        new CustomEvent(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, {
          detail: { threadId: updated.thread_id, title: updated.title || trimmedTitle },
        }),
      );
      window.dispatchEvent(new CustomEvent(CUSTOM_DOM_EVENTS.THREADS_CHANGED));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Rename failed", "error");
    }
  };

  const handleOpenMobileNav = () => {
    setMobileDrawerOpen(true);
  };

  onMount(() => {
    if (!historyLoaded()) {
      if (selectedChatThreadId()) void loadHistory(true);
      else scheduleDeferredHistoryLoad(true);
    }
    document.addEventListener("click", handleClickOutside);
    document.addEventListener("pointerdown", handleOutsidePointerDown);
    document.addEventListener("keydown", handleNavigationKeydown);
    window.addEventListener(CUSTOM_DOM_EVENTS.OPEN_MOBILE_NAV, handleOpenMobileNav);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleThreadsChanged);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, handleThreadStartRunning);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunning);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_AGENT_UPDATED, handleThreadAgentUpdated);
    window.addEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);

    void checkForUpdates();
  });

  onCleanup(() => {
    disposed = true;
    window.clearTimeout(threadsChangedTimer);
    clearNavigationClose();
    cancelDeferredHistoryLoad();
    document.removeEventListener("click", handleClickOutside);
    document.removeEventListener("pointerdown", handleOutsidePointerDown);
    document.removeEventListener("keydown", handleNavigationKeydown);
    window.removeEventListener(CUSTOM_DOM_EVENTS.OPEN_MOBILE_NAV, handleOpenMobileNav);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREADS_CHANGED, handleThreadsChanged);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_START_RUNNING, handleThreadStartRunning);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_STOP_RUNNING, handleThreadStopRunning);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_AGENT_UPDATED, handleThreadAgentUpdated);
    window.removeEventListener(CUSTOM_DOM_EVENTS.THREAD_TITLE_UPDATED, handleThreadTitleUpdated);
  });

  return (
    <div
      class={`app-layout app-layout--resizable ${sidebar.resizing() ? "app-layout--resizing" : ""} ${collapsed() ? "app-shell--collapsed" : ""} ${isMobileViewport() && mobileDrawerOpen() ? "app-layout--drawer-open" : ""} ${props.noHeader ? "app-layout-no-header" : ""}`}
      style={{
        "--sidebar-width": `${sidebar.width()}px`,
        "--chat-viewport-height": chatViewport() ? `${chatViewport()!.height}px` : undefined,
        "--chat-viewport-top": chatViewport() ? `${chatViewport()!.offsetTop}px` : undefined,
      }}
      onClick={handleAppLayoutClick}
    >
      <aside
        id="app-shell-sidebar"
        class="sidebar app-shell-sidebar"
        classList={{ "app-shell-sidebar--open": projectsVisible() }}
        ref={sidebarRef}
        aria-label="Dashboard sidebar"
        onClick={handleSidebarClick}
        onPointerEnter={handleNavigationPointerEnter}
        onPointerLeave={handleNavigationPointerLeave}
        on:focusout={handleNavigationFocusOut}
      >
        <Show when={isMobileViewport()} fallback={
          <nav
            id={navigationRailId}
            class="app-shell-nav-rail"
            ref={navigationRail}
            aria-label="Main navigation"
            onPointerEnter={(event) => {
              if (isHoverPointer(event)) openTemporaryNavigation();
            }}
            on:focusin={handleRailFocusIn}
            onKeyDown={handleRailKeydown}
          >
            {renderNavigationItems()}
          </nav>
        }>
          <header class="app-shell-navigation-heading">
            <h2 class="app-shell-navigation-title">{navItems.find((item) => item.section === activeSection())?.label}</h2>
            <button
              class="drawer-close-btn"
              type="button"
              onClick={closeMobileDrawer}
              aria-label="Close navigation"
              data-shortcut-action="toggle-sidebar"
            >
              <X size={18} />
            </button>
          </header>
        </Show>
        <nav
          id={projectsPanelId}
          class="nav app-shell-projects"
          ref={projectsPanel}
          aria-label={isMobileViewport() ? "Main navigation" : "Projects and chat history"}
          hidden={!isMobileViewport() && !projectsVisible()}
        >
          <div class="nav-scroll" onScroll={handleHistoryScroll}>
            <Show when={isMobileViewport()}>
              {renderNavigationItems()}
            </Show>
            <div class="nav-history-group open">
              <div class="nav-projects-header">
                <span>Projects</span>
                <A href="/projects" class="nav-projects-manage" title="Manage projects">Manage</A>
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
              >
                <For each={historyGroups()}>
                  {(group) => {
                    const open = () => !collapsedProjects().has(group.key);
                    const projectState = () => navProjectsStore.projectState(group.key);
                    const previewThreads = createMemo(() => navProjectThreads(
                      group.threads, selectedChatThreadId(), runningThreadIds(), projectState()?.expanded,
                    ));
                    const groupId = `nav-project-${encodeURIComponent(group.key)}`;
                    return (
                      <section class="nav-history-workspace" aria-label={group.label}>
                        <div class="nav-project-heading">
                          <button
                            type="button"
                            class="nav-history-workspace-toggle"
                            aria-expanded={open()}
                            aria-controls={groupId}
                            title={group.label}
                            onClick={() => toggleProject(group.key)}
                          >
                            <Show when={group.isRepo} fallback={<Folder size={15} />}>
                              <GitBranch size={15} />
                            </Show>
                            <span class="nav-history-workspace-label">{group.label}</span>
                            <Show when={group.is_pinned}><Pin size={12} aria-label="Pinned project" /></Show>
                            <span class="nav-history-workspace-caret">
                              <Show when={open()} fallback={<ChevronRight size={13} />}>
                                <ChevronDown size={13} />
                              </Show>
                            </span>
                          </button>
                          <button
                            type="button"
                            class="nav-project-add"
                            disabled={Boolean(openingProject())}
                            aria-label={`New chat in ${group.label}`}
                            title={`New chat in ${group.label}`}
                            onClick={() => startProjectChat(group)}
                          >
                            <Plus size={16} />
                          </button>
                          <Show when={group.id}>
                            <ProjectActions
                              project={{ id: group.id!, label: group.label,
                                is_pinned: Boolean(group.is_pinned), status: group.status || "active" }}
                              onMenuOpenChange={handleProjectMenuOpenChange}
                            />
                          </Show>
                        </div>
                        <div id={groupId} class="nav-history-workspace-threads" hidden={!open()}>
                          <Show when={group.threads.length === 0}><div class="nav-history-status">No conversations yet</div></Show>
                          <For each={previewThreads()}>
                            {(thread) => renderHistoryItem(thread)}
                          </For>
                          <Show when={group.threads.length > 0 && (!projectState()?.expanded || projectState()?.hasMore)}>
                            <button
                              class="nav-project-view-all"
                              type="button"
                              disabled={projectState()?.loading}
                              aria-label={`Load more chats in ${group.label}`}
                              aria-busy={projectState()?.loading}
                              title={projectState()?.error || undefined}
                              onClick={() => void navProjectsStore.loadProjectThreads(group.key)}
                            >
                              {projectState()?.loading ? "Loading..." : projectState()?.error ? "Retry" : projectState()?.expanded ? "Load more" : "View all"}
                            </button>
                          </Show>
                        </div>
                      </section>
                    );
                  }}
                </For>

                <Show when={historyGroups().length === 0 && !historyError() && (!historyLoaded() || historyLoading())}>
                  <div class="nav-history-skeleton" aria-label="Loading history">
                    <span class="skeleton-line nav-history-skeleton-line" />
                    <span class="skeleton-line nav-history-skeleton-line short" />
                    <span class="skeleton-line nav-history-skeleton-line" />
                  </div>
                </Show>
                <Show when={historyGroups().length === 0 && historyLoaded() && !historyLoading() && !historyError()}>
                  <div class="nav-history-status">No projects</div>
                </Show>
                <Show when={historyGroups().length > 0 && historyLoading()}>
                  <div class="nav-history-status">Loading more...</div>
                </Show>
                <Show when={historyError()}>
                  <button
                    class="nav-history-retry"
                    type="button"
                    onClick={() => void loadHistory(true)}
                  >
                    Retry
                  </button>
                </Show>
                <Show
                  when={
                    historyGroups().length > 0
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
          </div>
        </nav>
        <div class="sidebar-footer">
          <div class="sidebar-footer-row">
            <div class="user-menu-wrapper">
              <button
                ref={accountButton}
                class="user-avatar"
                type="button"
                onClick={() => setMenuOpen(!menuOpen())}
                title="Account"
                aria-label="Account"
                aria-expanded={menuOpen()}
              >
                <span class="user-avatar-initial" aria-hidden="true">A</span>
              </button>
              <Show when={menuOpen()}>
                <div class="user-menu">
                  <button class="user-menu-item user-menu-danger" type="button" onClick={async () => {
                    try {
                      await postJson("/logout", {});
                      window.location.href = "/login";
                    } catch (error) {
                      showToast(error instanceof Error ? error.message : "Logout failed", "error");
                    }
                  }}>
                    <LogOut size={14} />
                    <span>Logout</span>
                  </button>
                </div>
              </Show>
            </div>
            <Show when={isMobileViewport() || projectsVisible()}>
              <div class="sidebar-footer-brand">
                <div class="brand-title">Kai Console</div>
                <button
                  type="button"
                  class="brand-subtitle brand-version-btn"
                  classList={{ "has-update": versionInfo()?.has_update }}
                  onClick={openUpdateDialog}
                  title={availableUpdateTitle() ?? `Version v${versionInfo()?.current_version || ""}`}
                >
                  <span>v{versionInfo()?.current_version || "..."}</span>
                  <Show when={versionInfo()?.has_update}>
                    <span class="brand-update-badge">
                      <Sparkles size={11} aria-hidden="true" />
                      <span>Update: v{versionInfo()?.latest_version}</span>
                    </span>
                  </Show>
                </button>
              </div>
            </Show>
            <Show when={!isMobileViewport()}>
              {renderSidebarPin()}
            </Show>
            <A href="/settings" class="btn btn-icon btn-ghost" title="Settings" aria-label="Settings">
              <Settings size={15} />
            </A>
          </div>
        </div>
        <Show when={!collapsed() && !isMobileViewport()}>
          <div
            class="sidebar-resize-handle"
            role="separator"
            tabIndex={0}
            aria-label="Resize navigation"
            aria-orientation="vertical"
            aria-controls="app-shell-sidebar"
            aria-valuemin={sidebar.minWidth}
            aria-valuemax={sidebar.maxWidth()}
            aria-valuenow={sidebar.width()}
            aria-valuetext={`${sidebar.width()} pixels`}
            title="Drag to resize navigation. Double-click to reset."
            onPointerDown={sidebar.startResize}
            onKeyDown={sidebar.handleKeyDown}
            onDblClick={sidebar.reset}
          />
        </Show>
      </aside>
      <main class={`main ${props.noHeader ? "main-no-header" : ""}`}>
        <Show when={!props.noHeader}>
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
                data-shortcut-action="toggle-sidebar"
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
        </Show>
        <div class={`content ${props.noHeader ? "content-no-header" : ""}`}>{props.children}</div>
      </main>

      <Show when={historyMenuThread()}>
        {(thread) => (
          <Portal>
            <div
              ref={historyMenuRef}
              class="nav-history-menu nav-history-menu--portal"
              role="menu"
              style={{
                top: `${historyMenuPos().top}px`,
                left: `${historyMenuPos().left}px`,
              }}
            >
              <Show when={runningThreadIds().has(thread().thread_id)}>
                <button
                  class="nav-history-menu-item nav-history-menu-danger"
                  type="button"
                  onClick={(event) => stopThreadExecution(thread(), event)}
                >
                  <Square size={13} fill="currentColor" />
                  <span>Stop Execution</span>
                </button>
              </Show>
              <button
                class="nav-history-menu-item"
                type="button"
                onClick={(event) => startRenameHistoryThread(thread(), event)}
              >
                <Pencil size={13} />
                <span>Rename</span>
              </button>
              <button class="nav-history-menu-item" type="button" onClick={() => {
                setExportTarget(thread());
                closeHistoryMenu();
              }}>
                <Download size={13} />
                <span>Export</span>
              </button>
              <button
                class="nav-history-menu-item nav-history-menu-danger"
                type="button"
                onClick={(event) => requestDeleteHistoryThread(thread(), event)}
              >
                <Trash2 size={13} />
                <span>Delete</span>
              </button>
            </div>
          </Portal>
        )}
      </Show>
      <ExportChatDialog target={exportTarget()} onClose={() => setExportTarget(null)} />
      <DeleteThreadDialog
        open={deleteTarget() !== null}
        thread={deleteTarget()}
        deleting={deleting()}
        onClose={cancelDelete}
        onConfirm={() => void confirmDeleteHistoryThread()}
      />
      <UpdateDialog />
    </div>
  );
}
