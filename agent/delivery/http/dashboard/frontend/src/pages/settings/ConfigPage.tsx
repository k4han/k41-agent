import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  AlignJustify,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  Rows3,
  Save,
  SearchX,
  TriangleAlert,
} from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { STORAGE_KEYS } from "@/lib/uiConstants";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
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
  const [collapsedMap, setCollapsedMap] = createSignal<Record<string, boolean>>({});
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

  const categoriesWithCounts = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    return Object.entries(payload.by_category).map(([category, settings]) => ({
      id: category,
      label: categoryLabel(category),
      totalCount: Object.keys(settings).length,
    }));
  });

  const totalSettingsCount = createMemo(() =>
    categoriesWithCounts().reduce((acc, c) => acc + c.totalCount, 0),
  );

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    const needle = search().trim().toLowerCase();
    const catFilter = selectedCategory();

    return Object.entries(payload.by_category)
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
      (change) => payload.settings[change.key]?.restart_required === true,
    );
  });

  const areAllCollapsed = createMemo(() => {
    const groups = filteredCategories();
    if (groups.length === 0) return false;
    return groups.every((g) => Boolean(collapsedMap()[g.category]));
  });

  const toggleAllCollapse = () => {
    const nextCollapsed = !areAllCollapsed();
    const updated: Record<string, boolean> = {};
    for (const g of filteredCategories()) {
      updated[g.category] = nextCollapsed;
    }
    setCollapsedMap(updated);
  };

  const toggleSectionCollapse = (cat: string) => {
    setCollapsedMap((prev) => ({
      ...prev,
      [cat]: !prev[cat],
    }));
  };

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
      title="Runtime Configuration"
      breadcrumbLabel="Runtime"
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
                  <Show when={selectedCategory() === "all" && filteredCategories().length > 1}>
                    <button
                      type="button"
                      class="btn btn-sm btn-ghost settings-toolbar-action-btn"
                      onClick={toggleAllCollapse}
                      title={areAllCollapsed() ? "Expand all sections" : "Collapse all sections"}
                    >
                      <Show when={areAllCollapsed()} fallback={<ChevronUp size={13} />}>
                        <ChevronDown size={13} />
                      </Show>
                      <span>{areAllCollapsed() ? "Expand all" : "Collapse all"}</span>
                    </button>
                  </Show>
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

              <Show when={categoriesWithCounts().length > 1}>
                <div class="settings-category-nav" role="tablist" aria-label="Settings Categories">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={selectedCategory() === "all"}
                    class={`settings-category-pill ${selectedCategory() === "all" ? "active" : ""}`}
                    onClick={() => setSelectedCategory("all")}
                  >
                    <span>All</span>
                    <span class="settings-category-pill-badge">{totalSettingsCount()}</span>
                  </button>
                  <For each={categoriesWithCounts()}>
                    {(cat) => (
                      <button
                        type="button"
                        role="tab"
                        aria-selected={selectedCategory() === cat.id}
                        class={`settings-category-pill ${selectedCategory() === cat.id ? "active" : ""}`}
                        onClick={() => setSelectedCategory(cat.id)}
                      >
                        <span>{cat.label}</span>
                        <span class="settings-category-pill-badge">{cat.totalCount}</span>
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
                  {(group) => {
                    const isSingleView = () => selectedCategory() !== "all";
                    const isCollapsed = () => Boolean(collapsedMap()[group.category]);
                    return (
                      <SettingsSection
                        title={categoryLabel(group.category)}
                        count={group.settings.length}
                        collapsible={!isSingleView()}
                        isOpen={isSingleView() || !isCollapsed()}
                        onToggle={() => toggleSectionCollapse(group.category)}
                      >
                        <div class="settings-list settings-table">
                          <For each={group.settings}>
                            {([key, info]) => (
                              <SettingRow
                                settingKey={key}
                                info={info}
                                draft={drafts()[key]}
                                dirty={pendingChanges().some((change) => change.key === key)}
                                density={density()}
                                onChange={(value) => setDraft(key, value)}
                                onRestore={() => restoreDraft(key)}
                              />
                            )}
                          </For>
                        </div>
                      </SettingsSection>
                    );
                  }}
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
