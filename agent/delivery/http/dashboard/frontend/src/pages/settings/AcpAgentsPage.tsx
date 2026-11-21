import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import { Bot, RefreshCw } from "lucide-solid";

import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { SettingsLayout } from "./SettingsLayout";

type AcpAgent = {
  id: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  executable_path: string | null;
  docs_url?: string;
  login_command?: string;
  update_command?: string;
  license?: string;
  auth: {
    signed_in: boolean;
    state: "not_installed" | "signed_out" | "signed_in" | "error";
    error?: string;
    plan?: string;
  };
};

export function AcpAgentsPage() {
  const [agents, setAgents] = createStore<{ items: AcpAgent[] | undefined }>({ items: undefined });
  const [loadError, setLoadError] = createSignal("");
  const [refreshing, setRefreshing] = createSignal(false);
  let disposed = false;

  const load = async (refresh = false) => {
    try {
      const payload = await apiFetch<{ agents: AcpAgent[] }>(API_PATHS.acpAgents + (refresh ? "?refresh=true" : ""));
      if (disposed) return;
      setAgents("items", reconcile(payload.agents, { key: "id" }));
      setLoadError("");
    } catch (err) {
      if (!disposed) setLoadError(err instanceof Error ? err.message : "Could not load ACP agents.");
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    try { await load(true); } finally { if (!disposed) setRefreshing(false); }
  };

  onMount(() => void load());
  onCleanup(() => { disposed = true; });

  return (
    <SettingsLayout
      title="ACP Agents"
      description="Connect external agents over the Agent Client Protocol using your installed CLI and account."
      breadcrumbLabel="ACP Agents"
      contentWidth="medium"
    >
      <div class="stack settings-page-stack">
        <Show when={loadError()}><p class="hint" role="alert">{loadError()}</p></Show>
        <Show when={agents.items} fallback={<Show when={!loadError()}><p class="hint" role="status">Starting the agent to check its status…</p></Show>}>
          <For each={agents.items}>{(agent) => <AcpAgentPanel agent={agent} refreshing={refreshing()} onRefresh={refresh} />}</For>
        </Show>
      </div>
    </SettingsLayout>
  );
}

function AcpAgentPanel(props: {
  agent: AcpAgent;
  refreshing: boolean;
  onRefresh: () => Promise<void>;
}) {
  const statusText = createMemo(() => {
    const { agent } = props;
    if (!agent.installed) return "Not installed";
    const version = agent.version ?? "unknown";
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
        <button class="btn btn-secondary btn-sm" type="button" disabled={props.refreshing} onClick={() => void props.onRefresh()}>
          <RefreshCw size={13} class={props.refreshing ? "spinner-animate" : ""} /> Refresh
        </button>
      </div>
      <div class="panel-body stack">
        <p role={props.agent.auth.state === "error" ? "alert" : "status"}>{statusText()}</p>
        <Show when={props.agent.executable_path}><p class="hint">Executable: <code>{props.agent.executable_path}</code></p></Show>
        <Show
          when={props.agent.id === "opencode"}
          fallback={
            <>
              <p class="hint">K41 uses agy from PATH on this machine, with your existing user and workspace settings.</p>
              <p class="hint">Install Antigravity CLI separately. Update with <code>agy update</code> and sign in with <code>agy acp --login</code>, then refresh.</p>
              <a href="https://antigravity.google/docs/ide/extensions/acp" target="_blank" rel="noopener noreferrer">Antigravity CLI setup</a>
            </>
          }
        >
          <p class="hint">K41 uses opencode from PATH on this machine, with your existing user and workspace settings.</p>
          <p class="hint">Install OpenCode CLI separately. Update with <code>npm i -g @opencode/cli@latest</code> and sign in with <code>opencode auth login</code>, then refresh.</p>
          <a href="https://opencode.ai" target="_blank" rel="noopener noreferrer">OpenCode setup</a>
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
