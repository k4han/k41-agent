import { createSignal, onMount } from "solid-js";
import { Moon, Sun } from "lucide-solid";

import { SettingsLayout } from "./SettingsLayout";
import { STORAGE_KEYS, THEME_OPTIONS } from "@/lib/uiConstants";

export function AppearancePage() {
  const [dark, setDark] = createSignal(false);

  onMount(() => {
    const stored = window.localStorage.getItem(STORAGE_KEYS.THEME);
    const next =
      stored === THEME_OPTIONS.DARK ||
      (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches);
    setDark(next);
  });

  const setTheme = (mode: "light" | "dark" | "system") => {
    if (mode === THEME_OPTIONS.SYSTEM) {
      window.localStorage.removeItem(STORAGE_KEYS.THEME);
      const next = window.matchMedia("(prefers-color-scheme: dark)").matches;
      setDark(next);
      document.documentElement.classList.toggle("dark", next);
    } else {
      const next = mode === THEME_OPTIONS.DARK;
      setDark(next);
      document.documentElement.classList.toggle("dark", next);
      window.localStorage.setItem(STORAGE_KEYS.THEME, mode);
    }
  };

  const currentMode = () => {
    const stored = window.localStorage.getItem(STORAGE_KEYS.THEME);
    if (!stored) {
      return THEME_OPTIONS.SYSTEM;
    }
    return stored as "light" | "dark";
  };

  return (
    <SettingsLayout
      title="Appearance"
      contentWidth="narrow"
    >
      <section class="panel">
        <div class="panel-header">
          <div class="panel-title row">
            {dark() ? <Moon size={14} /> : <Sun size={14} />}
            Theme
          </div>
        </div>
        <div class="panel-body">
          <div class="stack">
            <div class="field">
              <label>Color Mode</label>
              <div class="theme-selector">
                <button
                  class={`theme-option ${currentMode() === "light" ? "active" : ""}`}
                  type="button"
                  onClick={() => setTheme("light")}
                >
                  <Sun size={18} />
                  <span>Light</span>
                </button>
                <button
                  class={`theme-option ${currentMode() === "dark" ? "active" : ""}`}
                  type="button"
                  onClick={() => setTheme("dark")}
                >
                  <Moon size={18} />
                  <span>Dark</span>
                </button>
                <button
                  class={`theme-option ${currentMode() === "system" ? "active" : ""}`}
                  type="button"
                  onClick={() => setTheme("system")}
                >
                  <Sun size={18} />
                  <span>System</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </SettingsLayout>
  );
}
