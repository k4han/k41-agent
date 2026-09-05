import { createSignal, onMount, Show } from "solid-js";
import { Check, Monitor, Moon, Sun, Eye } from "lucide-solid";

import { SettingsLayout } from "./SettingsLayout";
import { STORAGE_KEYS, THEME_OPTIONS } from "@/lib/uiConstants";

type ThemeMode = "light" | "dark" | "system";

export function AppearancePage() {
  const [dark, setDark] = createSignal(false);
  const [mode, setMode] = createSignal<ThemeMode>("system");
  const [systemDark, setSystemDark] = createSignal(false);

  const syncFromStorage = () => {
    const stored = window.localStorage.getItem(STORAGE_KEYS.THEME) as ThemeMode | null;
    const sys = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setSystemDark(sys);
    if (!stored || stored === THEME_OPTIONS.SYSTEM) {
      setMode("system");
      setDark(sys);
    } else {
      setMode(stored as ThemeMode);
      setDark(stored === THEME_OPTIONS.DARK);
    }
  };

  onMount(() => {
    syncFromStorage();
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      setSystemDark(mql.matches);
      if (mode() === "system") {
        setDark(mql.matches);
        document.documentElement.classList.toggle("dark", mql.matches);
      }
    };
    mql.addEventListener("change", handler);
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEYS.THEME) syncFromStorage();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      mql.removeEventListener("change", handler);
      window.removeEventListener("storage", onStorage);
    };
  });

  const setTheme = (next: ThemeMode) => {
    if (next === THEME_OPTIONS.SYSTEM) {
      window.localStorage.removeItem(STORAGE_KEYS.THEME);
      const sys = window.matchMedia("(prefers-color-scheme: dark)").matches;
      setMode("system");
      setDark(sys);
      document.documentElement.classList.toggle("dark", sys);
    } else {
      const isDark = next === THEME_OPTIONS.DARK;
      setMode(next);
      setDark(isDark);
      document.documentElement.classList.toggle("dark", isDark);
      window.localStorage.setItem(STORAGE_KEYS.THEME, next);
    }
  };

  const resolvedLabel = () => {
    if (mode() === "system") return systemDark() ? "Dark (system)" : "Light (system)";
    return mode() === "dark" ? "Dark" : "Light";
  };

  return (
    <SettingsLayout
      title="Appearance"
      contentWidth="narrow"
    >
      <section class="panel appearance-panel">
        <div class="panel-header">
          <div class="panel-title row">
            {dark() ? <Moon size={14} /> : <Sun size={14} />}
            Theme
            <span class="badge">{resolvedLabel()}</span>
          </div>
          <span class="hint">{mode() === "system" ? "Syncing with OS" : "Manual override"}</span>
        </div>
        <div class="panel-body">
          <div class="stack" style={{ gap: "18px" }}>
            <div class="theme-selector">
              <button
                class={`theme-option ${mode() === "light" ? "active" : ""}`}
                type="button"
                aria-pressed={mode() === "light"}
                onClick={() => setTheme("light")}
              >
                <span class="theme-option-icon">
                  <Sun size={20} />
                </span>
                <span class="theme-option-label">Light</span>
                <Show when={mode() === "light"}>
                  <span class="theme-option-check"><Check size={12} /></span>
                </Show>
              </button>
              <button
                class={`theme-option ${mode() === "dark" ? "active" : ""}`}
                type="button"
                aria-pressed={mode() === "dark"}
                onClick={() => setTheme("dark")}
              >
                <span class="theme-option-icon">
                  <Moon size={20} />
                </span>
                <span class="theme-option-label">Dark</span>
                <Show when={mode() === "dark"}>
                  <span class="theme-option-check"><Check size={12} /></span>
                </Show>
              </button>
              <button
                class={`theme-option ${mode() === "system" ? "active" : ""}`}
                type="button"
                aria-pressed={mode() === "system"}
                onClick={() => setTheme("system")}
              >
                <span class="theme-option-icon">
                  <Monitor size={20} />
                </span>
                <span class="theme-option-label">System</span>
                <Show when={mode() === "system"}>
                  <span class="theme-option-check"><Check size={12} /></span>
                </Show>
              </button>
            </div>

            <div class="appearance-preview">
              <div class="appearance-preview-header">
                <Eye size={13} />
                <span>Preview</span>
                <span class="hint" style={{ "margin-left": "auto", "font-size": "11px" }}>Live preview</span>
              </div>
              <div class="appearance-preview-body">
                <div class="appearance-preview-mock">
                  <div class="appearance-mock-topbar">
                    <span class="appearance-mock-dot" />
                    <span class="appearance-mock-dot" />
                    <span class="appearance-mock-dot" />
                    <span class="appearance-mock-title">Kai Console</span>
                  </div>
                  <div class="appearance-mock-content">
                    <div class="appearance-mock-card">
                      <div class="appearance-mock-line" style={{ width: "62%" }} />
                      <div class="appearance-mock-line short" />
                      <div class="appearance-mock-actions">
                        <span class="appearance-mock-btn primary">Primary</span>
                        <span class="appearance-mock-btn">Default</span>
                      </div>
                    </div>
                    <div class="appearance-mock-card muted">
                      <div class="appearance-mock-line" style={{ width: "46%" }} />
                      <div class="appearance-mock-line short" style={{ width: "78%" }} />
                    </div>
                  </div>
                </div>
                <div class="appearance-preview-caption">
                  Background <span class="mono" style={{ "font-size": "11px" }}>{dark() ? "#0a0a0a" : "#fafafa"}</span>
                  {" · "} Surface <span class="mono" style={{ "font-size": "11px" }}>{dark() ? "#111" : "#fff"}</span>
                </div>
              </div>
            </div>

            <div class="hint" style={{ "font-size": "12px", "line-height": "1.5" }}>
              Tip: use <span class="kbd">System</span> to automatically follow your OS dark mode. Changes apply instantly and are saved locally on this browser.
            </div>
          </div>
        </div>
      </section>
    </SettingsLayout>
  );
}
