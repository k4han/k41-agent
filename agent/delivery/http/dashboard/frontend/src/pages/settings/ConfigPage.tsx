import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Save, TriangleAlert, SearchX, RotateCcw } from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  RESTART_REQUIRED_NOTICE,
  SettingRow,
  SettingsSection,
  SettingsConfirmDialog,
  SettingsPendingBar,
  useSettingsData,
} from "./shared";

export function ConfigPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft, discardAll, saveChanges } =
    useSettingsData("/dashboard-api/config");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  const filteredCategories = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = search().trim().toLowerCase();
    return Object.entries(payload.by_category)
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

  const totalVisible = createMemo(() => filteredCategories().reduce((acc, g) => acc + g.settings.length, 0));

  const pendingRestartChanges = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return pendingChanges().filter(
      (change) => payload.settings[change.key]?.restart_required === true,
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

  onMount(load);

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
          <div class="stack">
            <div class="stack">
              <SettingsResourceToolbar
                searchValue={search()}
                searchPlaceholder="Search by key, label or description…"
                onSearchInput={setSearch}
              />
              <Show when={search().trim()}>
                <div class="hint" style={{ "font-size": "12px" }}>
                  Found <strong>{totalVisible()}</strong> setting{totalVisible() === 1 ? "" : "s"} in {filteredCategories().length} categor{filteredCategories().length === 1 ? "y" : "ies"}
                  <Show when={search().trim()}> for “{search().trim()}”</Show>
                </div>
              </Show>
              <Show when={pendingRestartChanges().length > 0}>
                <div class="settings-restart-notice" role="status">
                  <TriangleAlert size={14} />
                  <span>{RESTART_REQUIRED_NOTICE} — {pendingRestartChanges().length} change{pendingRestartChanges().length === 1 ? "" : "s"} need restart.</span>
                </div>
              </Show>
              <Show
                when={filteredCategories().length > 0}
                fallback={
                  <div class="panel" style={{ padding: "32px 20px", "text-align": "center" }}>
                    <div style={{ display: "grid", "place-items": "center", gap: "8px", color: "var(--muted)" }}>
                      <SearchX size={28} />
                      <div style={{ "font-weight": "650", color: "var(--fg)" }}>No settings match your search</div>
                      <div class="hint">Try a different term or clear the search.</div>
                      <button class="btn btn-sm" type="button" onClick={() => setSearch("")}>
                        <RotateCcw size={13} /> Clear search
                      </button>
                    </div>
                  </div>
                }
              >
                <For each={filteredCategories()}>
                  {(group) => (
                    <SettingsSection
                      title={categoryLabel(group.category)}
                      count={group.settings.length}
                      collapsible
                      defaultOpen={filteredCategories().length <= 3 || Boolean(search().trim())}
                    >
                      <div class="settings-list">
                        <For each={group.settings}>
                          {([key, info]) => (
                            <SettingRow
                              settingKey={key}
                              info={info}
                              draft={drafts()[key]}
                              dirty={pendingChanges().some((change) => change.key === key)}
                              onChange={(value) => setDraft(key, value)}
                              onRestore={() => restoreDraft(key)}
                            />
                          )}
                        </For>
                      </div>
                    </SettingsSection>
                  )}
                </For>
              </Show>
            </div>

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
