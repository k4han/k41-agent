import { A, useLocation } from "@solidjs/router";
import {
  ArrowLeft,
  BarChart3,
  BookOpen,
  Bot,
  Braces,
  ChevronsLeft,
  ChevronsRight,
  CloudCog,
  Cog,
  KeyRound,
  Link2,
  Menu,
  Network,
  Palette,
  Search,
  ServerCog,
  Users,
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
};

const settingsNavItems: SettingsNavItem[] = [
  { href: "/settings/config", label: "Runtime", icon: () => <Cog size={15} /> },
  { href: "/settings/backends", label: "Backends", icon: () => <ServerCog size={15} /> },
  { href: "/settings/sandboxes", label: "Sandboxes", icon: () => <CloudCog size={15} /> },
  { href: "/settings/providers", label: "Providers", icon: () => <Workflow size={15} /> },
  { href: "/settings/connections", label: "Connections", icon: () => <Link2 size={15} /> },
  { href: "/settings/channels", label: "Channels", icon: () => <Network size={15} /> },
  { href: "/settings/agents", label: "Agents", icon: () => <Users size={15} /> },
  { href: "/settings/skills", label: "Skills", icon: () => <BookOpen size={15} /> },
  { href: "/settings/prompt-variables", label: "Prompt Variables", icon: () => <Braces size={15} /> },
  { href: "/settings/security", label: "Security", icon: () => <KeyRound size={15} /> },
  { href: "/settings/usage", label: "Usage", icon: () => <BarChart3 size={15} /> },
  { href: "/settings/appearance", label: "Appearance", icon: () => <Palette size={15} /> },
];

type BreadcrumbSegment = {
  label: string;
  href?: string;
};

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
  const [navQuery, setNavQuery] = createSignal("");
  const {
    isMobileViewport,
    mobileDrawerOpen,
    setMobileDrawerOpen,
    closeMobileDrawer,
    handleAppLayoutClick,
    handleKeydown,
  } = useMobileDrawer({ sidebarId: "settings-layout-sidebar" });

  const isActive = (href: string) => location.pathname === href;

  const toggleSidebar = () => {
    const next = !collapsed();
    setCollapsed(next);
    window.localStorage.setItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, next ? "collapsed" : "expanded");
  };

  onMount(() => {
    if (window.localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED) === "collapsed") {
      setCollapsed(true);
    }
    document.addEventListener("keydown", handleKeydown);
  });

  onCleanup(() => {
    document.removeEventListener("keydown", handleKeydown);
  });

  const settingsHomeHref = "/settings/config";
  const homeHref = "/";

  const filteredNavItems = createMemo<SettingsNavItem[]>(() => {
    const query = navQuery().trim().toLowerCase();
    if (!query) {
      return settingsNavItems;
    }
    return settingsNavItems.filter((item) => item.label.toLowerCase().includes(query));
  });

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
      class={`app-layout ${collapsed() ? "sidebar-collapsed" : ""} ${isMobileViewport() && mobileDrawerOpen() ? "app-layout--drawer-open" : ""}`}
      onClick={handleAppLayoutClick}
    >
      <aside id="settings-layout-sidebar" class="sidebar">
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
          <A
            href={homeHref}
            class="nav-link settings-back-home"
            title="Back to home"
            aria-label="Back to home"
          >
            <ArrowLeft size={15} />
            <span class="nav-label">Back to home</span>
          </A>
          <div class="nav-section-title">Settings</div>
          <Show when={!collapsed()}>
            <div class="settings-nav-search">
              <Search size={13} class="settings-nav-search-icon" />
              <input
                type="text"
                class="settings-nav-search-input"
                placeholder="Search settings..."
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
          </Show>
          <For each={filteredNavItems()}>
            {(item) => (
              <A
                href={item.href}
                class={`nav-link ${isActive(item.href) ? "active" : ""}`}
                title={item.label}
              >
                {item.icon()}
                <span class="nav-label">{item.label}</span>
              </A>
            )}
          </For>
          <Show when={filteredNavItems().length === 0}>
            <div class="settings-nav-empty">No matches</div>
          </Show>
        </nav>
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
            {segments().map((segment, index) => {
              const isLast = index === segments().length - 1;
              return (
                <>
                  {index > 0 && <span class="settings-breadcrumb-separator">/</span>}
                  <Show
                    when={!isLast && segment.href}
                    fallback={
                      <span class="settings-breadcrumb-current">{segment.label}</span>
                    }
                  >
                    <A href={segment.href!} class="settings-breadcrumb-link">
                      {segment.label}
                    </A>
                  </Show>
                </>
              );
            })}
          </nav>
          <div class="row-wrap">{props.actions}</div>
        </header>
        <div class={`content settings-content settings-content-${props.contentWidth || "medium"}`}>
          <div class="settings-page-heading">
            <h1 class="page-title">{props.title}</h1>
          </div>
          <div class="settings-page-body">{props.children}</div>
        </div>
      </main>
    </div>
  );
}
