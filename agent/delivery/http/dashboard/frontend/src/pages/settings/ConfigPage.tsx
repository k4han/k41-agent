import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw, Save } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { formatValue } from "@/lib/utils";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  SourcesTab,
  useSettingsData,
} from "./shared";

export function ConfigPage() {
  const { data, error, drafts, load, pendingChanges, setDraft, resetDraft, saveChanges } =
    useSettingsData("/dashboard-api/config");

  const [tab, setTab] = createSignal<"effective" | "sources">("effective");
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
            <div class="tabs">
              <button class={`tab ${tab() === "effective" ? "active" : ""}`} type="button" onClick={() => setTab("effective")}>
                Effective Values
              </button>
              <button class={`tab ${tab() === "sources" ? "active" : ""}`} type="button" onClick={() => setTab("sources")}>
                By Source
              </button>
            </div>

            <Show when={tab() === "effective"}>
              <div class="stack">
                <input
                  class="input"
                  type="search"
                  placeholder="Search settings..."
                  value={search()}
                  onInput={(event) => setSearch(event.currentTarget.value)}
                />
                <For each={filteredCategories()}>
                  {(group) => (
                    <section class="stack">
                      <div class="row-wrap">
                        <strong>{group.category}</strong>
                        <span class="badge">{group.settings.length}</span>
                      </div>
                      <For each={group.settings}>
                        {([key, info]) => (
                          <SettingRow
                            settingKey={key}
                            info={info}
                            draft={drafts()[key]}
                            dirty={pendingChanges().some((change) => change.key === key)}
                            onChange={(value) => setDraft(key, value)}
                            onReset={() => resetDraft(key)}
                          />
                        )}
                      </For>
                    </section>
                  )}
                </For>
              </div>
            </Show>

            <Show when={tab() === "sources"}>
              <SourcesTab sources={payload.settings_sources} />
            </Show>

            <ConfirmDialog
              open={confirmOpen()}
              changes={pendingChanges()}
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
        <p>You are about to update {props.changes.length} setting(s).</p>
        <For each={props.changes}>
          {(change) => (
            <div class="panel">
              <div class="panel-body">
                <div class="mono">{change.key}</div>
                <div class="hint">Current: {formatValue(change.oldValue)}</div>
                <div>New: {formatValue(change.newValue)}</div>
              </div>
            </div>
          )}
        </For>
      </div>
    </Dialog>
  );
}
