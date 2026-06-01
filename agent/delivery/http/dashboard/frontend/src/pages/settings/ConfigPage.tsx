import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Save, TriangleAlert } from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  RESTART_REQUIRED_NOTICE,
  SettingRow,
  SettingsSection,
  SettingsConfirmDialog,
  useSettingsData,
} from "./shared";

export function ConfigPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, restoreDraft, saveChanges } =
    useSettingsData("/dashboard-api/config");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);

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

  const pendingRestartChanges = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return pendingChanges().filter(
      (change) => payload.settings[change.key]?.restart_required === true,
    );
  });

  onMount(load);

  return (
    <SettingsLayout
      title="Runtime Configuration"
      subtitle="Manage database, security, and general runtime settings."
      breadcrumbLabel="Runtime"
      actions={
        <button
          class="btn btn-primary"
          type="button"
          disabled={pendingChanges().length === 0}
          onClick={() => setConfirmOpen(true)}
        >
          <Save size={14} />
          Save Changes {pendingChanges().length ? `(${pendingChanges().length})` : ""}
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="stack">
              <SettingsResourceToolbar
                searchValue={search()}
                searchPlaceholder="Search settings..."
                onSearchInput={setSearch}
              />
              <Show when={pendingRestartChanges().length > 0}>
                <div class="settings-restart-notice" role="status">
                  <TriangleAlert size={14} />
                  <span>{RESTART_REQUIRED_NOTICE}</span>
                </div>
              </Show>
              <For each={filteredCategories()}>
                {(group) => (
                  <SettingsSection
                    title={categoryLabel(group.category)}
                    description={`${group.settings.length} setting${group.settings.length === 1 ? "" : "s"}`}
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
              <Show when={filteredCategories().length === 0}>
                <div class="empty">No settings found.</div>
              </Show>
            </div>

            <SettingsConfirmDialog
              open={confirmOpen()}
              changes={pendingChanges()}
              settings={payload.settings}
              restartRequired={pendingRestartChanges().length > 0}
              onClose={() => setConfirmOpen(false)}
              onConfirm={() => saveChanges(() => setConfirmOpen(false))}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
