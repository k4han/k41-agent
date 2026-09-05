import {
  createMemo,
  createSignal,
  For,
  JSX,
  Show,
} from "solid-js";
import { useNavigate, useParams } from "@solidjs/router";
import {
  ArrowLeft,
  Save,
  Settings as SettingsIcon,
  TriangleAlert,
} from "lucide-solid";

import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, putJson } from "@/lib/api";
import { getBackends } from "@/lib/catalogStore";
import { getBackendIcon } from "@/lib/iconRegistry";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import type { BackendCatalogItem, SettingInfo, SettingsPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  sameValue,
  typedValue,
} from "./shared";

type BackendStatus = "enabled" | "disabled" | "always-on";

type DrawerSection = {
  id: string;
  title: string;
  subtitle?: string;
  fields: string[];
  defaultCollapsed?: boolean;
};

type BackendDefinition = {
  name: string;
  title: string;
  summary: string;
  tagline: string;
  sections: DrawerSection[];
  toggleable: boolean;
  configuredPredicate?: (
    settings: Record<string, SettingInfo>,
    drafts: Record<string, unknown>,
  ) => boolean;
};

const ENABLED_SUFFIX = "enabled";

const BACKEND_DEFS_BY_NAME: Record<string, BackendDefinition> = {
  local: {
    name: "local",
    title: "Local",
    summary: "Workspaces stored on the host machine under a configurable root.",
    tagline: "Filesystem backend",
    toggleable: false,
    sections: [
      {
        id: "workspace",
        title: "Workspace Root",
        subtitle: "Default directory used for local workspaces",
        fields: ["root"],
      },
      {
        id: "github",
        title: "GitHub Workspace Root",
        subtitle: "Repository checkouts managed by GitHub automation",
        fields: ["github.root"],
      },
    ],
  },
  daytona: {
    name: "daytona",
    title: "Daytona",
    summary: "Cloud sandbox workspaces powered by the Daytona platform.",
    tagline: "Sandbox provider",
    toggleable: true,
    configuredPredicate: (settings, drafts) => {
      const key = "workspace.daytona.api_key";
      const value = drafts[key] ?? settings[key]?.value;
      return value !== null && value !== undefined && String(value).length > 0;
    },
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        subtitle: "Daytona API credentials",
        fields: [ENABLED_SUFFIX, "api_key"],
      },
      {
        id: "defaults",
        title: "Defaults",
        subtitle: "Sandbox defaults for new workspaces",
        fields: ["default_root"],
        defaultCollapsed: true,
      },
      {
        id: "sandbox",
        title: "Sandbox Config",
        subtitle: "Resources, image, and runtime for new sandboxes",
        fields: [
          "target",
          "image",
          "language",
          "cpu",
          "memory",
          "disk",
          "ephemeral",
          "network_block_all",
          "network_allow_list",
        ],
        defaultCollapsed: true,
      },
      {
        id: "lifecycle",
        title: "Lifecycle",
        subtitle: "Auto-stop, archive, and timeout policy",
        fields: [
          "auto_stop_minutes",
          "auto_archive_days",
          "sweeper_interval_seconds",
          "start_timeout_seconds",
          "stop_timeout_seconds",
          "sandbox_auto_stop_minutes",
          "sandbox_auto_archive_minutes",
          "sandbox_auto_delete_minutes",
        ],
        defaultCollapsed: true,
      },
    ],
  },
  modal: {
    name: "modal",
    title: "Modal",
    summary: "Serverless sandbox workspaces powered by Modal sandboxes.",
    tagline: "Sandbox provider",
    toggleable: true,
    configuredPredicate: (settings, drafts) => {
      const id = drafts["workspace.modal.token_id"] ?? settings["workspace.modal.token_id"]?.value;
      const secret =
        drafts["workspace.modal.token_secret"] ??
        settings["workspace.modal.token_secret"]?.value;
      const hasExplicit = String(id ?? "").length > 0 && String(secret ?? "").length > 0;
      return hasExplicit;
    },
    sections: [
      {
        id: "authentication",
        title: "Authentication",
        subtitle: "Modal token credentials (leave empty to use SDK defaults)",
        fields: [ENABLED_SUFFIX, "token_id", "token_secret"],
      },
      {
        id: "defaults",
        title: "Defaults",
        subtitle: "Sandbox defaults for new workspaces",
        fields: ["app_name", "default_root", "image"],
        defaultCollapsed: true,
      },
      {
        id: "lifecycle",
        title: "Lifecycle",
        subtitle: "Sandbox and idle timeout policy",
        fields: ["sandbox_timeout_seconds", "idle_timeout_seconds"],
        defaultCollapsed: true,
      },
    ],
  },
};

function settingKey(backend: string, suffix: string): string {
  if (backend === "local") {
    return suffix === ENABLED_SUFFIX ? "workspace.local.enabled" : `workspace.${suffix}`;
  }
  return `workspace.${backend}.${suffix}`;
}

function resolveBackendDef(entry: BackendCatalogItem): BackendDefinition | null {
  const def = BACKEND_DEFS_BY_NAME[entry.name];
  if (!def) {
    // Surface a console warning so missing UI metadata does not silently
    // hide a backend that the server has just started exposing. The full
    // fix is to add a BackendDefinition entry in ``BACKEND_DEFS_BY_NAME``
    // (sections, fields, configuredPredicate, etc.).
    console.warn(
      `[BackendsPage] No UI definition for backend "${entry.name}". ` +
        `Add an entry to BACKEND_DEFS_BY_NAME to expose it in the dashboard.`,
    );
  }
  return def ?? null;
}

function visibleBackendDefs(): BackendDefinition[] {
  return getBackends()
    .map((entry) => resolveBackendDef(entry))
    .filter((def): def is BackendDefinition => def !== null);
}

export function BackendsPage() {
  const { showToast } = useToast();
  const [data, setData] = createSignal<SettingsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [selectedBackend, setSelectedBackend] = createSignal<string>("daytona");
  const [busy, setBusy] = createSignal<Record<string, string>>({});

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<SettingsPayload>("/dashboard-api/backends");
      setData(payload);
      setDrafts(
        Object.fromEntries(
          Object.entries(payload.settings).map(([key, info]) => [key, info.value]),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load backends");
    }
  };

  useCatalogAndLoad(load);

  const settingsByBackend = (backend: string): Record<string, SettingInfo> => {
    const payload = data();
    if (!payload) {
      return {};
    }
    const result: Record<string, SettingInfo> = {};
    const localKeys = new Set(["workspace.root", "workspace.github.root"]);
    const prefix = `workspace.${backend}.`;
    for (const [key, info] of Object.entries(payload.settings)) {
      if (backend === "local" ? localKeys.has(key) : key.startsWith(prefix)) {
        result[key] = info;
      }
    }
    return result;
  };

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const restoreDraft = (key: string) => {
    const payload = data();
    const info = payload?.settings[key];
    if (!info) {
      showToast("No server value to restore.", "warning");
      return;
    }
    setDraft(key, info.value ?? null);
    showToast("Change reverted.", "warning");
  };

  const draftValueFor = (backend: string, suffix: string): unknown => {
    const key = settingKey(backend, suffix);
    const current = drafts();
    if (Object.prototype.hasOwnProperty.call(current, key)) {
      return current[key];
    }
    return data()?.settings[key]?.value ?? null;
  };

  const isEnabled = (backend: BackendDefinition): boolean => {
    if (!backend.toggleable) {
      return true;
    }
    return Boolean(draftValueFor(backend.name, ENABLED_SUFFIX));
  };

  const backendStatus = (backend: BackendDefinition): BackendStatus => {
    if (!backend.toggleable) {
      return "always-on";
    }
    return isEnabled(backend) ? "enabled" : "disabled";
  };

  const isBackendConfigured = (backend: BackendDefinition): boolean => {
    const settings = settingsByBackend(backend.name);
    if (backend.configuredPredicate) {
      return backend.configuredPredicate(settings, drafts());
    }
    if (backend.toggleable) {
      return isEnabled(backend);
    }
    return Object.values(settings).some((info) => {
      const value = info?.value;
      return value !== null && value !== "" && value !== undefined;
    });
  };

  const changesForBackend = (backend: string): PendingChange[] => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const settings = settingsByBackend(backend);
    return Object.entries(settings)
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  };

  const pendingByBackend = createMemo<Record<string, PendingChange[]>>(() => {
    return Object.fromEntries(
      visibleBackendDefs().map((def) => [def.name, changesForBackend(def.name)]),
    );
  });

  const setBusyState = (backend: string, action: string | null) => {
    setBusy((current) => {
      const next = { ...current };
      if (action) {
        next[backend] = action;
      } else {
        delete next[backend];
      }
      return next;
    });
  };

  const toggleEnabled = async (backend: BackendDefinition, next: boolean) => {
    const key = settingKey(backend.name, ENABLED_SUFFIX);
    setDraft(key, next);
    setBusyState(backend.name, "toggle");
    try {
      await putJson("/settings", { values: { [key]: next } });
      showToast(`${backend.title} ${next ? "enabled" : "disabled"}.`);
      await load();
    } catch (err) {
      const payload = data();
      const previous = payload?.settings[key]?.value ?? null;
      setDraft(key, previous);
      showToast(
        err instanceof Error ? err.message : "Failed to update setting",
        "error",
      );
    } finally {
      setBusyState(backend.name, null);
    }
  };

  const saveBackend = async (backend: string) => {
    const changes = pendingByBackend()[backend] || [];
    if (!changes.length) {
      return;
    }
    setBusyState(backend, "save");
    try {
      const values = Object.fromEntries(
        changes.map((change) => [change.key, change.newValue]),
      );
      await putJson("/settings", { values });
      showToast(
        `Updated ${changes.length} ${titleOf(backend)} setting(s).`,
      );
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save settings",
        "error",
      );
    } finally {
      setBusyState(backend, null);
    }
  };

  const params = useParams<{ backendName?: string }>();
  const navigate = useNavigate();

  return (
    <Show
      when={params.backendName}
      fallback={
        <SettingsLayout
          title="Workspace Backends"
          breadcrumbLabel="Backends"
          contentWidth="wide"
        >
          <DataGate data={data()} error={error()} onRetry={load}>
            {() => (
              <div class="stack" style={{ gap: "24px" }}>
                <div>
                  <div class="settings-section-title" style={{ "margin-bottom": "12px" }}>
                    Select Backend
                  </div>
                  <div class="backends-grid">
                    <For each={getBackends()}>
                      {(entry) => {
                        const backend = resolveBackendDef(entry);
                        if (!backend) {
                          return null;
                        }
                        return (
                          <BackendCard
                            backend={backend}
                            status={backendStatus(backend)}
                            configured={isBackendConfigured(backend)}
                            pendingCount={(pendingByBackend()[backend.name] || []).length}
                            busy={busy()[backend.name] || null}
                            onToggle={(value) => void toggleEnabled(backend, value)}
                            onConfigure={() => navigate(`/settings/backends/${backend.name}`)}
                          />
                        );
                      }}
                    </For>
                  </div>
                </div>
              </div>
            )}
          </DataGate>
        </SettingsLayout>
      }
    >
      {(backendName) => {
        const def = () =>
          BACKEND_DEFS_BY_NAME[backendName()] ??
          visibleBackendDefs().find((b) => b.name === backendName()) ??
          null;
        const name = () => backendName();
        const backendSettings = () => settingsByBackend(name());
        const pending = () => pendingByBackend()[name()] || [];

        return (
          <SettingsLayout
            title={def() ? `${def()!.title} Settings` : "Backend Settings"}
            description={def()?.summary}
            breadcrumbSegments={[
              { label: "Backends", href: "/settings/backends" },
              { label: def()?.title || name() },
            ]}
            contentWidth="wide"
            actions={
              <div class="row-wrap" style={{ gap: "8px" }}>
                <button
                  class="btn btn-sm"
                  type="button"
                  onClick={() => navigate("/settings/backends")}
                >
                  <ArrowLeft size={14} />
                  Back to Backends
                </button>
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  disabled={pending().length === 0 || busy()[name()] === "save"}
                  onClick={() => void saveBackend(name())}
                >
                  <Save size={14} />
                  {busy()[name()] === "save"
                    ? "Saving..."
                    : `Save changes ${pending().length ? `(${pending().length})` : ""}`}
                </button>
              </div>
            }
          >
            <DataGate data={data()} error={error()} onRetry={load}>
              {() => (
                <Show
                  when={def()}
                  fallback={
                    <div class="panel empty" style={{ padding: "32px", "text-align": "center" }}>
                      <h3>Backend not found</h3>
                      <button
                        class="btn"
                        style={{ "margin-top": "12px" }}
                        type="button"
                        onClick={() => navigate("/settings/backends")}
                      >
                        <ArrowLeft size={14} />
                        Back to Backends
                      </button>
                    </div>
                  }
                >
                  <div class="stack" style={{ gap: "20px" }}>
                    {/* Header overview card */}
                    <div class="backend-config-card">
                      <div class="backend-config-header" style={{ "border-bottom": "none", padding: "0" }}>
                        <div class="backend-config-header-left">
                          <div
                            class="backend-brand-icon"
                            data-brand={name()}
                            aria-hidden="true"
                          >
                            {getBackendIcon(name())()}
                          </div>
                          <div>
                            <h2 class="backend-config-title">{def()!.title}</h2>
                            <p class="hint backend-config-subtitle">{def()!.tagline} &mdash; {def()!.summary}</p>
                          </div>
                        </div>
                        <div class="row-wrap backend-config-header-actions">
                          <Show
                            when={def()!.toggleable}
                            fallback={
                              <span class="badge badge-success">
                                Always available
                              </span>
                            }
                          >
                            <label class="backend-toggle-cell">
                              <button
                                class={`toggle-control ${isEnabled(def()!) ? "active" : ""}`}
                                type="button"
                                role="switch"
                                aria-checked={isEnabled(def()!)}
                                disabled={busy()[name()] === "toggle"}
                                onClick={() => void toggleEnabled(def()!, !isEnabled(def()!))}
                              >
                                <span class="toggle-track">
                                  <span class="toggle-thumb" />
                                </span>
                                <span class="toggle-label">
                                  {isEnabled(def()!) ? "Enabled" : "Disabled"}
                                </span>
                              </button>
                            </label>
                          </Show>
                        </div>
                      </div>
                    </div>

                    {/* Section groups */}
                    <div class="backend-sections-list">
                      <For each={def()!.sections}>
                        {(section) => (
                          <BackendSectionGroup
                            backend={name()}
                            settings={backendSettings()}
                            section={section}
                            drafts={drafts()}
                            pending={pending()}
                            onChange={setDraft}
                            onRestore={restoreDraft}
                          />
                        )}
                      </For>
                    </div>
                  </div>
                </Show>
              )}
            </DataGate>
          </SettingsLayout>
        );
      }}
    </Show>
  );
}

function BackendCard(props: {
  backend: BackendDefinition;
  active?: boolean;
  status: BackendStatus;
  configured: boolean;
  pendingCount: number;
  busy: string | null;
  onToggle: (value: boolean) => void;
  onConfigure: () => void;
}) {
  const statusLabel = () => {
    switch (props.status) {
      case "enabled":
        return "Enabled";
      case "disabled":
        return "Disabled";
      case "always-on":
        return "Always on";
    }
  };

  const enableHint = () => {
    if (props.backend.toggleable) {
      return props.status === "enabled" ? "Enabled" : "Disabled";
    }
    return "Always available";
  };

  return (
    <article
      class={`backend-card ${props.active ? "backend-card--active" : ""}`}
      onClick={props.onConfigure}
      style={{ cursor: "pointer" }}
    >
      <header class="backend-card-header">
        <div
          class="backend-brand-icon"
          data-brand={props.backend.name}
          aria-hidden="true"
        >
          {getBackendIcon(props.backend.name)()}
        </div>
        <div class="backend-card-title">
          <div class="backend-card-name">{props.backend.title}</div>
        </div>
        <span
          class="backend-status-pill"
          data-state={props.status}
          title={statusLabel()}
        >
          <span class="backend-status-dot" />
          {statusLabel()}
        </span>
      </header>

      <div class="backend-card-body">
        <Show
          when={
            !props.configured &&
            props.backend.toggleable &&
            props.status === "disabled"
          }
        >
          <div class="backend-card-empty-hint">
            <TriangleAlert size={14} />
            <div>
              Add credentials in <strong>Configure</strong> to enable this backend.
            </div>
          </div>
        </Show>

        <div class="backend-card-meta">
          <div class="backend-card-meta-row">
            <span class="backend-card-meta-label">Pending</span>
            <span class={`mono ${props.pendingCount ? "" : "muted"}`}>
              {props.pendingCount}
            </span>
          </div>
        </div>
      </div>

      <footer class="backend-card-footer">
        <Show
          when={props.backend.toggleable}
          fallback={
            <span class="backend-toggle-cell">
              <span>{enableHint()}</span>
            </span>
          }
        >
          <label class="backend-toggle-cell" onClick={(e) => e.stopPropagation()}>
            <button
              class={`toggle-control ${props.status === "enabled" ? "active" : ""}`}
              type="button"
              role="switch"
              aria-checked={props.status === "enabled"}
              disabled={props.busy === "toggle"}
              onClick={() => props.onToggle(props.status !== "enabled")}
            >
              <span class="toggle-track">
                <span class="toggle-thumb" />
              </span>
              <span class="toggle-label">Enabled</span>
            </button>
            <span>{enableHint()}</span>
          </label>
        </Show>

        <div class="backend-card-actions">
          <button
            class="btn btn-sm btn-primary"
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              props.onConfigure();
            }}
          >
            <SettingsIcon size={13} />
            Configure
            <Show when={props.pendingCount > 0}>
              <span class="badge badge-warning" style={{ "margin-left": "4px" }}>
                {props.pendingCount}
              </span>
            </Show>
          </button>
        </div>
      </footer>
    </article>
  );
}

function BackendSectionGroup(props: {
  backend: string;
  settings: Record<string, SettingInfo>;
  section: DrawerSection;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  onChange: (key: string, value: unknown) => void;
  onRestore: (key: string) => void;
}) {
  const fieldEntries = createMemo(() => {
    return props.section.fields
      .map((suffix) => {
        const key = settingKey(props.backend, suffix);
        const info = props.settings[key];
        return info ? { key, info } : null;
      })
      .filter((entry): entry is { key: string; info: SettingInfo } => entry !== null);
  });

  const dirtyCount = createMemo(() => {
    const keys = new Set(fieldEntries().map((entry) => entry.key));
    return props.pending.filter((change) => keys.has(change.key)).length;
  });

  return (
    <section class="backend-section-group">
      <div class="backend-section-header">
        <div>
          <div class="backend-section-title">
            {props.section.title}
            <Show when={dirtyCount() > 0}>
              <span class="badge badge-warning">{dirtyCount()}</span>
            </Show>
          </div>
          <Show when={props.section.subtitle}>
            <div class="hint backend-section-subtitle">{props.section.subtitle}</div>
          </Show>
        </div>
      </div>
      <div class="backend-section-body">
        <For
          each={fieldEntries()}
          fallback={
            <div class="empty" style={{ padding: "18px" }}>
              No fields available.
            </div>
          }
        >
          {(entry) => {
            const dirty = () =>
              props.pending.some((change) => change.key === entry.key);
            return (
              <SettingRow
                settingKey={entry.key}
                info={entry.info}
                draft={props.drafts[entry.key]}
                dirty={dirty()}
                onChange={(value) => props.onChange(entry.key, value)}
                onRestore={() => props.onRestore(entry.key)}
              />
            );
          }}
        </For>
      </div>
    </section>
  );
}

function titleOf(backend: string): string {
  const fromCatalog = getBackends().find((entry) => entry.name === backend)?.title;
  if (fromCatalog) {
    return fromCatalog;
  }
  return BACKEND_DEFS_BY_NAME[backend]?.title ?? backend;
}
