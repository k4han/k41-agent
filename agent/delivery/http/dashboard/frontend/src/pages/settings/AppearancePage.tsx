import { createSignal, onCleanup, onMount, Show, For } from "solid-js";
import { Check, Clock, Eye, MessageSquare, Monitor, Moon, Sun } from "lucide-solid";

import { useToast } from "@/components/Toast";
import { apiFetch, putJson } from "@/lib/api";
import { STORAGE_KEYS, THEME_OPTIONS } from "@/lib/uiConstants";
import type { SettingsPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type ThemeMode = "light" | "dark" | "system";
export function AppearancePage() {
  const [dark, setDark] = createSignal(false);
  const [mode, setMode] = createSignal<ThemeMode>("system");
  const [systemDark, setSystemDark] = createSignal(false);

  // Server settings for display & chat
  const [timezone, setTimezone] = createSignal("UTC");
  const [timezoneOptions, setTimezoneOptions] = createSignal<string[]>(["UTC"]);
  const [streamThinking, setStreamThinking] = createSignal(false);
  const [loadingConfig, setLoadingConfig] = createSignal(false);
  const [savingTimezone, setSavingTimezone] = createSignal(false);
  const [savingThinking, setSavingThinking] = createSignal(false);

  const { showToast } = useToast();

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

  const loadServerSettings = async () => {
    setLoadingConfig(true);
    try {
      const payload = await apiFetch<SettingsPayload>("/dashboard-api/config");
      if (payload?.settings) {
        const tzInfo = payload.settings["display.timezone"];
        if (tzInfo) {
          setTimezone(String(tzInfo.value || "UTC"));
          if (Array.isArray(tzInfo.options) && tzInfo.options.length > 0) {
            setTimezoneOptions(tzInfo.options.map(String));
          }
        }
        const thinkingInfo = payload.settings["chat.stream_thinking"];
        if (thinkingInfo) {
          setStreamThinking(Boolean(thinkingInfo.value));
        }
      }
    } catch {
      // Non-blocking error for server settings
    } finally {
      setLoadingConfig(false);
    }
  };

  onMount(() => {
    syncFromStorage();
    loadServerSettings();

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

  const handleTimezoneChange = async (newTz: string) => {
    setTimezone(newTz);
    setSavingTimezone(true);
    try {
      await putJson("/settings", {
        values: { "display.timezone": newTz },
      });
      showToast("Display timezone updated.", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to update timezone", "error");
    } finally {
      setSavingTimezone(false);
    }
  };

  const handleToggleStreamThinking = async () => {
    const next = !streamThinking();
    setStreamThinking(next);
    setSavingThinking(true);
    try {
      await putJson("/settings", {
        values: { "chat.stream_thinking": next },
      });
      showToast(`Stream thinking ${next ? "enabled" : "disabled"}.`, "success");
    } catch (err) {
      setStreamThinking(!next);
      showToast(err instanceof Error ? err.message : "Failed to update stream thinking", "error");
    } finally {
      setSavingThinking(false);
    }
  };

  const resolvedLabel = () => {
    if (mode() === "system") return systemDark() ? "Dark (system)" : "Light (system)";
    return mode() === "dark" ? "Dark" : "Light";
  };

  return (
    <SettingsLayout
      title="Appearance & Preferences"
      breadcrumbLabel="Appearance"
      contentWidth="narrow"
    >
      <div class="stack" style={{ gap: "24px" }}>
        {/* Theme section */}
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

        {/* Display & Localization */}
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title row">
              <Clock size={14} />
              Display & Localization
            </div>
            <span class="hint">Dashboard timestamp rendering</span>
          </div>
          <div class="panel-body">
            <div class="stack" style={{ gap: "12px" }}>
              <div class="form-field">
                <label class="form-label" for="setting-timezone">
                  Display Timezone
                </label>
                <select
                  id="setting-timezone"
                  class="select"
                  disabled={loadingConfig() || savingTimezone()}
                  value={timezone()}
                  onChange={(e) => handleTimezoneChange(e.currentTarget.value)}
                  style={{ width: "100%" }}
                >
                  <For each={timezoneOptions()}>
                    {(opt) => <option value={opt}>{opt}</option>}
                  </For>
                </select>
                <div class="hint" style={{ "font-size": "12px" }}>
                  Used to format timestamps across session history, logs, and tables.
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Chat Preferences */}
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title row">
              <MessageSquare size={14} />
              Chat Preferences
            </div>
          </div>
          <div class="panel-body">
            <div class="stack" style={{ gap: "16px" }}>
              <div class="row" style={{ "justify-content": "space-between", "align-items": "center" }}>
                <div>
                  <div style={{ "font-weight": "550", "font-size": "13px" }}>Stream Thinking to UI</div>
                  <div class="hint" style={{ "font-size": "12px", "max-width": "380px" }}>
                    Show model reasoning while it streams.
                  </div>
                </div>
                <button
                  class={`toggle-control ${streamThinking() ? "active" : ""}`}
                  type="button"
                  role="switch"
                  aria-checked={streamThinking()}
                  disabled={savingThinking() || loadingConfig()}
                  onClick={handleToggleStreamThinking}
                >
                  <span class="toggle-track">
                    <span class="toggle-thumb" />
                  </span>
                  <span class="toggle-text">{streamThinking() ? "Enabled" : "Disabled"}</span>
                </button>
              </div>
            </div>
          </div>
        </section>

      </div>
    </SettingsLayout>
  );
}
