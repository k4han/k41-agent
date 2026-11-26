import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import { Bot, Download, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { SettingsLayout } from "./SettingsLayout";

type Action = "install" | "update" | "uninstall";
type Source = "managed" | "google" | "npm" | "pnpm" | "unknown";

type Operation = {
  id: string;
  action: Action;
  state: "running" | "succeeded" | "failed";
  error?: string | null;
};

type AcpAgent = {
  id: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  latest_version: string | null;
  executable_path: string | null;
  management_source: Source | null;
  installation_revision: string;
  actions: Record<Action, boolean>;
  operation: Operation | null;
  docs_url?: string;
  login_command?: string;
  license?: string;
  auth: {
    signed_in: boolean;
    state: "not_installed" | "signed_out" | "signed_in" | "error";
    error?: string;
    plan?: string;
  };
};

const sourceNames: Record<Source, string> = {
  managed: "K41",
  google: "Google installer",
  npm: "npm (global)",
  pnpm: "pnpm (global)",
  unknown: "External installation (PATH)",
};

const runningText: Record<Action, string> = {
  install: "Downloading and installing…",
  update: "Updating…",
  uninstall: "Uninstalling…",
};

const successText: Record<Action, string> = {
  install: "Installation complete.",
  update: "Update complete.",
  uninstall: "Uninstall complete.",
};

export function AcpAgentsPage() {
  const [agents, setAgents] = createStore<{ items: AcpAgent[] | undefined }>({ items: undefined });
  const [loadError, setLoadError] = createSignal("");
  const [refreshing, setRefreshing] = createSignal(false);
  const [submitting, setSubmitting] = createStore<Record<string, boolean>>({});
  const [actionError, setActionError] = createStore<Record<string, string>>({});
  const [removeTarget, setRemoveTarget] = createSignal<AcpAgent>();
  let disposed = false;
  let requestSequence = 0;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let polling = false;

  const load = async (refresh = false, checkUpdates = false) => {
    const sequence = ++requestSequence;
    const query = new URLSearchParams();
    if (refresh) query.set("refresh", "true");
    if (checkUpdates) query.set("check_updates", "true");
    try {
      const payload = await apiFetch<{ agents: AcpAgent[] }>(API_PATHS.acpAgents + (query.size ? `?${query}` : ""));
      if (disposed || sequence !== requestSequence) return;
      setAgents("items", reconcile(payload.agents, { key: "id" }));
      setLoadError("");
      schedulePoll();
    } catch (err) {
      if (!disposed && sequence === requestSequence) setLoadError(err instanceof Error ? err.message : "Could not load ACP agents.");
    }
  };

  const schedulePoll = () => {
    if (disposed || polling || pollTimer !== undefined || !agents.items?.some((agent) => agent.operation?.state === "running")) return;
    pollTimer = setTimeout(async () => {
      pollTimer = undefined;
      polling = true;
      try {
        if (!refreshing()) await load();
      } finally {
        polling = false;
        schedulePoll();
      }
    }, 1500);
  };

  const refresh = async () => {
    setRefreshing(true);
    try { await load(true, true); } finally { if (!disposed) setRefreshing(false); }
  };

  const manage = async (action: Action, agent: AcpAgent) => {
    const id = agent.id;
    if (submitting[id]) return;
    setSubmitting(id, true);
    setActionError(id, "");
    const path = action === "install" ? API_PATHS.acpInstall(id)
      : action === "update" ? API_PATHS.acpUpdate(id) : API_PATHS.acpAgent(id);
    try {
      const { operation } = await apiFetch<{ operation: Operation }>(path, {
        method: action === "uninstall" ? "DELETE" : "POST",
        headers: { "If-Match": `"${agent.installation_revision}"` },
      });
      if (disposed) return;
      requestSequence++;
      setAgents("items", (item) => item.id === id, "operation", operation);
      schedulePoll();
    } catch (err) {
      if (!disposed) {
        setActionError(id, err instanceof Error ? err.message : "Could not manage the ACP agent.");
        void load();
      }
    } finally {
      if (!disposed) setSubmitting(id, false);
    }
  };

  onMount(() => void load(false, true));
  onCleanup(() => {
    disposed = true;
    if (pollTimer !== undefined) clearTimeout(pollTimer);
  });

  return (
    <SettingsLayout
      title="ACP Agents"
      description="Install and manage the agent CLIs that K41 runs on this machine."
      breadcrumbLabel="ACP Agents"
      contentWidth="medium"
    >
      <div class="stack settings-page-stack">
        <Show when={loadError()}><p class="hint" role="alert">{loadError()}</p></Show>
        <Show when={agents.items} fallback={<p class="hint" role="status">{loadError() ? "" : "Checking ACP agents…"}</p>}>
          <For each={agents.items}>{(agent) => (
            <AcpAgentPanel
              agent={agent}
              refreshing={refreshing()}
              submitting={Boolean(submitting[agent.id])}
              error={actionError[agent.id]}
              onRefresh={refresh}
              onManage={(action) => action === "uninstall" ? setRemoveTarget({ ...agent }) : void manage(action, agent)}
            />
          )}</For>
        </Show>
        <Show when={removeTarget()} keyed>{(target) => (
          <ConfirmDialog
            open
            title="Uninstall ACP agent?"
            message={<p>Uninstall <strong>{target.display_name}</strong> at <code>{target.executable_path}</code>?</p>}
            confirmLabel="Uninstall"
            onClose={() => setRemoveTarget(undefined)}
            onConfirm={() => {
              setRemoveTarget(undefined);
              void manage("uninstall", target);
            }}
          />
        )}</Show>
      </div>
    </SettingsLayout>
  );
}

function AcpAgentPanel(props: {
  agent: AcpAgent;
  refreshing: boolean;
  submitting: boolean;
  error?: string;
  onRefresh: () => Promise<void>;
  onManage: (action: Action) => void;
}) {
  const running = () => props.agent.operation?.state === "running";
  const disabled = () => props.refreshing || props.submitting || running();
  const statusText = createMemo(() => {
    const { agent } = props;
    if (!agent.installed) return "Not installed";
    const version = agent.version ?? "unknown";
    if (running()) return "Version " + version;
    if (agent.auth.state === "signed_in") {
      return "Version " + version + ", signed in" + (agent.auth.plan ? " (" + agent.auth.plan + ")" : "");
    }
    if (agent.auth.state === "error") return "Version " + version + ": " + (agent.auth.error || "status unavailable");
    return "Version " + version + ", not signed in";
  });

  return (
    <section class="panel settings-section-card" aria-label={props.agent.display_name}>
      <div class="panel-header split">
        <div class="panel-title"><Bot size={17} /> {props.agent.display_name}</div>
        <button class="btn btn-secondary btn-sm" type="button" disabled={disabled()} onClick={() => void props.onRefresh()}>
          <RefreshCw size={13} class={props.refreshing ? "spinner-animate" : ""} /> Refresh
        </button>
      </div>
      <div class="panel-body stack">
        <p role={props.agent.auth.state === "error" && !running() ? "alert" : "status"}>{statusText()}</p>
        <Show when={props.agent.latest_version}>
          <p class="hint">Latest available: {props.agent.latest_version}</p>
        </Show>
        <Show when={props.agent.management_source}>
          {(source) => <p class="hint">Installation source: {sourceNames[source()]}</p>}
        </Show>
        <Show when={props.agent.executable_path}><p class="hint">Executable: <code>{props.agent.executable_path}</code></p></Show>
        <div class="row-wrap">
          <Show when={props.agent.actions.install}>
            <button class="btn btn-primary btn-sm" type="button" disabled={disabled()} onClick={() => props.onManage("install")}>
              <Download size={14} /> Download and install
            </button>
          </Show>
          <Show when={props.agent.actions.update}>
            <button class="btn btn-secondary btn-sm" type="button" disabled={disabled()} onClick={() => props.onManage("update")}>
              <Download size={14} /> Update
            </button>
          </Show>
          <Show when={props.agent.actions.uninstall}>
            <button class="btn btn-danger btn-sm" type="button" disabled={disabled()} onClick={() => props.onManage("uninstall")}>
              <Trash2 size={14} /> Uninstall
            </button>
          </Show>
        </div>
        <Show when={props.error}><p class="hint" role="alert">{props.error}</p></Show>
        <Show when={props.agent.operation}>
          {(operation) => (
            <p class="hint" role={operation().state === "failed" ? "alert" : "status"}>
              {operation().state === "running" ? runningText[operation().action]
                : operation().state === "succeeded" ? successText[operation().action] : operation().error || "The operation failed."}
            </p>
          )}
        </Show>
        <Show when={props.agent.management_source === "unknown"}>
          <p class="hint">Use the original installer to update or uninstall this CLI.</p>
        </Show>
        <Show when={!props.agent.installed && !props.agent.actions.install}>
          <p class="hint">Use the vendor setup instructions to install this CLI on this machine.</p>
        </Show>
        <Show when={props.agent.installed && !props.agent.auth.signed_in && !running() && props.agent.login_command}>
          <p class="hint">Sign in with <code>{props.agent.login_command}</code>, then refresh.</p>
        </Show>
        <Show when={props.agent.docs_url}>
          <a href={props.agent.docs_url} target="_blank" rel="noopener noreferrer">Vendor setup instructions</a>
        </Show>
        <Show when={props.agent.installed}>
          <p class="hint">
            This agent's tools run on the machine running K41, with the same access as K41 itself. Keep approvals on unless you trust everything the agent reads.
          </p>
        </Show>
      </div>
    </section>
  );
}
