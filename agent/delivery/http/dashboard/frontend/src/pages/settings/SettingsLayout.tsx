import { A, useLocation } from "@solidjs/router";
import {
  ArrowLeft,
  BarChart3,
  BookOpen,
  Bot,
  Braces,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CloudCog,
  Cog,
  KeyRound,
  Link2,
  Menu,
  Network,
  Palette,
  Route,
  Search,
  ServerCog,
  Users,
  Wrench,
  Workflow,
  X,
} from "lucide-solid";
import { createMemo, createSignal, For, JSX, onCleanup, onMount, Show } from "solid-js";

import { STORAGE_KEYS } from "@/lib/uiConstants";
import { useMobileDrawer } from "@/lib/useMobileDrawer";

type SettingsNavItem = {
  href: string;
  label: string;
  icon: () => JSX.Element;
  description?: string;
  keywords?: string;
};

type SettingsNavGroup = {
  id: string;
  label: string;
  items: SettingsNavItem[];
};

const settingsNavGroups: SettingsNavGroup[] = [
  {
    id: "general",
    label: "Preferences",
    items: [
      { href: "/settings/appearance", label: "Appearance", icon: () => <Palette size={15} />, description: "Theme & display", keywords: "theme dark light system appearance timezone display preferences" },
      { href: "/settings/security", label: "Security", icon: () => <KeyRound size={15} />, description: "Password & access", keywords: "security password auth credentials" },
    ],
  },
  {
    id: "workspace",
    label: "Workspace",
    items: [
      { href: "/settings/connections", label: "Connections", icon: () => <Link2 size={15} />, description: "Repos, MCP & Calendar", keywords: "repositories mcp connections github google calendar" },
      { href: "/settings/sandboxes", label: "Active Sandboxes", icon: () => <CloudCog size={15} />, description: "Live instances", keywords: "sandbox container live instances" },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    items: [
      { href: "/settings/providers", label: "Providers", icon: () => <Workflow size={15} />, description: "Models, web, environments & decisions", keywords: "llm provider model openai anthropic web search firecrawl tavily brave bing google daytona modal backend cloudflare" },
      { href: "/settings/decisions", label: "Decisions & Routing", icon: () => <Route size={15} />, description: "Clef-flash & routing", keywords: "decision routing clef cloudflare model strategy cascade" },
      { href: "/settings/tools", label: "Tools", icon: () => <Wrench size={15} />, description: "Global tool config", keywords: "tool api key search image web config" },
      { href: "/settings/agents", label: "Agents", icon: () => <Users size={15} />, description: "Agent profiles", keywords: "agent persona" },
      { href: "/settings/skills", label: "Skills", icon: () => <BookOpen size={15} />, description: "Reusable skills", keywords: "skill repository" },
      { href: "/settings/prompt-variables", label: "Prompt Variables", icon: () => <Braces size={15} />, description: "Template variables", keywords: "prompt variable template" },
    ],
  },
  {
    id: "integration",
    label: "Integrations",
    items: [
      { href: "/settings/channels", label: "Channels", icon: () => <Network size={15} />, description: "Telegram / Discord / Zalo", keywords: "channel telegram discord zalo messaging" },
    ],
  },
  {
    id: "system",
    label: "System",
    items: [
      { href: "/settings/config", label: "Server Runtime", icon: () => <Cog size={15} />, description: "Host, port, DB & bootstrap", keywords: "bootstrap config env runtime server database host port" },
    ],
  },
  {
    id: "insights",
    label: "Insights",
    items: [
      { href: "/settings/usage", label: "Usage", icon: () => <BarChart3 size={15} />, description: "Token & cost", keywords: "usage analytics token cost" },
    ],
  },
];

type BreadcrumbSegment = {
  label: string;
  href?: string;
};

const SETTINGS_AUTO_COLLAPSE_BREAKPOINT = 1280;
const SETTINGS_AUTO_COLLAPSE_QUERY = `(max-width: ${SETTINGS_AUTO_COLLAPSE_BREAKPOINT}px)`;
let settingsNavScrollTop = 0;

export function SettingsLayout(props: {
  title: string;
  actions?: JSX.Element;
  breadcrumbLabel?: string;
  breadcrumbSegments?: BreadcrumbSegment[];
  contentWidth?: "narrow" | "medium" | "wide";
  children: JSX.Element;
}) {
  const location = useLocation();
  const [collapsed, setCollapsed] = createSignal(false);
  const [userLocked, setUserLocked] = createSignal(false);
  const [navQuery, setNavQuery] = createSignal("");
  const {
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer,
    handleAppLayoutClick,
    handleNavClick,
    handleKeydown,
  } = useMobileDrawer({ sidebarId: "settings-layout-sidebar" });

  let searchInputRef: HTMLInputElement | undefined;
  let settingsNavElement: HTMLElement | undefined;

  const isActive = (href: string) => location.pathname === href || location.pathname.startsWith(href + "/");

  const toggleSidebar = () => {
    const next = !collapsed();
    setCollapsed(next);
    setUserLocked(true);
    window.localStorage.setItem(STORAGE_KEYS.SETTINGS_SIDEBAR_COLLAPSED, next ? "collapsed" : "expanded");
  };

  onMount(() => {
    if (settingsNavElement) {
      settingsNavElement.scrollTop = settingsNavScrollTop;
    }

    const saveNavScrollPosition = () => {
      if (settingsNavElement) {
        settingsNavScrollTop = settingsNavElement.scrollTop;
      }
    };
    settingsNavElement?.addEventListener("scroll", saveNavScrollPosition, { passive: true });
    onCleanup(() => {
      saveNavScrollPosition();
      settingsNavElement?.removeEventListener("scroll", saveNavScrollPosition);
    });

    const saved = window.localStorage.getItem(STORAGE_KEYS.SETTINGS_SIDEBAR_COLLAPSED);
    if (saved === "collapsed") {
      setCollapsed(true);
      setUserLocked(true);
    } else if (saved === "expanded") {
      setCollapsed(false);
      setUserLocked(true);
    } else {
      // Auto-collapse on displays <= 1280px when no explicit preference is stored
      if (typeof window !== "undefined" && window.innerWidth <= SETTINGS_AUTO_COLLAPSE_BREAKPOINT) {
        setCollapsed(true);
      }
    }

    // Responsive listener when not manually locked by user
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      const mql = window.matchMedia(SETTINGS_AUTO_COLLAPSE_QUERY);
      const handleMediaChange = (event: MediaQueryListEvent) => {
        if (!userLocked()) {
          setCollapsed(event.matches);
        }
      };
      mql.addEventListener("change", handleMediaChange);
      onCleanup(() => mql.removeEventListener("change", handleMediaChange));
    }

    document.addEventListener("keydown", handleKeydown);

    // Global keyboard search handler: auto-expand and focus search on '/' or 'Ctrl+K'
    const onGlobalKey = (e: KeyboardEvent) => {
      if ((e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) && !isMobileViewport()) {
        const target = e.target as HTMLElement;
        if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
        e.preventDefault();
        if (collapsed()) {
          setCollapsed(false);
          setUserLocked(true);
          window.localStorage.setItem(STORAGE_KEYS.SETTINGS_SIDEBAR_COLLAPSED, "expanded");
          setTimeout(() => searchInputRef?.focus(), 50);
        } else {
          searchInputRef?.focus();
        }
      }
    };
    document.addEventListener("keydown", onGlobalKey);
    onCleanup(() => document.removeEventListener("keydown", onGlobalKey));
  });

  onCleanup(() => {
    document.removeEventListener("keydown", handleKeydown);
  });

  const settingsHomeHref = "/settings/config";
  const homeHref = "/";

  const filteredGroups = createMemo<SettingsNavGroup[]>(() => {
    const query = navQuery().trim().toLowerCase();
    if (!query) return settingsNavGroups;
    return settingsNavGroups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) =>
          [item.label, item.description, item.keywords, item.href]
            .join(" ")
            .toLowerCase()
            .includes(query),
        ),
      }))
      .filter((group) => group.items.length > 0);
  });

  const totalFilteredCount = createMemo(() => filteredGroups().reduce((acc, g) => acc + g.items.length, 0));

  const segments = createMemo<BreadcrumbSegment[]>(() => {
    if (props.breadcrumbSegments && props.breadcrumbSegments.length > 0) {
      return [{ label: "Settings", href: settingsHomeHref }, ...props.breadcrumbSegments];
    }
    return [
      { label: "Settings", href: settingsHomeHref },
      { label: props.breadcrumbLabel || props.title },
    ];
  });

  return (
    <div
      class={`app-layout settings-app-layout ${collapsed() ? "sidebar-collapsed" : ""} ${isMobileViewport() && mobileDrawerOpen() ? "app-layout--drawer-open" : ""}`}
      onClick={handleAppLayoutClick}
    >
      <aside id="settings-layout-sidebar" class="sidebar settings-sidebar">
        <div class="brand">
          <Show
            when={!collapsed() || isMobileViewport()}
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
              <div class="brand-title">Kai Console</div>
              <div class="brand-subtitle">Settings</div>
            </div>
            <Show
              when={isMobileViewport()}
              fallback={
                <button
                  class="brand-collapse-btn"
                  type="button"
                  onClick={toggleSidebar}
                  title="Collapse sidebar"
                  aria-label="Collapse sidebar"
                >
                  <ChevronsLeft size={14} />
                </button>
              }
            >
              <button
                class="drawer-close-btn"
                type="button"
                onClick={closeMobileDrawer}
                aria-label="Close navigation"
              >
                <X size={18} />
              </button>
            </Show>
          </Show>
        </div>
        <nav ref={settingsNavElement} class="nav settings-nav" onClick={handleNavClick}>
          <A
            href={homeHref}
            class="nav-link settings-back-home"
            title="Back to home"
            aria-label="Back to home"
          >
            <ArrowLeft size={15} />
            <Show when={!collapsed() || isMobileViewport()}>
              <span class="nav-label">Back to home</span>
            </Show>
          </A>

          <Show when={!collapsed() || isMobileViewport()}>
            <div class="settings-nav-search">
              <Search size={13} class="settings-nav-search-icon" />
              <input
                ref={searchInputRef}
                type="text"
                class="settings-nav-search-input"
                placeholder="Search settings… ( / )"
                value={navQuery()}
                aria-label="Search settings"
                onInput={(event) => setNavQuery(event.currentTarget.value)}
              />
              <Show when={navQuery().length > 0}>
                <button
                  type="button"
                  class="settings-nav-search-clear"
                  title="Clear search"
                  aria-label="Clear search"
                  onClick={() => setNavQuery("")}
                >
                  <X size={12} />
                </button>
              </Show>
            </div>
            <Show when={navQuery().trim().length > 0}>
              <div class="settings-nav-search-meta">
                <span class="settings-nav-search-count">{totalFilteredCount()} results</span>
                <Show when={totalFilteredCount() === 0}>
                  <span class="settings-nav-search-hint">Try “provider” or “workspace”</span>
                </Show>
              </div>
            </Show>
          </Show>

          <div class="settings-nav-groups">
            <For each={filteredGroups()}>
              {(group) => (
                <div class="settings-nav-group">
                  <Show when={!collapsed() || isMobileViewport()}>
                    <div class="settings-nav-group-title">{group.label}</div>
                  </Show>
                  <For each={group.items}>
                    {(item) => (
                      <A
                        href={item.href}
                        class={`nav-link ${isActive(item.href) ? "active" : ""}`}
                        title={item.description ? `${item.label} — ${item.description}` : item.label}
                      >
                        {item.icon()}
                        <Show when={!collapsed() || isMobileViewport()}>
                          <span class="nav-label">
                            <span class="settings-nav-label-text">{item.label}</span>
                          </span>
                        </Show>
                      </A>
                    )}
                  </For>
                </div>
              )}
            </For>
          </div>

          <Show when={filteredGroups().length === 0}>
            <div class="settings-nav-empty">
              <Search size={16} />
              <div>No matches for “{navQuery()}”</div>
              <div class="settings-nav-empty-hint">Try a different keyword</div>
            </div>
          </Show>
        </nav>
        <Show when={!collapsed() || isMobileViewport()}>
          <div class="settings-sidebar-footer-hint">
            <span class="hint">Press <span class="kbd">/</span> to search</span>
          </div>
        </Show>
      </aside>
      <main class="main">
        <header class="topbar settings-topbar">
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
              aria-controls="settings-layout-sidebar"
            >
              <Menu size={18} />
            </button>
          </Show>
          <nav class="settings-breadcrumb" aria-label="Breadcrumb">
            <For each={segments()}>
              {(segment, index) => (
                <>
                  <Show when={index() > 0}>
                    <span class="settings-breadcrumb-separator" aria-hidden="true">
                      <ChevronRight size={12} />
                    </span>
                  </Show>
                  <Show
                    when={index() !== segments().length - 1 && segment.href}
                    fallback={
                      <span class="settings-breadcrumb-current" aria-current="page" title={segment.label}>
                        {segment.label}
                      </span>
                    }
                  >
                    <A href={segment.href!} class="settings-breadcrumb-link" title={segment.label}>
                      {segment.label}
                    </A>
                  </Show>
                </>
              )}
            </For>
          </nav>
          <div class="row-wrap settings-topbar-actions">{props.actions}</div>
        </header>
        <div class={`content settings-content settings-content-${props.contentWidth || "medium"}`}>
          <div class="settings-page-heading">
            <div class="settings-page-heading-text">
              <h1 class="page-title">{props.title}</h1>
            </div>
          </div>
          <div class="settings-page-body">{props.children}</div>
        </div>
      </main>
    </div>
  );
}
