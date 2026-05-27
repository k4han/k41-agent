import { createSignal, For, onMount, Show } from "solid-js";
import { Link2, Trash2 } from "lucide-solid";

import { DataGate } from "@/components/State";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyTableRow } from "@/components/EmptyTableRow";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import type { Identity } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type ChannelsPayload = {
  identities: Identity[];
};

type PairingResponse = {
  code: string;
  user_id: string;
};

export function ChannelsPage() {
  const [data, setData] = createSignal<ChannelsPayload>();
  const [error, setError] = createSignal("");
  const [pairing, setPairing] = createSignal<PairingResponse | null>(null);
  const [unpairTarget, setUnpairTarget] = createSignal<Identity | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<ChannelsPayload>("/dashboard-api/channels"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    }
  };

  const createCode = async () => {
    try {
      setPairing(await postJson<PairingResponse>("/channels/pair"));
      showToast("Pairing code created.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create code", "error");
    }
  };

  const requestUnpair = (identity: Identity) => {
    if (identity.id === null) {
      return;
    }
    setUnpairTarget(identity);
  };

  const confirmUnpair = async () => {
    const identity = unpairTarget();
    if (!identity || identity.id === null) {
      return;
    }
    try {
      await deleteJson(`/channels/identities/${identity.id}`);
      showToast("Identity unpaired.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to unpair identity", "error");
    } finally {
      setUnpairTarget(null);
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Pair Channels"
      subtitle="Connect external channel identities to dashboard users."
      breadcrumbLabel="Channels"
      contentWidth="wide"
      actions={
        <button class="btn btn-primary" type="button" onClick={createCode}>
          <Link2 size={14} />
          New Pairing Code
        </button>
      }
    >
      <div class="stack">
        <Show when={pairing()}>
          {(item) => (
            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Pairing Code</div>
              </div>
              <div class="panel-body row-wrap">
                <span class="chip">{item().code}</span>
                <span class="hint">User ID {item().user_id}. The code expires in 24 hours.</span>
              </div>
            </section>
          )}
        </Show>

        <DataGate data={data()} error={error()} onRetry={load}>
          {(payload) => (
            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Paired Identities</div>
              </div>
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Platform</th>
                      <th>External ID</th>
                      <th>User ID</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={payload.identities}
                      fallback={
                        <EmptyTableRow colSpan={4} message="No paired identities." />
                      }
                    >
                      {(identity) => (
                        <tr>
                          <td>
                            <span class="badge">{identity.platform}</span>
                          </td>
                          <td class="mono">{identity.external_id}</td>
                          <td class="mono">{identity.user_id ?? "-"}</td>
                          <td>
                            <button
                              class="btn btn-sm btn-danger"
                              type="button"
                              onClick={() => requestUnpair(identity)}
                            >
                              <Trash2 size={13} />
                              Unpair
                            </button>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </DataGate>
      </div>

      <ConfirmDialog
        open={unpairTarget() !== null}
        title="Unpair Identity"
        message={<p>Unpair <span class="mono">{unpairTarget()?.platform}:{unpairTarget()?.external_id}</span>?</p>}
        confirmLabel="Unpair"
        confirmVariant="danger"
        onClose={() => setUnpairTarget(null)}
        onConfirm={() => void confirmUnpair()}
      />
    </SettingsLayout>
  );
}
