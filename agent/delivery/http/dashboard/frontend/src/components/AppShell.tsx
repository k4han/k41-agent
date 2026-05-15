import { A, useLocation } from "@solidjs/router";
import {
  Activity,
  Bot,
  CalendarClock,
  KeyRound,
  LogOut,
  MessageSquare,
  Moon,
  Network,
  PanelsTopLeft,
  PlaySquare,
  Settings,
  Sun,
  Users,
  Workflow,
} from "lucide-solid";
import { createSignal, JSX, onMount } from "solid-js";

type NavItem = {
  href: string;
  label: string;
  icon: JSX.Element;
};

const navItems: NavItem[] = [
  { href: "/", label: "Overview", icon: <PanelsTopLeft size={15} /> },
  { href: "/channels", label: "Channels", icon: <Network size={15} /> },
  { href: "/agents", label: "Agents", icon: <Bot size={15} /> },
  { href: "/chat", label: "Chat", icon: <MessageSquare size={15} /> },
  { href: "/sessions", label: "Active Sessions", icon: <Activity size={15} /> },
  { href: "/tasks", label: "Background Tasks", icon: <PlaySquare size={15} /> },
  { href: "/scheduler", label: "Scheduler", icon: <CalendarClock size={15} /> },
  { href: "/config", label: "Configuration", icon: <Settings size={15} /> },
  { href: "/providers", label: "Provider Config", icon: <Workflow size={15} /> },
];

export function AppShell(props: {
  title: string;
  subtitle?: string;
  actions?: JSX.Element;
  children: JSX.Element;
}) {
  const location = useLocation();
  const [dark, setDark] = createSignal(false);

  onMount(() => {
    const stored = window.localStorage.getItem("kaka-dashboard-theme");
    const next =
      stored === "dark" ||
      (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches);
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  });

  const toggleTheme = () => {
    const next = !dark();
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    window.localStorage.setItem("kaka-dashboard-theme", next ? "dark" : "light");
  };

  const isActive = (href: string) => {
    if (href === "/") {
      return location.pathname === "/";
    }
    return location.pathname.startsWith(href);
  };

  return (
    <div class="app-layout">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">
            <Bot size={16} />
          </div>
          <div>
            <div class="brand-title">Kaka Dashboard</div>
            <div class="brand-subtitle">Agent control plane</div>
          </div>
        </div>
        <nav class="nav">
          {navItems.map((item) => (
            <A
              href={item.href}
              class={`nav-link ${isActive(item.href) ? "active" : ""}`}
            >
              {item.icon}
              <span>{item.label}</span>
            </A>
          ))}
          <div class="nav-separator" />
          <A
            href="/change-password"
            class={`nav-link ${isActive("/change-password") ? "active" : ""}`}
          >
            <KeyRound size={15} />
            <span>Change Password</span>
          </A>
          <a class="nav-link" href="/logout">
            <LogOut size={15} />
            <span>Logout</span>
          </a>
        </nav>
        <div class="sidebar-footer">
          <button class="btn btn-icon" type="button" onClick={toggleTheme}>
            {dark() ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <div class="hint">Solid + Vite</div>
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

