import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Link2, Save, Trash2 } from "lucide-solid";

import { DataGate } from "@/components/State";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import type { Identity, SettingInfo, SourceValue } from "@/types";

import { SettingsLayout } from "./SettingsLayout";
import {
  type PendingChange,
  SettingRow,
  SettingsConfirmDialog,
  SettingsSection,
  sameValue,
  typedValue,
} from "./shared";

type ChannelsPayload = {
  identities: Identity[];
  settings: Record<string, SettingInfo>;
  by_channel: Record<string, Record<string, SettingInfo>>;
  settings_sources: Record<string, SourceValue[]>;
};

type PairingResponse = {
  code: string;
  user_id: string;
};

export function ChannelsPage() {
  const [data, setData] = createSignal<ChannelsPayload>();
  const [error, setError] = createSignal("");
  const [drafts, setDrafts] = createSignal<Record<string, unknown>>({});
  const [confirmOpen, setConfirmOpen] = createSignal(false);
  const [pairing, setPairing] = createSignal<PairingResponse | null>(null);
  const [unpairTarget, setUnpairTarget] = createSignal<Identity | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<ChannelsPayload>("/dashboard-api/channels");
      setData(payload);
      setDrafts(
        Object.fromEntries(
          Object.entries(payload.settings).map(([key, info]) => [key, info.value]),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    }
  };

  const pendingChanges = createMemo<PendingChange[]>(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    return Object.entries(payload.settings)
      .map(([key, info]) => {
        const next = typedValue({ ...info, key }, drafts()[key]);
        return { key, oldValue: info.value, newValue: next };
      })
      .filter((change) => !sameValue(change.oldValue, change.newValue));
  });

  const channelGroups = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const order = new Map([
      ["telegram", 0],
      ["discord", 1],
      ["github", 2],
    ]);
    return Object.entries(payload.by_channel)
      .map(([name, settings]) => ({
        name,
        title: channelTitle(name),
        settings: Object.entries(settings),
      }))
      .sort((a, b) => {
        const left = order.get(a.name) ?? 99;
        const right = order.get(b.name) ?? 99;
        return left === right ? a.name.localeCompare(b.name) : left - right;
      });
  });

  const setDraft = (key: string, value: unknown) => {
    setDrafts((current) => ({ ...current, [key]: value }));
  };

  const restoreDraft = (key: string) => {
    const payload = data();
    const setting = payload?.settings[key];
    if (!setting) {
      showToast("No server value to restore.", "warning");
      return;
    }
    setDraft(key, setting.value ?? null);
    showToast("Change reverted.", "warning");
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

  const saveChanges = async () => {
    const changes = pendingChanges();
    if (!changes.length) {
      setConfirmOpen(false);
      return;
    }
    const values = Object.fromEntries(
      changes.map((change) => [change.key, change.newValue]),
    );
    try {
      await putJson("/settings", { values });
      showToast(`Updated ${changes.length} setting(s).`);
      setConfirmOpen(false);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save settings", "error");
    }
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Channel Settings"
      subtitle="Configure integrations and connect external identities."
      breadcrumbLabel="Channels"
      contentWidth="wide"
      actions={
        <>
          <button
            class="btn btn-primary"
            type="button"
            disabled={pendingChanges().length === 0}
            onClick={() => setConfirmOpen(true)}
          >
            <Save size={14} />
            Save Changes {pendingChanges().length ? `(${pendingChanges().length})` : ""}
          </button>
          <button class="btn" type="button" onClick={createCode}>
            <Link2 size={14} />
            New Pairing Code
          </button>
        </>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <For each={channelGroups()}>
              {(group) => (
                <SettingsSection
                  title={group.title}
                  description={`${group.settings.length} setting${group.settings.length === 1 ? "" : "s"}`}
                >
                  <div class="settings-list">
                    <For each={group.settings}>
                      {([key, info]) => (
                        <SettingRow
                          settingKey={key}
                          info={info}
                          draft={drafts()[key]}
                          dirty={pendingChanges().some((change) => change.key === key)}
                          onChange={(value) => setDraft(key, value)}
                          onRestore={() => restoreDraft(key)}
                        />
                      )}
                    </For>
                  </div>
                </SettingsSection>
              )}
            </For>

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

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Paired Identities</div>
              </div>
              <DashboardTable
                columns={[
                  { header: "Platform" },
                  { header: "External ID" },
                  { header: "User ID" },
                  { header: "Actions" },
                ]}
                rows={payload.identities}
                emptyMessage="No paired identities."
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
              </DashboardTable>
            </section>
          </div>
        )}
      </DataGate>

      <SettingsConfirmDialog
        open={confirmOpen()}
        changes={pendingChanges()}
        settings={data()?.settings || {}}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => void saveChanges()}
      />

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

function channelTitle(name: string): string {
  if (name === "telegram") {
    return "Telegram";
  }
  if (name === "discord") {
    return "Discord";
  }
  if (name === "github") {
    return "GitHub";
  }
  return name
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
