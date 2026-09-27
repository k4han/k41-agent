import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RotateCcw, Save, SearchX } from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import type { SettingInfo } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  SettingsConfirmDialog,
  SettingsPendingBar,
  SettingsSection,
  useSettingsData,
} from "./shared";

function toolNameFromKey(key: string): string {
  const parts = key.split(".");
  return parts.length >= 3 ? parts[1] : "general";
}

export function ToolsPage() {
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
  } = useSettingsData("/dashboard-api/tools");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => {
    load();
  });

  const isSettingVisible = (
    key: string,
    payload: NonNullable<ReturnType<typeof data>>,
    currentDrafts: Record<string, unknown>,
  ) => {
    const parts = key.split(".");
    if (parts.length < 3 || parts[0] !== "tools") return true;
    const tool = parts[1];
    const fieldName = parts.slice(2).join(".");
    const schema = payload.tool_config_schemas?.[tool];
    if (!schema) return true;
    const field = schema.fields.find((f) => f.name === fieldName);
    if (!field || !field.show_when || Object.keys(field.show_when).length === 0) {
      return true;
    }
    for (const [depField, allowedValues] of Object.entries(field.show_when)) {
      const depKey = `tools.${tool}.${depField}`;
      const depVal =
        currentDrafts[depKey] !== undefined && currentDrafts[depKey] !== null && currentDrafts[depKey] !== ""
          ? currentDrafts[depKey]
          : payload.settings[depKey]?.value ??
            schema.fields.find((f) => f.name === depField)?.default ??
            "";
      if (!allowedValues.includes(String(depVal))) {
        return false;
      }
    }
    return true;
  };

  const toolGroups = createMemo(() => {
    const payload = data();
    if (!payload) return [];
    const needle = search().trim().toLowerCase();
    const currentDrafts = drafts();
    const grouped: Record<string, [string, SettingInfo][]> = {};
    for (const [key, info] of Object.entries(payload.settings)) {
      const tool = toolNameFromKey(key);
      if (!isSettingVisible(key, payload, currentDrafts)) continue;
      const haystack = [key, info.label, info.description, tool].join(" ").toLowerCase();
      if (needle && !haystack.includes(needle)) continue;
      (grouped[tool] ||= []).push([key, info]);
    }
    return Object.entries(grouped)
      .map(([tool, entries]) => {
        const schema = payload.tool_config_schemas?.[tool];
        const fieldIndex = (k: string) => {
          const fn = k.split(".").slice(2).join(".");
          const idx = schema?.fields.findIndex((f) => f.name === fn) ?? -1;
          return idx === -1 ? 999 : idx;
        };
        return {
          tool,
          entries: entries.sort(([a], [b]) => {
            const idxA = fieldIndex(a);
            const idxB = fieldIndex(b);
            if (idxA !== idxB) return idxA - idxB;
            return a.localeCompare(b);
          }),
        };
      })
      .sort((a, b) => a.tool.localeCompare(b.tool));
  });

  const totalVisible = createMemo(() =>
    toolGroups().reduce((acc, group) => acc + group.entries.length, 0),
  );

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
      title="Tool Configuration"
      breadcrumbLabel="Tools"
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
                    searchPlaceholder="Search tools or settings by name, key, or description…"
                    onSearchInput={setSearch}
                  />
                </div>
              </div>
              <div class="hint">
                Global defaults for built-in tools. Per-agent overrides live in Agents →
                Capabilities &amp; Tools. Empty values reset a setting to its schema default.
              </div>
              <Show when={search().trim()}>
                <div class="hint settings-search-meta">
                  Found <strong>{totalVisible()}</strong> setting{totalVisible() === 1 ? "" : "s"}
                  {" in "}
                  <strong>{toolGroups().length}</strong> tool{toolGroups().length === 1 ? "" : "s"}
                  {" for “"}
                  {search().trim()}
                  {"”"}
                </div>
              </Show>
            </div>

            <Show
              when={toolGroups().length > 0}
              fallback={
                <div class="panel" style={{ padding: "36px 20px", "text-align": "center" }}>
                  <div style={{ display: "grid", "place-items": "center", gap: "8px", color: "var(--muted)" }}>
                    <SearchX size={28} />
                    <div style={{ "font-weight": "650", color: "var(--fg)" }}>No tool settings match your search</div>
                    <div class="hint">Try a different term or clear the search.</div>
                    <button class="btn btn-sm" type="button" onClick={() => setSearch("")}>
                      <RotateCcw size={13} /> Clear filters
                    </button>
                  </div>
                </div>
              }
            >
              <div class="settings-sections-wrapper">
                <For each={toolGroups()}>
                  {(group) => (
                    <SettingsSection title={group.tool}>
                      <div class="settings-list settings-table">
                        <For each={group.entries as [string, SettingInfo][]}>
                          {([key, info]: [string, SettingInfo]) => (
                            <SettingRow
                              settingKey={key}
                              info={info}
                              draft={drafts()[key]}
                              dirty={pendingChanges().some((change: PendingChange) => change.key === key)}
                              trimToolPrefix
                              onChange={(value) => setDraft(key, value)}
                              onRestore={() => restoreDraft(key)}
                            />
                          )}
                        </For>
                      </div>
                      <Show when={group.tool === "web_search" && (drafts()["tools.web_search.provider"] ?? payload.settings["tools.web_search.provider"]?.value) === "duckduckgo"}>
                        <div class="hint" style={{ padding: "10px 16px" }}>
                          DuckDuckGo operates without API credentials.
                        </div>
                      </Show>
                      <Show when={group.tool === "web_fetch" && (drafts()["tools.web_fetch.provider"] ?? payload.settings["tools.web_fetch.provider"]?.value) === "local"}>
                        <div class="hint" style={{ padding: "10px 16px" }}>
                          Local fetch operates directly via HTTP without API credentials.
                        </div>
                      </Show>
                      <Show when={group.tool === "web_search" || group.tool === "web_fetch"}>
                        <div class="hint" style={{ padding: "6px 16px 12px", "font-size": "12px", opacity: 0.85 }}>
                          💡 Firecrawl and Tavily credentials configured in either web_search or web_fetch are automatically shared if one is left empty.
                        </div>
                      </Show>
                    </SettingsSection>
                  )}
                </For>
              </div>
            </Show>

            <SettingsPendingBar
              count={pendingChanges().length}
              saving={saving()}
              onDiscard={discardAll}
              onSave={() => setConfirmOpen(true)}
            />

            <SettingsConfirmDialog
              open={confirmOpen()}
              saving={saving()}
              changes={pendingChanges()}
              settings={payload.settings}
              onClose={() => setConfirmOpen(false)}
              onConfirm={handleSave}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
