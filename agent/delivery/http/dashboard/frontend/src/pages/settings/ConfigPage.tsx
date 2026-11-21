import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  Code,
  RefreshCw,
  RotateCcw,
  SearchX,
  Sparkles,
  TriangleAlert,
} from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { checkForUpdates, openUpdateDialog, versionInfo, versionLoading } from "@/lib/versionStore";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  type PendingChange,
  RESTART_REQUIRED_NOTICE,
  SettingRow,
  SettingsConfirmDialog,
  SettingsEmptyState,
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
  const [showKeys, setShowKeys] = createSignal(false);
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => {
    load();
  });

  const runtimeCategories = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    const byCategory = payload.by_category as Record<string, Record<string, SettingInfo>>;
    return Object.entries(byCategory).filter(([category]) => category !== "preferences");
  });

  const categories = createMemo(() =>
    runtimeCategories().map(([category]) => ({
      id: category,
      label: categoryLabel(category),
    })),
  );

  const filteredCategories = createMemo(() => {
    const needle = search().trim().toLowerCase();
    const catFilter = selectedCategory();

    return runtimeCategories()
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
      description="Runtime flags, host, database, and system environment configurations."
      breadcrumbLabel="Server Runtime"
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack settings-page-stack">
            {/* Version and System Update Card */}
            <section class="panel settings-section-card version-settings-card">
              <div class="panel-header split">
                <div class="panel-title">Agent Runtime Version</div>
                <div class="row-wrap">
                  <button
                    type="button"
                    class="btn btn-secondary btn-sm"
                    onClick={() => void checkForUpdates(true)}
                    disabled={versionLoading()}
                  >
                    <RefreshCw size={13} class={versionLoading() ? "spinner-animate" : ""} />
                    <span>Check for updates</span>
                  </button>
                  <button
                    type="button"
                    class={`btn btn-sm ${versionInfo()?.has_update ? "btn-primary" : "btn-secondary"}`}
                    onClick={openUpdateDialog}
                  >
                    <Sparkles size={13} />
                    <span>{versionInfo()?.has_update ? `Update to v${versionInfo()?.latest_version}` : "Version Details"}</span>
                  </button>
                </div>
              </div>
              <div class="panel-body">
                <div class="version-settings-grid">
                  <div class="version-settings-item">
                    <span class="version-settings-label">Current Version</span>
                    <span class="version-settings-val">v{versionInfo()?.current_version || "..."}</span>
                  </div>
                  <div class="version-settings-item">
                    <span class="version-settings-label">Latest Version</span>
                    <span class="version-settings-val">
                      v{versionInfo()?.latest_version || "..."}
                      <Show when={versionInfo()?.has_update}>
                        <span class="hint" style={{ "margin-left": "8px" }}>Update available</span>
                      </Show>
                      <Show when={versionInfo() && !versionInfo()?.has_update}>
                        <span class="hint" style={{ "margin-left": "8px" }}>Up to date</span>
                      </Show>
                    </span>
                  </div>
                  <div class="version-settings-item">
                    <span class="version-settings-label">Installation Type</span>
                    <span class="version-settings-val" style={{ "text-transform": "capitalize" }}>
                      {versionInfo()?.install_type || "..."}
                    </span>
                  </div>
                </div>
              </div>
            </section>

            <div class="settings-config-header">
              <SettingsResourceToolbar
                searchValue={search()}
                searchPlaceholder="Search settings…"
                onSearchInput={setSearch}
                filters={
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
                }
                actions={
                  <button
                    type="button"
                    class={`btn btn-sm ${showKeys() ? "btn-secondary" : "btn-ghost"} settings-toolbar-action-btn`}
                    onClick={() => setShowKeys(!showKeys())}
                    title={showKeys() ? "Hide technical keys" : "Show technical keys"}
                  >
                    <Code size={13} />
                    <span>{showKeys() ? "Hide Keys" : "Show Keys"}</span>
                  </button>
                }
              />

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
                <SettingsEmptyState
                  title="No settings match your search"
                  hint="Try a different term or clear the search."
                  action={
                    <button class="btn btn-sm" type="button" onClick={() => { setSearch(""); setSelectedCategory("all"); }}>
                      <RotateCcw size={13} /> Clear filters
                    </button>
                  }
                />
              }
            >
              <div class="settings-sections-wrapper">
                <For each={filteredCategories()}>
                  {(group) => (
                    <SettingsSection
                      title={categoryLabel(group.category)}
                      count={group.settings.length}
                    >
                      <div class="settings-list settings-table">
                        <For each={group.settings as [string, SettingInfo][]}>
                          {([key, info]: [string, SettingInfo]) => (
                            <SettingRow
                              settingKey={key}
                              info={info}
                              draft={drafts()[key]}
                              dirty={pendingChanges().some((change: PendingChange) => change.key === key)}
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
              settings={payload().settings}
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
