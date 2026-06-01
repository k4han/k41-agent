import { createSignal, onMount, Show } from "solid-js";
import { Copy, Fingerprint, Link2, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import { writeToClipboard } from "@/lib/utils";
import type { Identity } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type PairingPayload = {
  identities: Identity[];
};

type PairingResponse = {
  code: string;
  user_id: string;
};

export function PairingPage() {
  const [data, setData] = createSignal<PairingPayload>();
  const [error, setError] = createSignal("");
  const [pairing, setPairing] = createSignal<PairingResponse | null>(null);
  const [creating, setCreating] = createSignal(false);
  const [unpairTarget, setUnpairTarget] = createSignal<Identity | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<{ identities: Identity[] }>(
        "/dashboard-api/channels",
      );
      setData({ identities: payload.identities });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load identities");
    }
  };

  const createCode = async () => {
    setCreating(true);
    try {
      const response = await postJson<PairingResponse>("/channels/pair");
      setPairing(response);
      showToast("Pairing code created.");
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to create code",
        "error",
      );
    } finally {
      setCreating(false);
    }
  };

  const copyCode = async () => {
    const item = pairing();
    if (!item) {
      return;
    }
    try {
      await writeToClipboard(item.code);
      showToast("Pairing code copied.");
    } catch {
      showToast("Failed to copy code.", "error");
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
      showToast(
        err instanceof Error ? err.message : "Failed to unpair identity",
        "error",
      );
    } finally {
      setUnpairTarget(null);
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Pairing"
      subtitle="Generate codes to link external chat identities to a Kaka user."
      breadcrumbLabel="Pairing"
      contentWidth="wide"
      actions={
        <button
          class="btn btn-primary"
          type="button"
          disabled={creating()}
          onClick={() => void createCode()}
        >
          <Link2 size={14} />
          {creating() ? "Creating..." : "New Pairing Code"}
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <Show
              when={pairing()}
              fallback={
                <section class="panel">
                  <div class="panel-body stack">
                    <div class="row-wrap" style={{ gap: "10px", "align-items": "center" }}>
                      <Fingerprint size={18} />
                      <div>
                        <div class="setting-title">No active code</div>
                        <div class="hint">
                          Generate a pairing code, then send <span class="mono">/pair XXXX-XXXX</span> from
                          Telegram or Discord to link that account to a Kaka user.
                        </div>
                      </div>
                    </div>
                  </div>
                </section>
              }
            >
              {(item) => (
                <section class="pairing-code-display">
                  <div class="channel-card-meta-label">Pairing Code</div>
                  <div class="pairing-code-value">
                    <span class="chip">{item().code}</span>
                    <button
                      class="btn btn-sm"
                      type="button"
                      onClick={() => void copyCode()}
                    >
                      <Copy size={13} />
                      Copy
                    </button>
                    <span class="hint">
                      User ID <span class="mono">{item().user_id}</span>. The code expires in 24 hours.
                      Send <span class="mono">/pair {item().code}</span> from Telegram or Discord.
                    </span>
                  </div>
                </section>
              )}
            </Show>

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Paired Identities</div>
                <span class="hint">{payload.identities.length} linked</span>
              </div>
              <DashboardTable
                columns={[
                  { header: "Platform" },
                  { header: "External ID" },
                  { header: "User ID" },
                  { header: "Linked Since" },
                  { header: "Actions" },
                ]}
                rows={payload.identities}
                emptyMessage="No paired identities yet."
              >
                {(identity) => (
                  <tr>
                    <td>
                      <span class="badge">{identity.platform}</span>
                    </td>
                    <td class="mono">{identity.external_id}</td>
                    <td class="mono">{identity.user_id ?? "-"}</td>
                    <td class="mono hint">{formatDate(identity.created_at)}</td>
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
              </DashboardTable>
            </section>
          </div>
        )}
      </DataGate>

      <ConfirmDialog
        open={unpairTarget() !== null}
        title="Unpair Identity"
        message={
          <p>
            Unpair{" "}
            <span class="mono">
              {unpairTarget()?.platform}:{unpairTarget()?.external_id}
            </span>
            ? The connected account will lose access until paired again.
          </p>
        }
        confirmLabel="Unpair"
        confirmVariant="danger"
        onClose={() => setUnpairTarget(null)}
        onConfirm={() => void confirmUnpair()}
      />
    </SettingsLayout>
  );
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}
