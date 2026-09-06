import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  AlignJustify,
  Code,
  RotateCcw,
  Rows3,
  Save,
  SearchX,
  TriangleAlert,
} from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { STORAGE_KEYS } from "@/lib/uiConstants";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  type PendingChange,
  RESTART_REQUIRED_NOTICE,
  SettingRow,
  SettingsConfirmDialog,
  SettingsPendingBar,
  SettingsSection,
  useSettingsData,
} from "./shared";

export function ConfigPage() {
  const {
    data,
    error,
    drafts,
    load,
    pendingChanges,
    setDraft,
    restoreDraft,
    discardAll,
    saveChanges,
  } = useSettingsData("/dashboard-api/config");

  const [search, setSearch] = createSignal("");
  const [selectedCategory, setSelectedCategory] = createSignal("all");
  const [density, setDensity] = createSignal<"compact" | "detailed">("compact");
  const [showKeys, setShowKeys] = createSignal(false);
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => {
    load();
    const savedDensity = window.localStorage.getItem(STORAGE_KEYS.SETTINGS_DENSITY);
    if (savedDensity === "detailed" || savedDensity === "compact") {
      setDensity(savedDensity);
    }
  });

  const toggleDensity = () => {
    const next = density() === "compact" ? "detailed" : "compact";
    setDensity(next);
    window.localStorage.setItem(STORAGE_KEYS.SETTINGS_DENSITY, next);
  };

  const categories = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    const byCategory = payload.by_category as Record<string, Record<string, SettingInfo>>;
    return Object.keys(byCategory).map((category) => ({
      id: category,
      label: categoryLabel(category),
    }));
  });

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    const needle = search().trim().toLowerCase();
    const catFilter = selectedCategory();
    const byCategory = payload.by_category as Record<string, Record<string, SettingInfo>>;

    return Object.entries(byCategory)
      .filter(([category]) => catFilter === "all" || category === catFilter)
      .map(([category, settings]) => ({
        category,
        settings: Object.entries(settings).filter(([key, info]) =>
          [key, info.label, info.description, info.category]
            .join(" ")
            .toLowerCase()
            .includes(needle),
        ),
      }))
      .filter((group) => group.settings.length > 0);
  });

  const totalVisible = createMemo(() =>
    filteredCategories().reduce((acc, g) => acc + g.settings.length, 0),
  );

  const pendingRestartChanges = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    return pendingChanges().filter(
      (change: PendingChange) => payload.settings[change.key]?.restart_required === true,
    );
  });

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveChanges(() => setConfirmOpen(false));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsLayout
      title="Server Runtime Configuration"
      breadcrumbLabel="Server Runtime"
      actions={
        <button
          class="btn btn-primary"
          type="button"
          disabled={pendingChanges().length === 0}
          onClick={() => setConfirmOpen(true)}
        >
          <Save size={14} />
          Save {pendingChanges().length ? `(${pendingChanges().length})` : ""}
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            <div class="settings-config-header">
              <div class="settings-toolbar-container">
                <div class="settings-toolbar-search-wrap">
                  <SettingsResourceToolbar
                    searchValue={search()}
                    searchPlaceholder="Search settings by key, name, or description…"
                    onSearchInput={setSearch}
                  />
                </div>
                <div class="settings-toolbar-controls">
                  <button
                    type="button"
                    class={`btn btn-sm ${showKeys() ? "btn-secondary" : "btn-ghost"} settings-toolbar-action-btn`}
                    onClick={() => setShowKeys(!showKeys())}
                    title={showKeys() ? "Hide technical keys" : "Show technical keys"}
                  >
                    <Code size={13} />
                    <span>{showKeys() ? "Hide Keys" : "Show Keys"}</span>
                  </button>
                  <button
                    type="button"
                    class={`btn btn-sm ${density() === "compact" ? "btn-secondary" : "btn-ghost"} settings-toolbar-action-btn`}
                    onClick={toggleDensity}
                    title={
                      density() === "compact"
                        ? "Switch to detailed view (shows descriptions)"
                        : "Switch to compact view"
                    }
                  >
                    <Show when={density() === "compact"} fallback={<Rows3 size={13} />}>
                      <AlignJustify size={13} />
                    </Show>
                    <span>{density() === "compact" ? "Compact" : "Detailed"}</span>
                  </button>
                </div>
              </div>

              <Show when={categories().length > 1}>
                <div class="settings-category-nav" role="tablist" aria-label="Settings Categories">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={selectedCategory() === "all"}
                    class={`settings-category-pill ${selectedCategory() === "all" ? "active" : ""}`}
                    onClick={() => setSelectedCategory("all")}
                  >
                    <span>All</span>
                  </button>
                  <For each={categories()}>
                    {(cat) => (
                      <button
                        type="button"
                        role="tab"
                        aria-selected={selectedCategory() === cat.id}
                        class={`settings-category-pill ${selectedCategory() === cat.id ? "active" : ""}`}
                        onClick={() => setSelectedCategory(cat.id)}
                      >
                        <span>{cat.label}</span>
                      </button>
                    )}
                  </For>
                </div>
              </Show>

              <Show when={search().trim()}>
                <div class="hint settings-search-meta">
                  Found <strong>{totalVisible()}</strong> setting{totalVisible() === 1 ? "" : "s"}
                  {" in "}
                  <strong>{filteredCategories().length}</strong> categor{filteredCategories().length === 1 ? "y" : "ies"}
                  {" for “"}
                  {search().trim()}
                  {"”"}
                </div>
              </Show>

              <Show when={pendingRestartChanges().length > 0}>
                <div class="settings-restart-notice" role="status">
                  <TriangleAlert size={14} />
                  <span>
                    {RESTART_REQUIRED_NOTICE} — {pendingRestartChanges().length} change
                    {pendingRestartChanges().length === 1 ? "" : "s"} need restart.
                  </span>
                </div>
              </Show>
            </div>

            <Show
              when={filteredCategories().length > 0}
              fallback={
                <div class="panel" style={{ padding: "36px 20px", "text-align": "center" }}>
                  <div style={{ display: "grid", "place-items": "center", gap: "8px", color: "var(--muted)" }}>
                    <SearchX size={28} />
                    <div style={{ "font-weight": "650", color: "var(--fg)" }}>No settings match your search</div>
                    <div class="hint">Try a different term or clear the search.</div>
                    <button class="btn btn-sm" type="button" onClick={() => { setSearch(""); setSelectedCategory("all"); }}>
                      <RotateCcw size={13} /> Clear filters
                    </button>
                  </div>
                </div>
              }
            >
              <div class="settings-sections-wrapper">
                <For each={filteredCategories()}>
                  {(group) => (
                    <SettingsSection
                      title={categoryLabel(group.category)}
                    >
                      <div class="settings-list settings-table">
                        <For each={group.settings as [string, SettingInfo][]}>
                          {([key, info]: [string, SettingInfo]) => (
                            <SettingRow
                              settingKey={key}
                              info={info}
                              draft={drafts()[key]}
                              dirty={pendingChanges().some((change: PendingChange) => change.key === key)}
                              density={density()}
                              showKey={showKeys()}
                              onChange={(value) => setDraft(key, value)}
                              onRestore={() => restoreDraft(key)}
                            />
                          )}
                        </For>
                      </div>
                    </SettingsSection>
                  )}
                </For>
              </div>
            </Show>

            <SettingsPendingBar
              count={pendingChanges().length}
              saving={saving()}
              restartRequired={pendingRestartChanges().length > 0}
              onDiscard={discardAll}
              onSave={() => setConfirmOpen(true)}
            />

            <SettingsConfirmDialog
              open={confirmOpen()}
              saving={saving()}
              changes={pendingChanges()}
              settings={payload.settings}
              restartRequired={pendingRestartChanges().length > 0}
              onClose={() => setConfirmOpen(false)}
              onConfirm={handleSave}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
