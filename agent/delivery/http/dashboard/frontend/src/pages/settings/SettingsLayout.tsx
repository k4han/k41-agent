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
  Network,
  Palette,
  ServerCog,
  Users,
  Workflow,
} from "lucide-solid";
import { createSignal, JSX, onMount, Show } from "solid-js";

import { STORAGE_KEYS } from "@/lib/uiConstants";

type SettingsNavItem = {
  href: string;
  label: string;
  icon: JSX.Element;
};

const settingsNavItems: SettingsNavItem[] = [
  { href: "/settings/config", label: "Runtime", icon: <Cog size={15} /> },
  { href: "/settings/backends", label: "Backends", icon: <ServerCog size={15} /> },
  { href: "/settings/sandboxes", label: "Sandboxes", icon: <CloudCog size={15} /> },
  { href: "/settings/providers", label: "Providers", icon: <Workflow size={15} /> },
  { href: "/settings/connections", label: "Connections", icon: <Link2 size={15} /> },
  { href: "/settings/channels", label: "Channels", icon: <Network size={15} /> },
  { href: "/settings/agents", label: "Agents", icon: <Users size={15} /> },
  { href: "/settings/skills", label: "Skills", icon: <BookOpen size={15} /> },
  { href: "/settings/prompt-variables", label: "Prompt Variables", icon: <Braces size={15} /> },
  { href: "/settings/security", label: "Security", icon: <KeyRound size={15} /> },
  { href: "/settings/usage", label: "Usage", icon: <BarChart3 size={15} /> },
  { href: "/settings/appearance", label: "Appearance", icon: <Palette size={15} /> },
];

export function SettingsLayout(props: {
  title: string;
  actions?: JSX.Element;
  breadcrumbLabel?: string;
  contentWidth?: "narrow" | "medium" | "wide";
  children: JSX.Element;
}) {
  const location = useLocation();
  const [collapsed, setCollapsed] = createSignal(false);

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
          <A href="/" class="nav-link" title="Back">
            <ArrowLeft size={15} />
            <span class="nav-label">Back</span>
          </A>
          <div class="nav-separator" />
          <div class="nav-section-title">Settings</div>
          {settingsNavItems.map((item) => (
            <A
              href={item.href}
              class={`nav-link ${isActive(item.href) ? "active" : ""}`}
              title={item.label}
            >
              {item.icon}
              <span class="nav-label">{item.label}</span>
            </A>
          ))}
        </nav>
      </aside>
      <main class="main">
        <header class="topbar settings-topbar">
          <div class="settings-breadcrumb" aria-label="Breadcrumb">
            <span class="settings-breadcrumb-root">Settings</span>
            <span class="settings-breadcrumb-separator">/</span>
            <span class="settings-breadcrumb-current">{props.breadcrumbLabel || props.title}</span>
          </div>
          <div class="row-wrap">{props.actions}</div>
        </header>
        <div class={`content settings-content settings-content-${props.contentWidth || "medium"}`}>
          <A href="/" class="settings-back-link">
            <ArrowLeft size={13} />
            Back to dashboard
          </A>
          <div class="settings-page-heading">
            <h1 class="page-title">{props.title}</h1>
          </div>
          <div class="settings-page-body">{props.children}</div>
        </div>
      </main>
    </div>
  );
}
