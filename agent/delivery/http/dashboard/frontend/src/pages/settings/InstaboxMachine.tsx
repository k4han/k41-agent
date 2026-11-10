import { createMemo, createSignal, For, onCleanup, Show } from "solid-js";
import { useNavigate, useParams } from "@solidjs/router";
import {
  Camera,
  CircleStop,
  Copy,
  ExternalLink,
  GitFork,
  KeyRound,
  Lock,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
  Unlock,
} from "lucide-solid";

import { DataGate } from "@/components/State";
import { InstaboxDesktop } from "@/components/InstaboxDesktop";
import { TypedConfirmDialog } from "@/components/TypedConfirmDialog";
import { useToast } from "@/components/Toast";
import {
  ACTIVE_OPERATION_STATUSES,
  errorText,
  instaboxApi,
  instaboxMachineHref,
  type InstaboxMachine as Machine,
  type InstaboxOperation,
  type InstaboxPort,
  type InstaboxSnapshot,
} from "@/lib/instabox";
import "@/styles/instabox.css";

import { SettingsLayout } from "./SettingsLayout";

type Tab = "ports" | "snapshots" | "desktop" | "operations";
type Confirm =
  | { kind: "delete-machine" }
  | { kind: "delete-snapshot"; snapshot: InstaboxSnapshot }
  | { kind: "restore"; snapshot: InstaboxSnapshot; step: "machine" | "snapshot" };

const POLL_MS = 3000;

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function InstaboxMachinePage() {
  const params = useParams();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const machineId = () => params.machineId || "";
  const [machine, setMachine] = createSignal<Machine | null>(null);
  const [error, setError] = createSignal("");
  const [tab, setTab] = createSignal<Tab>("ports");
  const [ports, setPorts] = createSignal<InstaboxPort[]>([]);
  const [snapshots, setSnapshots] = createSignal<InstaboxSnapshot[]>([]);
  const [operations, setOperations] = createSignal<InstaboxOperation[]>([]);
  const [busy, setBusy] = createSignal("");
  const [portNumber, setPortNumber] = createSignal("3000");
  const [portAccess, setPortAccess] = createSignal<"private" | "public">("private");
  const [snapshotName, setSnapshotName] = createSignal("");
  const [forkHint, setForkHint] = createSignal("");
  const [confirm, setConfirm] = createSignal<Confirm | null>(null);

  const activeOperations = createMemo(() => operations().filter((op) => ACTIVE_OPERATION_STATUSES.has(op.status)));
  const machineSnapshots = createMemo(() => snapshots());

  const loadMachine = async () => {
    try {
      setMachine(await instaboxApi.machine(machineId()));
      setError("");
    } catch (err) {
      setError(errorText(err, "Failed to load the machine."));
    }
  };
  const loadPorts = async () => setPorts((await instaboxApi.ports(machineId())).ports);
  const loadSnapshots = async () => setSnapshots((await instaboxApi.snapshots()).snapshots);
  const loadOperations = async () => setOperations((await instaboxApi.operations(machineId())).operations);

  const refreshAll = async () => {
    await loadMachine();
    await Promise.allSettled([loadPorts(), loadSnapshots(), loadOperations()]);
  };
  void refreshAll();

  // Keep progress visible after a reload: poll while operations or snapshots are still pending.
  const poll = window.setInterval(() => {
    const pendingSnapshot = snapshots().some((item) => item.state === "pending");
    if (activeOperations().length > 0 || pendingSnapshot) {
      void loadOperations().catch(() => undefined);
      void loadMachine();
      if (pendingSnapshot) void loadSnapshots().catch(() => undefined);
    }
  }, POLL_MS);
  onCleanup(() => window.clearInterval(poll));

  const run = async (label: string, action: () => Promise<unknown>, success?: string) => {
    setBusy(label);
    try {
      await action();
      if (success) showToast(success, "success");
      return true;
    } catch (err) {
      showToast(errorText(err, `Failed to ${label}.`), "error");
      return false;
    } finally {
      setBusy("");
      void loadOperations().catch(() => undefined);
    }
  };

  const machineAction = (action: "start" | "stop" | "suspend" | "resume") =>
    run(action, async () => {
      await instaboxApi.action(machineId(), action);
      await loadMachine();
    }, `Machine ${action} requested.`);

  const fork = async () => {
    setForkHint("");
    setBusy("fork");
    try {
      const record = await instaboxApi.fork(machineId());
      showToast(record.machine_id ? `Fork ${record.machine_id} is being created.` : "Fork requested.", "success");
    } catch (err) {
      const message = errorText(err, "Fork failed.");
      setForkHint(`${message} Forking needs a running machine on a host with room for a copy. You can create a snapshot and start a new machine from it instead.`);
    } finally {
      setBusy("");
      void loadOperations().catch(() => undefined);
    }
  };

  const publishPort = () =>
    run("publish port", async () => {
      const port = Number.parseInt(portNumber(), 10);
      if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Use a port between 1024 and 65535.");
      await instaboxApi.publishPort(machineId(), port, portAccess());
      await loadPorts();
    }, "Port published.");

  const openPort = async (port: InstaboxPort) => {
    try {
      const link = await instaboxApi.portLink(machineId(), port.port);
      if (link.token_missing) {
        showToast("The access token for this private port is not stored. Rotate the token to get a new link.", "error");
        return;
      }
      window.open(link.url, "_blank", "noopener,noreferrer");
    } catch (err) {
      showToast(errorText(err, "Could not open the port."), "error");
    }
  };

  const copyPort = async (port: InstaboxPort) => {
    try {
      const link = await instaboxApi.portLink(machineId(), port.port);
      if (link.token_missing) {
        showToast("Rotate the token to get a shareable link.", "error");
        return;
      }
      await navigator.clipboard.writeText(link.url);
      showToast(port.access === "private" ? "Private link copied. Anyone with it can open the port." : "Link copied.", "success");
    } catch (err) {
      showToast(errorText(err, "Could not copy the link."), "error");
    }
  };

  const createSnapshot = () =>
    run("create snapshot", async () => {
      const name = snapshotName().trim() || `${machine()?.name || "machine"}-${new Date().toISOString().slice(0, 10)}`;
      await instaboxApi.createSnapshot(machineId(), name);
      setSnapshotName("");
      await loadSnapshots();
    }, "Snapshot started. It can be used once it is ready.");

  const machineFromSnapshot = (snapshot: InstaboxSnapshot) =>
    run("create machine", async () => {
      const record = await instaboxApi.machineFromSnapshot(snapshot.id);
      if (record.machine_id) navigate(instaboxMachineHref(record.machine_id));
    }, "Machine is being created from the snapshot.");

  const performConfirm = async () => {
    const current = confirm();
    if (!current) return;
    if (current.kind === "delete-machine") {
      const ok = await run("delete machine", () => instaboxApi.deleteMachine(machineId(), machineId()), "Machine deletion requested.");
      if (ok) {
        setConfirm(null);
        navigate("/settings/sandboxes");
      }
    } else if (current.kind === "delete-snapshot") {
      const ok = await run("delete snapshot", () => instaboxApi.deleteSnapshot(current.snapshot.id, current.snapshot.id), "Snapshot deleted.");
      if (ok) {
        setConfirm(null);
        await loadSnapshots();
      }
    } else if (current.step === "machine") {
      setConfirm({ ...current, step: "snapshot" });
    } else {
      const ok = await run(
        "restore",
        () => instaboxApi.restore(machineId(), current.snapshot.id, machineId(), current.snapshot.id),
        "Restore started. Workspaces on this machine are paused until it finishes.",
      );
      if (ok) {
        setConfirm(null);
        setTab("operations");
      }
    }
  };

  const status = () => machine()?.status || "unknown";
  const state = () => machine()?.state || "";

  return (
    <SettingsLayout
      title={machine()?.name || machineId()}
      breadcrumbSegments={[{ label: "Sandboxes", href: "/settings/sandboxes" }, { label: machine()?.name || machineId() }]}
      contentWidth="wide"
    >
      <DataGate data={machine()} error={error()} onRetry={() => void refreshAll()}>
        {() => (
          <div class="instabox-machine">
            <div class="instabox-machine-header">
              <div class="instabox-machine-title">
                <h2>{machine()?.name || machineId()}</h2>
                <div class="instabox-machine-meta">
                  <span class="mono">{machineId()}</span>
                  <span>State: <strong>{state() || status()}</strong></span>
                  <Show when={machine()?.template}><span>Template: {machine()?.template}</span></Show>
                  <Show when={machine()?.vcpus}><span>{machine()?.vcpus} vCPU · {machine()?.mem_mib} MiB</span></Show>
                  <Show when={activeOperations().length > 0}><span>{activeOperations().length} operation(s) in progress</span></Show>
                </div>
              </div>
              <div class="instabox-machine-actions">
                <Show when={state() === "stopped"}>
                  <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void machineAction("start")}><Play size={13} /> Start</button>
                </Show>
                <Show when={state() === "suspended"}>
                  <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void machineAction("resume")}><Play size={13} /> Resume</button>
                </Show>
                <Show when={state() === "running"}>
                  <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void machineAction("suspend")}><Pause size={13} /> Suspend</button>
                  <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void machineAction("stop")}><CircleStop size={13} /> Stop</button>
                  <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void fork()}><GitFork size={13} /> Fork</button>
                </Show>
                <button class="btn btn-sm" type="button" disabled={!!busy()} onClick={() => void refreshAll()}><RefreshCw size={13} /> Refresh</button>
                <button class="btn btn-sm btn-danger" type="button" disabled={!!busy()} onClick={() => setConfirm({ kind: "delete-machine" })}><Trash2 size={13} /> Delete</button>
              </div>
            </div>
            <Show when={forkHint()}><p class="instabox-hint" role="status">{forkHint()}</p></Show>

            <div class="instabox-tabs" role="tablist" aria-label="Machine sections">
              <For each={[["ports", "Ports"], ["snapshots", "Snapshots"], ["desktop", "Desktop"], ["operations", "Operations"]] as [Tab, string][]}>
                {([value, label]) => (
                  <button class="instabox-tab" type="button" role="tab" aria-selected={tab() === value} onClick={() => setTab(value)}>{label}</button>
                )}
              </For>
            </div>

            <Show when={tab() === "ports"}>
              <section class="instabox-panel">
                <div class="instabox-form">
                  <label>Port<input type="number" min="1024" max="65535" value={portNumber()} onInput={(e) => setPortNumber(e.currentTarget.value)} /></label>
                  <label>Access
                    <select value={portAccess()} onChange={(e) => setPortAccess(e.currentTarget.value as "private" | "public")}>
                      <option value="private">Private (token link)</option>
                      <option value="public">Public</option>
                    </select>
                  </label>
                  <button class="btn btn-sm btn-primary" type="button" disabled={!!busy()} onClick={() => void publishPort()}>Publish</button>
                </div>
                <p class="instabox-hint">Private links carry an access token. Anyone with the link can open the port until you rotate the token.</p>
                <Show when={ports().length > 0} fallback={<p class="instabox-hint">No published ports.</p>}>
                  <div class="instabox-table-wrap">
                    <table class="instabox-table">
                      <thead><tr><th>Port</th><th>Access</th><th>URL</th><th /></tr></thead>
                      <tbody>
                        <For each={ports()}>
                          {(port) => (
                            <tr>
                              <td class="mono">{port.port}</td>
                              <td>{port.access === "private" ? <><Lock size={12} /> Private</> : <><Unlock size={12} /> Public</>}</td>
                              <td class="mono">{port.url || "—"}</td>
                              <td>
                                <div class="instabox-row-actions">
                                  <button class="btn btn-sm" type="button" onClick={() => void openPort(port)}><ExternalLink size={12} /> Open</button>
                                  <button class="btn btn-sm" type="button" onClick={() => void copyPort(port)}><Copy size={12} /> Copy link</button>
                                  <button class="btn btn-sm" type="button" disabled={!!busy()}
                                    onClick={() => void run("change access", async () => {
                                      await instaboxApi.setPortAccess(machineId(), port.port, port.access === "private" ? "public" : "private");
                                      await loadPorts();
                                    })}>
                                    {port.access === "private" ? "Make public" : "Make private"}
                                  </button>
                                  <Show when={port.access === "private"}>
                                    <button class="btn btn-sm" type="button" disabled={!!busy()}
                                      onClick={() => void run("rotate token", async () => {
                                        await instaboxApi.rotatePort(machineId(), port.port);
                                        await loadPorts();
                                      }, "Token rotated. Old links stop working.")}>
                                      <KeyRound size={12} /> Rotate
                                    </button>
                                  </Show>
                                  <button class="btn btn-sm btn-danger" type="button" disabled={!!busy()}
                                    onClick={() => void run("close port", async () => {
                                      await instaboxApi.closePort(machineId(), port.port);
                                      await loadPorts();
                                    })}>
                                    Close
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </div>
                </Show>
              </section>
            </Show>

            <Show when={tab() === "snapshots"}>
              <section class="instabox-panel">
                <div class="instabox-form">
                  <label>Snapshot name<input type="text" maxLength={63} placeholder="optional" value={snapshotName()} onInput={(e) => setSnapshotName(e.currentTarget.value)} /></label>
                  <button class="btn btn-sm btn-primary" type="button" disabled={!!busy()} onClick={() => void createSnapshot()}><Camera size={13} /> Create snapshot</button>
                </div>
                <Show when={machineSnapshots().length > 0} fallback={<p class="instabox-hint">No snapshots yet.</p>}>
                  <div class="instabox-table-wrap">
                    <table class="instabox-table">
                      <thead><tr><th>Name</th><th>State</th><th>ID</th><th>Created</th><th /></tr></thead>
                      <tbody>
                        <For each={machineSnapshots()}>
                          {(snapshot) => (
                            <tr>
                              <td>{snapshot.name}</td>
                              <td><span class="instabox-op-status" data-status={snapshot.state === "ready" ? "succeeded" : snapshot.state}>{snapshot.state}</span></td>
                              <td class="mono">{snapshot.id}</td>
                              <td>{formatTime(snapshot.createdAt)}</td>
                              <td>
                                <div class="instabox-row-actions">
                                  <button class="btn btn-sm" type="button" disabled={!!busy() || snapshot.state !== "ready"}
                                    onClick={() => void machineFromSnapshot(snapshot)}>New machine</button>
                                  <button class="btn btn-sm" type="button" disabled={!!busy() || snapshot.state !== "ready"}
                                    onClick={() => setConfirm({ kind: "restore", snapshot, step: "machine" })}><RotateCcw size={12} /> Restore</button>
                                  <button class="btn btn-sm btn-danger" type="button" disabled={!!busy()}
                                    onClick={() => setConfirm({ kind: "delete-snapshot", snapshot })}><Trash2 size={12} /></button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </div>
                </Show>
              </section>
            </Show>

            <Show when={tab() === "desktop"}>
              <section class="instabox-panel">
                <p class="instabox-hint">You watch by default. Take control to pause the agent's desktop tools; control returns to the agent when you hand back or stop sending heartbeats for 30 seconds.</p>
                <div class="instabox-desktop-page">
                  <InstaboxDesktop machineId={machineId()} active={tab() === "desktop" && state() === "running"} />
                </div>
                <Show when={state() !== "running"}><p class="instabox-hint">Start or resume the machine to open its desktop.</p></Show>
              </section>
            </Show>

            <Show when={tab() === "operations"}>
              <section class="instabox-panel">
                <Show when={operations().length > 0} fallback={<p class="instabox-hint">No operations recorded for this machine.</p>}>
                  <div class="instabox-table-wrap">
                    <table class="instabox-table">
                      <thead><tr><th>Operation</th><th>Status</th><th>Started</th><th>Updated</th><th>Details</th></tr></thead>
                      <tbody>
                        <For each={operations()}>
                          {(op) => (
                            <tr>
                              <td>{op.kind}</td>
                              <td><span class="instabox-op-status" data-status={op.status}>{op.phase ? `${op.status} · ${op.phase}` : op.status}</span></td>
                              <td>{formatTime(op.created_at)}</td>
                              <td>{formatTime(op.updated_at)}</td>
                              <td>{op.error?.message || op.operation_id || "—"}</td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </div>
                </Show>
              </section>
            </Show>
          </div>
        )}
      </DataGate>

      <TypedConfirmDialog
        open={confirm() !== null}
        busy={!!busy()}
        title={confirm()?.kind === "delete-machine" ? "Delete machine"
          : confirm()?.kind === "delete-snapshot" ? "Delete snapshot" : "Restore machine from snapshot"}
        expected={(() => {
          const current = confirm();
          if (!current) return "";
          if (current.kind === "delete-machine") return machineId();
          if (current.kind === "delete-snapshot") return current.snapshot.id;
          return current.step === "machine" ? machineId() : current.snapshot.id;
        })()}
        confirmLabel={confirm()?.kind === "restore" ? (confirm() as { step: string }).step === "machine" ? "Next" : "Restore" : "Delete"}
        onClose={() => setConfirm(null)}
        onConfirm={() => void performConfirm()}
      >
        <Show when={confirm()?.kind === "delete-machine"}>
          <p>The machine and its disk are deleted permanently. Chat threads using it lose their environment.</p>
        </Show>
        <Show when={confirm()?.kind === "delete-snapshot"}>
          <p>The snapshot is deleted permanently. Machines already created from it keep running.</p>
        </Show>
        <Show when={confirm()?.kind === "restore"}>
          <p>
            Restoring replaces the machine's disk with the snapshot. Changes made after the snapshot are lost on the machine;
            the app saves a copy of each workspace first. Restore is refused while tasks or processes are running.
          </p>
          <p class="instabox-hint">Step {(confirm() as { step: string }).step === "machine" ? "1 of 2: machine ID" : "2 of 2: snapshot ID"}</p>
        </Show>
      </TypedConfirmDialog>
    </SettingsLayout>
  );
}
