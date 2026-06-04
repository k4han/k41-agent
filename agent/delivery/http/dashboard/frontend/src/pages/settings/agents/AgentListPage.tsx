import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { useNavigate } from "@solidjs/router";
import { Copy, Edit3, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { DataGate } from "@/components/State";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import type { AgentsPayload } from "@/types";

export function AgentListPage() {
  const navigate = useNavigate();
  const [data, setData] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [deleteTargetName, setDeleteTargetName] = createSignal<string | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<AgentsPayload>("/dashboard-api/agents"));
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
    try {
      await postJson(`/agents/cards/${encodeURIComponent(name)}/clone`);
      showToast("Agent cloned.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clone agent", "error");
    }
  };

  const confirmDeleteAgent = async () => {
    const name = deleteTargetName();
    if (!name) {
      return;
    }
    try {
      await deleteJson(`/agents/cards/${encodeURIComponent(name)}`);
      showToast("Agent deleted.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete agent", "error");
    } finally {
      setDeleteTargetName(null);
    }
  };

  const reloadAgents = async () => {
    try {
      const result = await postJson<AgentsPayload & { status: string }>("/agents/reload");
      setData(result);
      showToast("Agents reloaded.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to reload agents", "error");
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Agents"
      breadcrumbLabel="Agents"
      contentWidth="wide"
      actions={
        <button class="btn" type="button" onClick={reloadAgents}>
          <RefreshCw size={14} />
          Reload
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search agents..."
              onSearchInput={setQuery}
              actions={
                <button class="btn btn-primary" type="button" onClick={openCreate}>
                  <Plus size={14} />
                  New Agent
                </button>
              }
            />

            <section class="panel">
              <DashboardTable
                columns={[
                  { header: "Agent" },
                  { header: "Description" },
                  { header: "Provider / Model" },
                  { header: "Status" },
                  { header: "Actions" },
                ]}
                rows={filteredCards()}
                emptyMessage="No agent cards found."
              >
                {(card) => (
                  <tr>
                    <td>
                      <Show
                        when={card.display_name}
                        fallback={<div class="mono">{card.name}</div>}
                      >
                        <div>{card.display_name}</div>
                      </Show>
                    </td>
                    <td>
                      <Show
                        when={card.description}
                        fallback={<span class="hint">-</span>}
                      >
                        {(description) => (
                          <div class="hint">{truncateText(description(), 160)}</div>
                        )}
                      </Show>
                    </td>
                    <td>
                      <div class="chips">
                        <span class="chip">{`${card.provider || "default"}/${card.model || "provider default"}`}</span>
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={card.valid ? "valid" : "invalid"} />
                      <Show when={!card.valid && card.error}>
                        <div class="hint">{card.error}</div>
                      </Show>
                    </td>
                    <td>
                      <div class="row-wrap">
                        <button
                          class="btn btn-sm"
                          type="button"
                          onClick={() => openCard(card.name)}
                        >
                          <Edit3 size={13} />
                          {card.editable ? "Edit" : "View"}
                        </button>
                        <Show when={!card.editable}>
                          <button
                            class="btn btn-sm"
                            type="button"
                            onClick={() => void cloneAgent(card.name)}
                          >
                            <Copy size={13} />
                            Clone
                          </button>
                        </Show>
                        <button
                          class="btn btn-sm btn-danger"
                          type="button"
                          onClick={() => setDeleteTargetName(card.name)}
                        >
                          <Trash2 size={13} />
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </DashboardTable>
            </section>

            <ConfirmDialog
              open={deleteTargetName() !== null}
              title="Delete Agent"
              message={
                <p>
                  Are you sure you want to delete agent{" "}
                  <span class="mono">{deleteTargetName()}</span>?
                </p>
              }
              confirmLabel="Delete"
              confirmVariant="danger"
              onClose={() => setDeleteTargetName(null)}
              onConfirm={() => void confirmDeleteAgent()}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
