import {
  createEffect,
  createMemo,
  createSignal,
  For,
  JSX,
  Show,
} from "solid-js";
import {
  ChevronDown,
  Save,
  Settings as SettingsIcon,
  TriangleAlert,
} from "lucide-solid";

import { DataGate } from "@/components/State";
import { Dialog } from "@/components/Dialog";
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
  microsandbox: {
    name: "microsandbox",
    title: "Microsandbox",
    summary: "Local microVM workspaces powered by Microsandbox.",
    tagline: "MicroVM sandbox",
    toggleable: true,
    configuredPredicate: () => true,
    sections: [
      {
        id: "runtime",
        title: "Runtime",
        subtitle: "MicroVM defaults for new workspaces",
        fields: [ENABLED_SUFFIX, "default_root", "image"],
      },
      {
        id: "resources",
        title: "Resources",
        subtitle: "CPU and memory allocation",
        fields: ["cpus", "memory"],
        defaultCollapsed: true,
      },
      {
        id: "lifecycle",
        title: "Lifecycle",
        subtitle: "Timeouts and replacement policy",
        fields: [
          "max_duration_seconds",
          "idle_timeout_seconds",
          "start_timeout_seconds",
          "stop_timeout_seconds",
          "replace_existing",
        ],
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
  const [drawerBackend, setDrawerBackend] = createSignal<string | null>(null);
  const [collapsed, setCollapsed] = createSignal<Record<string, boolean>>({});
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

  createEffect(() => {
    const payload = data();
    if (!payload) {
      return;
    }
    setCollapsed((current) => {
      const next = { ...current };
      for (const backend of visibleBackendDefs()) {
        for (const section of backend.sections) {
          const id = `${backend.name}:${section.id}`;
          if (id in next) {
            continue;
          }
          next[id] = section.defaultCollapsed ?? false;
        }
      }
      return next;
    });
  });

  const settingsByBackend = (backend: string): Record<string, SettingInfo> => {
    const payload = data();
    if (!payload) {
      return {};
    }
    const result: Record<string, SettingInfo> = {};
    const prefix = backend === "local" ? "workspace." : `workspace.${backend}.`;
    for (const [key, info] of Object.entries(payload.settings)) {
      if (key.startsWith(prefix) || (backend === "local" && key === "workspace.root")) {
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

  const toggleSection = (backend: string, sectionId: string) => {
    const id = `${backend}:${sectionId}`;
    setCollapsed((current) => ({ ...current, [id]: !current[id] }));
  };

  return (
    <SettingsLayout
      title="Workspace Backends"
      breadcrumbLabel="Backends"
      contentWidth="wide"
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {() => (
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
                    onConfigure={() => setDrawerBackend(backend.name)}
                  />
                );
              }}
            </For>
          </div>
        )}
      </DataGate>

      <Show when={drawerBackend()}>
        {(name) => {
          const def = () => BACKEND_DEFS_BY_NAME[name()] ?? null;
          const backendSettings = () => settingsByBackend(name());
          const pending = () => pendingByBackend()[name()] || [];
          return (
            <Show when={def()}>
              {(currentDef) => (
                <Dialog
                  open={true}
                  title={`${currentDef().title} settings`}
                  wide
                  onClose={() => setDrawerBackend(null)}
                  footer={
                    <div class="row-wrap" style={{ "justify-content": "space-between", width: "100%" }}>
                      <Show
                        when={currentDef().toggleable}
                        fallback={
                          <span class="hint">
                            The local backend is always available.
                          </span>
                        }
                      >
                        <span class="hint">
                          Status: {isEnabled(currentDef()) ? "Enabled" : "Disabled"}
                        </span>
                      </Show>
                      <div class="row-wrap">
                        <button class="btn" type="button" onClick={() => setDrawerBackend(null)}>
                          Close
                        </button>
                        <button
                          class="btn btn-primary"
                          type="button"
                          disabled={pending().length === 0 || busy()[name()] === "save"}
                          onClick={() => void saveBackend(name())}
                        >
                          <Save size={14} />
                          {busy()[name()] === "save"
                            ? "Saving..."
                            : `Save ${pending().length ? `(${pending().length})` : ""}`}
                        </button>
                      </div>
                    </div>
                  }
                >
                  <div class="backend-drawer-sections">
                    <For each={currentDef().sections}>
                      {(section) => {
                        const id = `${name()}:${section.id}`;
                        return (
                          <DrawerSection
                            backend={name()}
                            settings={backendSettings()}
                            section={section}
                            drafts={drafts()}
                            pending={pending()}
                            collapsed={Boolean(collapsed()[id])}
                            onToggle={() => toggleSection(name(), section.id)}
                            onChange={setDraft}
                            onRestore={restoreDraft}
                          />
                        );
                      }}
                    </For>
                  </div>
                </Dialog>
              )}
            </Show>
          );
        }}
      </Show>
    </SettingsLayout>
  );
}

function BackendCard(props: {
  backend: BackendDefinition;
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
    <article class="backend-card">
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
          <div class="backend-card-subtitle">{props.backend.tagline}</div>
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
        <div class="hint">{props.backend.summary}</div>

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
          <label class="backend-toggle-cell">
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
            onClick={props.onConfigure}
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

function DrawerSection(props: {
  backend: string;
  settings: Record<string, SettingInfo>;
  section: DrawerSection;
  drafts: Record<string, unknown>;
  pending: PendingChange[];
  collapsed: boolean;
  onToggle: () => void;
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
    <section class="backend-drawer-section" data-collapsed={props.collapsed}>
      <button
        type="button"
        class="backend-drawer-section-header"
        onClick={props.onToggle}
      >
        <div>
          <div class="backend-drawer-section-title">
            {props.section.title}
            <Show when={dirtyCount() > 0}>
              <span class="badge badge-warning">{dirtyCount()}</span>
            </Show>
          </div>
          <Show when={props.section.subtitle}>
            <div class="backend-drawer-section-subtitle">
              {props.section.subtitle}
            </div>
          </Show>
        </div>
        <span class="backend-drawer-section-caret" aria-hidden="true">
          <ChevronDown size={16} />
        </span>
      </button>
      <div class="backend-drawer-section-body">
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
                showDescription
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
