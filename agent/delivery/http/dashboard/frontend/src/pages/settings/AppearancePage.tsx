import { createSignal, onMount } from "solid-js";
import { Moon, Sun } from "lucide-solid";

import { SettingsLayout } from "./SettingsLayout";

export function AppearancePage() {
  const [dark, setDark] = createSignal(false);

  onMount(() => {
    const stored = window.localStorage.getItem("kaka-dashboard-theme");
    const next =
      stored === "dark" ||
      (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches);
    setDark(next);
  });

  const setTheme = (mode: "light" | "dark" | "system") => {
    if (mode === "system") {
      window.localStorage.removeItem("kaka-dashboard-theme");
      const next = window.matchMedia("(prefers-color-scheme: dark)").matches;
      setDark(next);
      document.documentElement.classList.toggle("dark", next);
    } else {
      const next = mode === "dark";
      setDark(next);
      document.documentElement.classList.toggle("dark", next);
      window.localStorage.setItem("kaka-dashboard-theme", mode);
    }
  };

  const currentMode = () => {
    const stored = window.localStorage.getItem("kaka-dashboard-theme");
    if (!stored) {
      return "system";
    }
    return stored as "light" | "dark";
  };

  return (
    <SettingsLayout title="Appearance" subtitle="Customize how the dashboard looks.">
      <section class="panel" style={{ "max-width": "520px" }}>
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
