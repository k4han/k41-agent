import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  BrainCircuit,
  Layers,
  RotateCcw,
  Route,
  Save,
  SearchX,
  ShieldCheck,
  Workflow,
  Wrench,
} from "lucide-solid";

import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import type { SettingInfo } from "@/types";

import { SettingsLayout as BaseSettingsLayout } from "./SettingsLayout";
import { ProviderSettingsLayout } from "./ProviderSettingsLayout";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import {
  type PendingChange,
  SettingRow,
  SettingsConfirmDialog,
  SettingsPendingBar,
  SettingsSection,
  useSettingsData,
} from "./shared";

const DOMAIN_EVALUATORS = [
  {
    name: "Agent Routing",
    description: "Fast-path selection of specialist sub-agents based on calibrated choice probabilities.",
    status: "Active (Production)",
    icon: Route,
  },
  {
    name: "Channel Inbound Triage",
    description: "Evaluates inbound chat messages (Telegram, Discord, Zalo) for spam and intent classification.",
    status: "Ready",
    icon: Workflow,
  },
  {
    name: "Tool Pre-Filtering",
    description: "Categorizes user requests to prune candidate tool schemas, reducing LLM token consumption.",
    status: "Ready",
    icon: Wrench,
  },
  {
    name: "Research Loop Termination",
    description: "Determines whether research evidence is sufficient to terminate search iterations.",
    status: "Ready",
    icon: Layers,
  },
  {
    name: "Safety & Guardrails",
    description: "Assesses risk levels and prompt injection likelihood for shell commands and destructive ops.",
    status: "Ready",
    icon: ShieldCheck,
  },
];

export function DecisionsPage(props: { providerOnly?: boolean } = {}) {
  const SettingsLayout = props.providerOnly ? ProviderSettingsLayout : BaseSettingsLayout;
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
  } = useSettingsData("/dashboard-api/decisions");

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => {
    load();
  });

  useUnsavedChanges(() => pendingChanges().length > 0, discardAll);

  const routingSettings = createMemo(() => {
    const payload = data();
    if (!payload?.settings) return [];
    const needle = search().trim().toLowerCase();
    return Object.entries(payload.settings).filter(([key, info]) => {
      if (props.providerOnly || !key.startsWith("decision.router")) return false;
      const haystack = [key, info.label, info.description].join(" ").toLowerCase();
      return !needle || haystack.includes(needle);
    });
  });

  const providerSettings = createMemo(() => {
    const payload = data();
    if (!payload?.settings) return [];
    const needle = search().trim().toLowerCase();
    return Object.entries(payload.settings).filter(([key, info]) => {
      if (!props.providerOnly || key.startsWith("decision.router")) return false;
      const haystack = [key, info.label, info.description].join(" ").toLowerCase();
      return !needle || haystack.includes(needle);
    });
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
      title={props.providerOnly ? "Decision Model Provider" : "Decisions & Routing"}
      breadcrumbLabel="Decisions"
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
              {/* Info Banner */}
              <div
                class="panel"
                style={{
                  padding: "16px 20px",
                  display: "flex",
                  "align-items": "flex-start",
                  gap: "14px",
                  "border-left": "4px solid var(--primary, #3b82f6)",
                  "margin-bottom": "8px",
                }}
              >
                <div style={{ color: "var(--primary, #3b82f6)", "margin-top": "2px" }}>
                  <BrainCircuit size={22} />
                </div>
                <div style={{ display: "flex", "flex-direction": "column", gap: "4px" }}>
                  <div style={{ display: "flex", "align-items": "center", gap: "8px" }}>
                    <span style={{ "font-weight": "650", "font-size": "14px" }}>
                      Cloudflare Clef-flash Decision Engine
                    </span>
                    <span
                      style={{
                        "font-size": "11px",
                        padding: "2px 8px",
                        "border-radius": "999px",
                        background: "rgba(16, 185, 129, 0.12)",
                        color: "#10b981",
                        "font-weight": "600",
                      }}
                    >
                      Fast-path (~100-200ms)
                    </span>
                  </div>
                  <div class="hint" style={{ "font-size": "12px", "line-height": "1.5" }}>
                    Clef-flash evaluates system state against typed schemas (choice, noul, score) returning calibrated
                    probabilities. In <strong>Cascade mode</strong>, queries with confidence above threshold are routed
                    immediately with ultra-low latency and minimal cost ($0.09/M tokens), cascading to standard LLM only when ambiguous.
                  </div>
                </div>
              </div>

              <div class="settings-toolbar-container">
                <div class="settings-toolbar-search-wrap">
                  <SettingsResourceToolbar
                    searchValue={search()}
                    searchPlaceholder="Search decision settings by key, label, or description…"
                    onSearchInput={setSearch}
                  />
                </div>
              </div>
            </div>

            <Show
              when={routingSettings().length > 0 || providerSettings().length > 0}
              fallback={
                <div class="panel" style={{ padding: "36px 20px", "text-align": "center" }}>
                  <div style={{ display: "grid", "place-items": "center", gap: "8px", color: "var(--muted)" }}>
                    <SearchX size={28} />
                    <div style={{ "font-weight": "650", color: "var(--fg)" }}>No decision settings match your search</div>
                    <div class="hint">Try a different term or clear the search.</div>
                    <button class="btn btn-sm" type="button" onClick={() => setSearch("")}>
                      <RotateCcw size={13} /> Clear search
                    </button>
                  </div>
                </div>
              }
            >
              <div class="settings-sections-wrapper">
                {/* Section 1: Router Strategy */}
                <Show when={routingSettings().length > 0}>
                  <SettingsSection
                    title="Agent Routing Strategy"
                    description="Configure how user requests are analyzed and routed to candidate agents."
                  >
                    <div class="settings-list settings-table">
                      <For each={routingSettings() as [string, SettingInfo][]}>
                        {([key, info]) => (
                          <SettingRow
                            settingKey={key}
                            info={info}
                            draft={drafts()[key]}
                            dirty={pendingChanges().some((change: PendingChange) => change.key === key)}
                            onChange={(value) => setDraft(key, value)}
                            onRestore={() => restoreDraft(key)}
                          />
                        )}
                      </For>
                    </div>
                  </SettingsSection>
                </Show>

                {/* Section 2: Cloudflare Workers AI Config */}
                <Show when={providerSettings().length > 0}>
                  <SettingsSection
                    title="Cloudflare Workers AI Connection"
                    description="Credentials and request parameters for @cf/cloudflare/clef-flash."
                  >
                    <div class="settings-list settings-table">
                      <For each={providerSettings() as [string, SettingInfo][]}>
                        {([key, info]) => (
                          <SettingRow
                            settingKey={key}
                            info={info}
                            draft={drafts()[key]}
                            dirty={pendingChanges().some((change: PendingChange) => change.key === key)}
                            onChange={(value) => setDraft(key, value)}
                            onRestore={() => restoreDraft(key)}
                          />
                        )}
                      </For>
                    </div>
                  </SettingsSection>
                </Show>

                {/* Section 3: Extensible Domain Evaluators */}
                <Show when={!props.providerOnly}><SettingsSection
                  title="Multi-Domain Decision Evaluators"
                  description="Built-in domain evaluators ready for extensible decision-making across the platform."
                >
                  <div
                    style={{
                      display: "grid",
                      "grid-template-columns": "repeat(auto-fit, minmax(280px, 1fr))",
                      gap: "12px",
                      padding: "12px 16px 16px",
                    }}
                  >
                    <For each={DOMAIN_EVALUATORS}>
                      {(evaluator) => {
                        const IconComponent = evaluator.icon;
                        return (
                          <div
                            class="panel"
                            style={{
                              padding: "12px 14px",
                              display: "flex",
                              "align-items": "flex-start",
                              gap: "10px",
                              background: "var(--panel-bg-subtle, rgba(255, 255, 255, 0.02))",
                            }}
                          >
                            <div
                              style={{
                                padding: "6px",
                                "border-radius": "6px",
                                background: "var(--bg-muted, rgba(127, 127, 127, 0.1))",
                                color: "var(--primary, #3b82f6)",
                              }}
                            >
                              <IconComponent size={16} />
                            </div>
                            <div style={{ display: "flex", "flex-direction": "column", gap: "2px", flex: 1 }}>
                              <div style={{ display: "flex", "align-items": "center", "justify-content": "space-between", gap: "6px" }}>
                                <span style={{ "font-size": "12px", "font-weight": "650" }}>
                                  {evaluator.name}
                                </span>
                                <span
                                  style={{
                                    "font-size": "10px",
                                    padding: "1px 6px",
                                    "border-radius": "4px",
                                    background: "rgba(16, 185, 129, 0.12)",
                                    color: "#10b981",
                                    "font-weight": "600",
                                  }}
                                >
                                  {evaluator.status}
                                </span>
                              </div>
                              <span class="hint" style={{ "font-size": "11px", "line-height": "1.4" }}>
                                {evaluator.description}
                              </span>
                            </div>
                          </div>
                        );
                      }}
                    </For>
                  </div>
                </SettingsSection></Show>
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
