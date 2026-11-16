import { A } from "@solidjs/router";
import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  ArrowRight,
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
import { FormSelect } from "@/components/forms";
import type { DecisionsPayload, SettingInfo } from "@/types";

import { DecisionConnectionSelect } from "./DecisionConnectionSelect";
import { SettingsLayout } from "./SettingsLayout";
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
    status: "Active (Production)",
    icon: Route,
  },
  {
    name: "Channel Inbound Triage",
    status: "Ready",
    icon: Workflow,
  },
  {
    name: "Tool Pre-Filtering",
    status: "Ready",
    icon: Wrench,
  },
  {
    name: "Research Loop Termination",
    status: "Ready",
    icon: Layers,
  },
  {
    name: "Safety & Guardrails",
    status: "Ready",
    icon: ShieldCheck,
  },
];

export function DecisionsPage() {
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
  } = useSettingsData<DecisionsPayload>("/dashboard-api/decisions", { saveEndpoint: "/dashboard-api/decisions" });

  const [search, setSearch] = createSignal("");
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => {
    load();
  });

  useUnsavedChanges(() => pendingChanges().length > 0, discardAll);

  const mode = () => String(drafts()["decision.router.mode"] || "cascade");
  const decisionProviders = () => data()?.decision_providers.providers || [];
  const llmOptions = createMemo(() => {
    const options = data()?.llm_model_options || [];
    const value = String(drafts()["decision.router.llm_model"] || "");
    return [
      { value: "", label: "Use current agent model selection" },
      ...(value && !options.some((option) => option.value === value)
        ? [{ value, label: `${value} (unavailable)`, disabled: true }] : []),
      ...options,
    ];
  });

  const routingSettings = createMemo(() => {
    const payload = data();
    if (!payload?.settings) return [];
    const needle = search().trim().toLowerCase();
    const keys = ["decision.router.mode", "decision.default_provider", "decision.router.llm_model",
      "decision.router.threshold", "decision.router.log_telemetry"];
    return keys.filter((key) => payload.settings[key]).map((key): [string, SettingInfo] => [key, payload.settings[key]]).filter(([key, info]) => {
      if (key === "decision.default_provider" && mode() === "llm_only") return false;
      if (key === "decision.router.llm_model" && mode() === "clef_only") return false;
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
      title="Decisions & Routing"
      breadcrumbLabel="Decisions"
      actions={
        <button
          class="btn btn-primary"
          type="button"
          disabled={saving() || pendingChanges().length === 0}
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
                    <span style={{ "font-weight": "650", "font-size": "var(--font-size-base)" }}>
                      Decision Model Engine
                    </span>
                    <span
                      style={{
                        "font-size": "0.6875rem",
                        padding: "2px 8px",
                        "border-radius": "999px",
                        background: "rgba(16, 185, 129, 0.12)",
                        color: "#10b981",
                        "font-weight": "600",
                      }}
                    >
                      Fast-path routing
                    </span>
                  </div>
                  <div class="hint" style={{ "font-size": "0.75rem", "line-height": "1.5" }}>
                    The selected decision provider evaluates system state against typed schemas (choice, noul, score), returning calibrated
                    probabilities. In <strong>Cascade mode</strong>, queries with confidence above threshold are routed
                    immediately, cascading to the standard LLM when confidence is low or the decision provider fails.
                    <div class="mt-2">
                      <A href="/settings/providers?kind=decision" class="text-primary hover:underline text-xs inline-flex items-center gap-1 font-semibold">
                        Configure Google or Cloudflare in Decision Model Providers <ArrowRight size={12} />
                      </A>
                    </div>
                  </div>
                </div>
              </div>

              <div class="settings-toolbar-container">
                <div class="settings-toolbar-search-wrap">
                  <SettingsResourceToolbar
                    searchValue={search()}
                    searchPlaceholder="Search decision settings…"
                    onSearchInput={setSearch}
                  />
                </div>
              </div>
            </div>

            <Show
              when={routingSettings().length > 0}
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
                            control={key === "decision.default_provider" ? (
                              <div class="stack" data-decision-model-control>
                                <DecisionConnectionSelect
                                  providers={decisionProviders()} value={String(drafts()[key] || "")}
                                  label="Decision Model" placeholder="Legacy / environment fallback"
                                  configuredOnly allowFallback disabled={saving()}
                                  onChange={(value) => setDraft(key, value)}
                                />
                                <p class="hint">Updates the shared Decision default in Providers.</p>
                                <Show when={!decisionProviders().some((provider) => provider.configured)}>
                                  <p class="hint">No configured Decision connections. <A href="/settings/providers?tab=catalog&kind=decision">Configure a Decision provider</A>.</p>
                                </Show>
                              </div>
                            ) : key === "decision.router.llm_model" ? (
                              <div class="stack" data-router-llm-control>
                                <FormSelect
                                  value={String(drafts()[key] || "")} options={llmOptions()}
                                  ariaLabel="Router LLM Model" disabled={saving()}
                                  onChange={(value) => setDraft(key, value)}
                                />
                                <p class="hint">Used for LLM-only routing, shadow, and cascade fallback.</p>
                                <Show when={!data()?.llm_model_options.length}>
                                  <p class="hint">No configured LLM models available. <A href="/settings/providers?kind=llm">Configure Models</A>.</p>
                                </Show>
                              </div>
                            ) : undefined}
                          />
                        )}
                      </For>
                    </div>
                  </SettingsSection>
                </Show>

                {/* Section 3: Extensible Domain Evaluators */}
                <SettingsSection
                  title="Multi-Domain Decision Evaluators"
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
                              background: "var(--surface-2)",
                            }}
                          >
                            <div
                              style={{
                                padding: "6px",
                                "border-radius": "6px",
                                background: "var(--surface-3)",
                                color: "var(--primary, #3b82f6)",
                              }}
                            >
                              <IconComponent size={16} />
                            </div>
                            <div style={{ display: "flex", "flex-direction": "column", gap: "2px", flex: 1 }}>
                              <div style={{ display: "flex", "align-items": "center", "justify-content": "space-between", gap: "6px" }}>
                                <span style={{ "font-size": "0.75rem", "font-weight": "650" }}>
                                  {evaluator.name}
                                </span>
                                <span
                                  style={{
                                    "font-size": "0.625rem",
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
                            </div>
                          </div>
                        );
                      }}
                    </For>
                  </div>
                </SettingsSection>
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
              settings={payload().settings}
              onClose={() => setConfirmOpen(false)}
              onConfirm={handleSave}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
