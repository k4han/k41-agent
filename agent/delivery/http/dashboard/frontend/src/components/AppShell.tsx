import { A, useLocation } from "@solidjs/router";
import {
  Activity,
  Bot,
  CalendarClock,
  ChevronsLeft,
  ChevronsRight,
  History,
  LogOut,
  MessageSquare,
  PanelsTopLeft,
  PlaySquare,
  Settings,
} from "lucide-solid";
import { createSignal, JSX, onCleanup, onMount, Show } from "solid-js";

type NavItem = {
  href: string;
  label: string;
  icon: JSX.Element;
};

const navItems: NavItem[] = [
  { href: "/", label: "Overview", icon: <PanelsTopLeft size={15} /> },
  { href: "/chat", label: "Chat", icon: <MessageSquare size={15} /> },
  { href: "/history", label: "History", icon: <History size={15} /> },
  { href: "/sessions", label: "Active Sessions", icon: <Activity size={15} /> },
  { href: "/tasks", label: "Background Tasks", icon: <PlaySquare size={15} /> },
  { href: "/scheduler", label: "Scheduler", icon: <CalendarClock size={15} /> },
];

export function AppShell(props: {
  title: string;
  subtitle?: string;
  actions?: JSX.Element;
  children: JSX.Element;
}) {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [collapsed, setCollapsed] = createSignal(false);

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
  };

  const toggleSidebar = () => {
    const next = !collapsed();
    setCollapsed(next);
    window.localStorage.setItem("kaka-dashboard-sidebar", next ? "collapsed" : "expanded");
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
          {navItems.map((item) => (
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
