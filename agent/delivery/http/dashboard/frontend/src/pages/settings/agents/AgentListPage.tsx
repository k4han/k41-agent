import { createMemo, createSignal, onMount, Show } from "solid-js";
import { A, useNavigate } from "@solidjs/router";
import { Copy, Edit3, Eye, Loader2, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { deleteJson, postJson } from "@/lib/api";
import { fetchAgentCards } from "@/lib/agents";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import type { AgentCard, AgentCardsPayload } from "@/types";

export function AgentListPage() {
  const navigate = useNavigate();
  const [data, setData] = createSignal<AgentCardsPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<AgentCard | null>(null);
  const [cloningName, setCloningName] = createSignal<string | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [reloading, setReloading] = createSignal(false);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await fetchAgentCards());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    }
  };

  const filteredCards = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return payload.cards;
    }
    return payload.cards.filter((card) =>
      [
        card.name,
        card.display_name,
        card.description,
        card.graph_type,
        card.provider,
        card.model,
        card.source,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  });

  const openCreate = () => {
    navigate("/settings/agents/new");
  };

  const openCard = (name: string) => {
    navigate(`/settings/agents/${encodeURIComponent(name)}`);
  };

  const cloneAgent = async (name: string) => {
    if (cloningName()) {
      return;
    }
    setCloningName(name);
    try {
      await postJson(`/agents/cards/${encodeURIComponent(name)}/clone`);
      showToast("Agent cloned.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clone agent", "error");
    } finally {
      setCloningName(null);
    }
  };

  const confirmDeleteAgent = async () => {
    const card = deleteTarget();
    if (!card || card.source !== "user" || !card.editable || deleting()) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJson(`/agents/cards/${encodeURIComponent(card.name)}`);
      showToast("Agent deleted.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete agent", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const reloadAgents = async () => {
    if (reloading()) {
      return;
    }
    setReloading(true);
    try {
      const result = await postJson<AgentCardsPayload & { status: string }>("/agents/reload");
      setData(result);
      showToast("Agents reloaded.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to reload agents", "error");
    } finally {
      setReloading(false);
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Agents"
      breadcrumbLabel="Agents"
      contentWidth="wide"
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {() => (
          <div class="stack agent-list">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search agents..."
              onSearchInput={setQuery}
              actions={
                <>
                  <button class="btn" type="button" onClick={reloadAgents} disabled={reloading()}>
                    <RefreshCw size={14} class={reloading() ? "spin-icon" : undefined} />
                    {reloading() ? "Reloading..." : "Reload"}
                  </button>
                  <button class="btn btn-primary" type="button" onClick={openCreate}>
                    <Plus size={14} />
                    New Agent
                  </button>
                </>
              }
            />

            <section class="panel">
              <DashboardTable
                tableClass="agent-list-table"
                columns={[
                  { header: "Agent", class: "agent-list-agent-column" },
                  { header: "Description" },
                  { header: "Provider / Model", class: "agent-list-model-column" },
                  { header: "Actions", class: "agent-list-actions-column" },
                ]}
                rows={filteredCards()}
                emptyMessage="No agent cards found."
              >
                {(card) => (
                  <tr>
                    <td>
                      <div class="agent-list-identity">
                        <div class="agent-list-heading">
                          <A
                            class="agent-list-name"
                            href={`/settings/agents/${encodeURIComponent(card.name)}`}
                            title={card.display_name || card.name}
                          >
                            {card.display_name || card.name}
                          </A>
                          <span class="badge agent-list-source" classList={{ "badge-info": card.overrides_builtin }}>
                            {card.source === "builtin" ? "Built-in" : card.overrides_builtin ? "Cloned" : "Custom"}
                          </span>
                        </div>
                        <Show when={card.display_name && card.display_name !== card.name}>
                          <div class="hint mono agent-list-identifier">{card.name}</div>
                        </Show>
                        <Show when={!card.valid && card.error}>
                          <div class="agent-list-error">{card.error}</div>
                        </Show>
                      </div>
                    </td>
                    <td>
                      <Show
                        when={card.description}
                        fallback={<span class="hint">-</span>}
                      >
                        {(description) => (
                          <div class="hint agent-list-description" title={description()}>{description()}</div>
                        )}
                      </Show>
                    </td>
                    <td>
                      <div class="agent-list-model">
                        <span class="agent-list-model-name">{card.model || "Provider default"}</span>
                        <span class="hint">{card.provider || "default"}</span>
                      </div>
                    </td>
                    <td>
                      <div class="agent-list-actions">
                        <button
                          class="btn btn-sm"
                          type="button"
                          aria-label={`${card.editable ? "Edit" : "View"} ${card.display_name || card.name}`}
                          onClick={() => openCard(card.name)}
                        >
                          <Show when={card.editable} fallback={<Eye size={14} />}>
                            <Edit3 size={14} />
                          </Show>
                          {card.editable ? "Edit" : "View"}
                        </button>
                        <Show when={card.source === "builtin" && !card.editable}>
                          <button
                            class="btn btn-sm btn-icon"
                            type="button"
                            aria-label={`Clone ${card.display_name || card.name}`}
                            title="Clone agent to customize"
                            disabled={cloningName() !== null}
                            onClick={() => void cloneAgent(card.name)}
                          >
                            <Show when={cloningName() === card.name} fallback={<Copy size={14} />}>
                              <Loader2 size={14} class="spin-icon" />
                            </Show>
                          </button>
                        </Show>
                        <Show when={card.source === "user" && card.editable}>
                          <button
                            class="btn btn-sm btn-icon btn-danger"
                            type="button"
                            aria-label={`Delete ${card.display_name || card.name}`}
                            title={card.overrides_builtin ? "Delete clone and restore built-in agent" : "Delete agent"}
                            onClick={() => setDeleteTarget(card)}
                          >
                            <Trash2 size={14} />
                          </button>
                        </Show>
                      </div>
                    </td>
                  </tr>
                )}
              </DashboardTable>
            </section>

            <ConfirmDialog
              open={deleteTarget() !== null}
              title="Delete Agent"
              message={
                <div class="stack">
                  <p>
                    Are you sure you want to delete agent{" "}
                    <span class="mono">{deleteTarget()?.name}</span>?
                  </p>
                  <Show when={deleteTarget()?.overrides_builtin}>
                    <p class="hint">Deleting this clone restores the built-in agent.</p>
                  </Show>
                </div>
              }
              confirmLabel="Delete"
              confirmVariant="danger"
              loading={deleting()}
              onClose={() => {
                if (!deleting()) {
                  setDeleteTarget(null);
                }
              }}
              onConfirm={() => void confirmDeleteAgent()}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
