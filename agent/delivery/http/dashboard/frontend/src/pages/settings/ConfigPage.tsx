import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw, Save, Search } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  categoryLabel,
  ChangesPreview,
  type PendingChange,
  SettingRow,
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

  onMount(load);

  return (
    <SettingsLayout
      title="Runtime Configuration"
      subtitle="Manage channels, database, and security settings."
      actions={
        <>
          <button class="btn" type="button" onClick={load}>
            <RefreshCw size={14} />
            Reload
          </button>
          <button
            class="btn btn-primary"
            type="button"
            disabled={pendingChanges().length === 0}
            onClick={() => setConfirmOpen(true)}
          >
            <Save size={14} />
            Save Changes {pendingChanges().length ? `(${pendingChanges().length})` : ""}
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <div class="stack">
              <div class="settings-toolbar">
                <div class="settings-search">
                  <Search size={15} />
                  <input
                    class="input"
                    type="search"
                    placeholder="Search settings..."
                    value={search()}
                    onInput={(event) => setSearch(event.currentTarget.value)}
                  />
                </div>
              </div>
              <For each={filteredCategories()}>
                {(group) => (
                  <section class="settings-group">
                    <div class="settings-section-header">
                      <div>
                        <div class="settings-section-title">{categoryLabel(group.category)}</div>
                        <div class="hint">{group.settings.length} setting{group.settings.length === 1 ? "" : "s"}</div>
                      </div>
                    </div>
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
                  </section>
                )}
              </For>
              <Show when={filteredCategories().length === 0}>
                <div class="empty">No settings found.</div>
              </Show>
            </div>

            <ConfirmDialog
              open={confirmOpen()}
              changes={pendingChanges()}
              settings={payload.settings}
              onClose={() => setConfirmOpen(false)}
              onConfirm={() => saveChanges(() => setConfirmOpen(false))}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function ConfirmDialog(props: {
  open: boolean;
  changes: PendingChange[];
  settings: Record<string, SettingInfo>;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={props.open}
      title="Confirm Changes"
      onClose={props.onClose}
      footer={
        <>
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button class="btn btn-primary" type="button" onClick={props.onConfirm}>
            Confirm Save
          </button>
        </>
      }
    >
      <div class="stack">
        <p>You are about to update {props.changes.length} setting{props.changes.length === 1 ? "" : "s"}.</p>
        <ChangesPreview changes={props.changes} settings={props.settings} />
      </div>
    </Dialog>
  );
}
