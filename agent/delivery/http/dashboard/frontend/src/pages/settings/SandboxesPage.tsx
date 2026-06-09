import {
  createMemo,
  createSignal,
  For,
  Show,
} from "solid-js";
import { A } from "@solidjs/router";
import {
  Archive,
  CircleStop,
  CloudCog,
  ExternalLink,
  HardDrive,
  PowerOff,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-solid";

import { DataGate } from "@/components/State";
import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { getBackends } from "@/lib/catalogStore";
import { getBackendIcon } from "@/lib/iconRegistry";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { truncateText } from "@/lib/utils";
import { chatThreadHref } from "@/lib/chatThreads";
import type {
  SandboxBackendKey,
  SandboxListPayload,
  SandboxStatus,
  SandboxSummary,
} from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type SandboxRow = SandboxSummary;
type FilterValue = "all" | SandboxBackendKey;
type StatusFilter = "all" | "active" | "stopped" | "archived" | "destroyed";

const STATUS_LABELS: Record<SandboxStatus, string> = {
  started: "Running",
  starting: "Starting",
  stopped: "Stopped",
  archived: "Archived",
  destroyed: "Destroyed",
  error: "Error",
  unknown: "Unknown",
};

const STATUS_TONE: Record<SandboxStatus, "ok" | "warn" | "muted" | "danger"> = {
  started: "ok",
  starting: "warn",
  stopped: "muted",
  archived: "muted",
  destroyed: "danger",
  error: "danger",
  unknown: "muted",
};

const STATUS_GROUP: Record<SandboxStatus, StatusFilter> = {
  started: "active",
  starting: "active",
  stopped: "stopped",
  archived: "archived",
  destroyed: "destroyed",
  error: "destroyed",
  unknown: "stopped",
};

const TERMINAL_STATUSES: ReadonlyArray<SandboxStatus> = [
  "destroyed",
  "error",
  "archived",
];

function backendIcon(name: SandboxBackendKey) {
  return getBackendIcon(name)();
}

function formatRelativeTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "—";
  }
  const diffMs = Date.now() - timestamp;
  if (diffMs < 0) {
    return "just now";
  }
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return `${days}d ago`;
  }
  const months = Math.floor(days / 30);
  if (months < 12) {
    return `${months}mo ago`;
  }
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

function statusPill(status: SandboxStatus) {
  const label = STATUS_LABELS[status] || status;
  const tone = STATUS_TONE[status] || "muted";
  return (
    <span class={`sandbox-status-pill sandbox-status-${tone}`}>
      <span class="sandbox-status-dot" />
      {label}
    </span>
  );
}

function sandboxDisplayId(value: string): string {
  if (!value) {
    return "—";
  }
  if (value.length <= 16) {
    return value;
  }
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function countByBackend(rows: SandboxRow[]): Record<string, number> {
  const counts: Record<string, number> = { all: rows.length };
  for (const backend of getBackends()) {
    if (backend.name !== "local") {
      counts[backend.name] = 0;
    }
  }
  for (const row of rows) {
    counts[row.backend] = (counts[row.backend] || 0) + 1;
  }
  return counts;
}

export function SandboxesPage() {
  const { showToast } = useToast();
  const [rows, setRows] = createSignal<SandboxRow[]>([]);
  const [loaded, setLoaded] = createSignal(false);
  const [error, setError] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [filter, setFilter] = createSignal<FilterValue>("all");
  const [statusFilter, setStatusFilter] = createSignal<StatusFilter>("all");
  const [search, setSearch] = createSignal("");
  const [includeAll, setIncludeAll] = createSignal(false);
  const [deleteTarget, setDeleteTarget] = createSignal<SandboxRow | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [actionBusy, setActionBusy] = createSignal<Record<string, string>>({});

  const fetchBackend = async (target: SandboxBackendKey): Promise<SandboxRow[]> => {
    const params = new URLSearchParams({
      backend: target,
      include_all: String(includeAll()),
    });
    const payload = await apiFetch<SandboxListPayload>(
      `${API_PATHS.sandboxes}?${params.toString()}`,
    );
    return payload.sandboxes;
  };

  const refresh = async () => {
    setError("");
    setBusy(true);
    try {
      if (filter() === "all") {
        const sandboxBackends = getBackends()
          .filter((b) => b.name !== "local")
          .map((b) => b.name);
        const results = await Promise.all(
          sandboxBackends.map((name) => fetchBackend(name as SandboxBackendKey)),
        );
        const merged = results.flat().sort((a, b) => {
          const aKey = a.last_used_at || a.updated_at || a.created_at || "";
          const bKey = b.last_used_at || b.updated_at || b.created_at || "";
          return bKey.localeCompare(aKey);
        });
        setRows(merged);
      } else {
        const data = await fetchBackend(filter() as SandboxBackendKey);
        setRows(data);
      }
      setLoaded(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load sandboxes",
      );
    } finally {
      setBusy(false);
    }
  };

  useCatalogAndLoad(() => refresh());

  const handleBackendChange = async (next: FilterValue) => {
    setFilter(next);
    await refresh();
  };

  const handleIncludeAllToggle = async (next: boolean) => {
    setIncludeAll(next);
    await refresh();
  };

  const setActionState = (row: SandboxRow, action: string | null) => {
    const key = `${row.backend}:${row.sandbox_id}`;
    setActionBusy((current) => {
      const next = { ...current };
      if (action) {
        next[key] = action;
      } else {
        delete next[key];
      }
      return next;
    });
  };

  const handleStop = async (row: SandboxRow) => {
    if (row.backend !== "daytona") {
      return;
    }
    setActionState(row, "stop");
    try {
      const result = await postJson<{ cloud_status: string }>(
        API_PATHS.sandboxStop(row.backend, row.sandbox_id),
      );
      showToast(
        `Sandbox ${sandboxDisplayId(row.sandbox_id)} stopped (${result.cloud_status}).`,
        "success",
      );
      await refresh();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to stop sandbox",
        "error",
      );
    } finally {
      setActionState(row, null);
    }
  };

  const handleArchive = async (row: SandboxRow) => {
    if (row.backend !== "daytona") {
      return;
    }
    setActionState(row, "archive");
    try {
      const result = await postJson<{ cloud_status: string }>(
        API_PATHS.sandboxArchive(row.backend, row.sandbox_id),
      );
      showToast(
        `Sandbox ${sandboxDisplayId(row.sandbox_id)} archived (${result.cloud_status}).`,
        "success",
      );
      await refresh();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to archive sandbox",
        "error",
      );
    } finally {
      setActionState(row, null);
    }
  };

  const confirmDelete = (row: SandboxRow) => {
    setDeleteTarget(row);
  };

  const cancelDelete = () => {
    if (deleting()) {
      return;
    }
    setDeleteTarget(null);
  };

  const performDelete = async () => {
    const row = deleteTarget();
    if (!row) {
      return;
    }
    setDeleting(true);
    try {
      const result = await deleteJson<{ cloud_status: string; detached_threads: string[] }>(
        API_PATHS.sandbox(row.backend, row.sandbox_id),
      );
      const threadCount = result.detached_threads.length;
      showToast(
        threadCount > 0
          ? `Sandbox ${sandboxDisplayId(row.sandbox_id)} deleted; detached from ${threadCount} thread(s).`
          : `Sandbox ${sandboxDisplayId(row.sandbox_id)} deleted.`,
        "success",
      );
      setDeleteTarget(null);
      await refresh();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to delete sandbox",
        "error",
      );
    } finally {
      setDeleting(false);
    }
  };

  const visibleRows = createMemo<SandboxRow[]>(() => {
    const list = rows();
    const backendFilter = filter();
    const status = statusFilter();
    const query = search().trim().toLowerCase();
    return list.filter((row) => {
      if (backendFilter !== "all" && row.backend !== backendFilter) {
        return false;
      }
      if (status !== "all" && STATUS_GROUP[row.status] !== status) {
        return false;
      }
      if (query) {
        const haystack = [
          row.sandbox_id,
          row.label,
          row.repository_full_name || "",
          row.thread_id || "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(query)) {
          return false;
        }
      }
      return true;
    });
  });

  const counts = createMemo(() => countByBackend(rows()));

  const hasSandboxes = createMemo(() => rows().length > 0);

  return (
    <SettingsLayout
      title="Sandboxes"
      breadcrumbLabel="Sandboxes"
      contentWidth="wide"
    >
      <DataGate
        data={loaded() ? { count: rows().length } : null}
        error={error()}
        onRetry={() => void refresh()}
      >
        {() => (
          <div class="sandboxes-page">
            <div class="sandboxes-toolbar">
              <div class="sandboxes-filter-group" role="tablist" aria-label="Filter by backend">
                <button
                  class={`sandboxes-filter-chip ${filter() === "all" ? "active" : ""}`}
                  type="button"
                  role="tab"
                  aria-selected={filter() === "all"}
                  onClick={() => void handleBackendChange("all")}
                >
                  <span class="sandboxes-filter-icon" aria-hidden="true">
                    <HardDrive size={13} />
                  </span>
                  <span>All</span>
                  <span class="sandboxes-filter-count">{counts().all}</span>
                </button>
                <For each={getBackends().filter((b) => b.name !== "local")}>
                  {(backend) => (
                    <button
                      class={`sandboxes-filter-chip sandboxes-filter-chip-${backend.name} ${filter() === backend.name ? "active" : ""}`}
                      type="button"
                      role="tab"
                      aria-selected={filter() === backend.name}
                      onClick={() => void handleBackendChange(backend.name as SandboxBackendKey)}
                    >
                      <span class="sandboxes-filter-icon" aria-hidden="true">
                        {getBackendIcon(backend.name)()}
                      </span>
                      <span>{backend.title}</span>
                      <span class="sandboxes-filter-count">
                        {counts()[backend.name] || 0}
                      </span>
                    </button>
                  )}
                </For>
              </div>
              <div class="sandboxes-toolbar-right">
                <label class="sandboxes-toggle" title="Include sandboxes not attached to any thread">
                  <input
                    type="checkbox"
                    checked={includeAll()}
                    onChange={(event) =>
                      void handleIncludeAllToggle(event.currentTarget.checked)
                    }
                  />
                  <span>Show all on cloud</span>
                </label>
                <div class="sandboxes-search">
                  <Search size={13} aria-hidden="true" />
                  <input
                    type="search"
                    placeholder="Search id, repo, thread…"
                    value={search()}
                    onInput={(event) => setSearch(event.currentTarget.value)}
                  />
                </div>
                <button
                  class="btn btn-sm"
                  type="button"
                  onClick={() => void refresh()}
                  disabled={busy()}
                >
                  <RefreshCw size={13} class={busy() ? "spinner-animate" : ""} />
                  Refresh
                </button>
              </div>
            </div>

            <div class="sandboxes-statusbar" role="tablist" aria-label="Filter by status">
              <For
                each={
                  [
                    { value: "all", label: "All" },
                    { value: "active", label: "Running" },
                    { value: "stopped", label: "Stopped" },
                    { value: "archived", label: "Archived" },
                    { value: "destroyed", label: "Destroyed" },
                  ] as { value: StatusFilter; label: string }[]
                }
              >
                {(item) => (
                  <button
                    class={`sandboxes-status-chip ${statusFilter() === item.value ? "active" : ""}`}
                    type="button"
                    role="tab"
                    aria-selected={statusFilter() === item.value}
                    onClick={() => setStatusFilter(item.value)}
                  >
                    {item.label}
                  </button>
                )}
              </For>
            </div>

            <Show
              when={hasSandboxes()}
              fallback={
                <div class="sandboxes-empty">
                  <Show
                    when={busy()}
                    fallback={
                      <Show
                        when={error()}
                        fallback={
                          <div class="empty">
                            <div class="sandboxes-empty-icon" aria-hidden="true">
                              <CloudCog size={20} />
                            </div>
                            <div class="sandboxes-empty-title">No sandboxes found</div>
                            <div class="sandboxes-empty-hint">
                              <Show
                                when={includeAll()}
                                fallback={
                                  <>
                                    Attach a Daytona or Modal sandbox to a chat
                                    thread, or toggle <strong>Show all on cloud</strong>
                                    {" "}to scan the cloud provider.
                                  </>
                                }
                              >
                                No sandboxes are visible on the selected
                                provider. Create one from the chat composer to
                                see it here.
                              </Show>
                            </div>
                          </div>
                        }
                      >
                        <div class="empty">
                          <div class="sandboxes-empty-title">Could not load sandboxes</div>
                          <div class="sandboxes-empty-hint">{error()}</div>
                        </div>
                      </Show>
                    }
                  >
                    <div class="empty">Loading sandboxes…</div>
                  </Show>
                </div>
              }
            >
              <Show
                when={visibleRows().length > 0}
                fallback={
                  <div class="empty" style={{ padding: "22px" }}>
                    No sandboxes match the current filters.
                  </div>
                }
              >
                <div class="sandboxes-table-wrap">
                  <table class="sandboxes-table">
                    <thead>
                      <tr>
                        <th>Backend</th>
                        <th>Sandbox</th>
                        <th>Status</th>
                        <th>Repository</th>
                        <th>Last used</th>
                        <th class="sandboxes-actions-col">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      <For each={visibleRows()}>
                        {(row) => <SandboxRowView row={row} busyAction={actionBusy()} onStop={handleStop} onArchive={handleArchive} onDelete={confirmDelete} />}
                      </For>
                    </tbody>
                  </table>
                </div>
              </Show>
            </Show>
          </div>
        )}
      </DataGate>

      <Dialog
        open={deleteTarget() !== null}
        title="Delete sandbox"
        onClose={cancelDelete}
        footer={
          <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "8px" }}>
            <button
              class="btn"
              type="button"
              onClick={cancelDelete}
              disabled={deleting()}
            >
              Cancel
            </button>
            <button
              class="btn btn-danger"
              type="button"
              onClick={() => void performDelete()}
              disabled={deleting()}
            >
              <Trash2 size={14} />
              {deleting() ? "Deleting…" : "Delete sandbox"}
            </button>
          </div>
        }
      >
        <p>
          Permanently delete the{" "}
          <strong>{deleteTarget()?.backend}</strong> sandbox{" "}
          <span class="mono">{sandboxDisplayId(deleteTarget()?.sandbox_id || "")}</span>?
        </p>
        <p class="muted" style={{ "margin-top": "8px" }}>
          The cloud resource will be terminated and any chat threads that
          reference it will be detached. This action cannot be undone.
        </p>
      </Dialog>
    </SettingsLayout>
  );
}

function SandboxRowView(props: {
  row: SandboxRow;
  busyAction: Record<string, string>;
  onStop: (row: SandboxRow) => void;
  onArchive: (row: SandboxRow) => void;
  onDelete: (row: SandboxRow) => void;
}) {
  const isTerminal = () => TERMINAL_STATUSES.includes(props.row.status);
  const isStopped = () => props.row.status === "stopped";
  const isArchived = () => props.row.status === "archived";
  const canStop = () => props.row.backend === "daytona" && !isTerminal() && !isStopped() && !isArchived();
  const canArchive = () => props.row.backend === "daytona" && !isTerminal() && !isArchived();
  const canDelete = () => !isTerminal() || props.row.on_cloud;
  const threadHref = () => {
    if (!props.row.thread_id || !props.row.thread_alive) {
      return null;
    }
    return chatThreadHref(props.row.thread_id);
  };
  const rowKey = () => `${props.row.backend}:${props.row.sandbox_id}`;
  const currentAction = () => props.busyAction[rowKey()];
  const stopBusy = () => currentAction() === "stop";
  const archiveBusy = () => currentAction() === "archive";

  return (
    <tr class={`sandbox-row sandbox-row-${props.row.backend}`}>
      <td>
        <div class="sandbox-backend-cell">
          <span class="sandbox-backend-icon" data-brand={props.row.backend}>
            {backendIcon(props.row.backend)}
          </span>
          <span class="sandbox-backend-name">
            {props.row.backend === "daytona" ? "Daytona" : "Modal"}
          </span>
        </div>
      </td>
      <td>
        <div class="sandbox-id-cell">
          <span class="mono sandbox-id" title={props.row.sandbox_id}>
            {sandboxDisplayId(props.row.sandbox_id)}
          </span>
          <Show when={props.row.label && props.row.label !== props.row.sandbox_id}>
            <span class="sandbox-label muted" title={props.row.label}>
              {truncateText(props.row.label, 36)}
            </span>
          </Show>
          <Show when={!props.row.on_cloud}>
            <span class="badge badge-muted" title="Sandbox no longer exists on the cloud provider">
              <PowerOff size={10} /> off-cloud
            </span>
          </Show>
        </div>
      </td>
      <td>{statusPill(props.row.status)}</td>
      <td>
        <Show
          when={props.row.repository_full_name}
          fallback={<span class="muted">—</span>}
        >
          {(repo) => <span class="sandbox-repo mono">{repo()}</span>}
        </Show>
      </td>
      <td>
        <span title={props.row.last_used_at || ""}>
          {formatRelativeTime(props.row.last_used_at)}
        </span>
      </td>
      <td>
        <div class="sandbox-actions">
          <Show when={threadHref()}>
            {(href) => (
              <A
                class="btn btn-sm sandbox-action"
                href={href()}
                title="Open the chat thread that uses this sandbox"
              >
                <ExternalLink size={12} />
                Chat
              </A>
            )}
          </Show>
          <Show when={canStop()}>
            <button
              class="btn btn-sm sandbox-action"
              type="button"
              disabled={stopBusy()}
              onClick={() => props.onStop(props.row)}
              title="Stop the sandbox (Daytona only)"
            >
              <CircleStop size={12} />
              {stopBusy() ? "Stopping…" : "Stop"}
            </button>
          </Show>
          <Show when={canArchive()}>
            <button
              class="btn btn-sm sandbox-action"
              type="button"
              disabled={archiveBusy()}
              onClick={() => props.onArchive(props.row)}
              title="Archive the sandbox (Daytona only)"
            >
              <Archive size={12} />
              {archiveBusy() ? "Archiving…" : "Archive"}
            </button>
          </Show>
          <button
            class="btn btn-sm btn-danger sandbox-action"
            type="button"
            onClick={() => props.onDelete(props.row)}
            title="Terminate the sandbox and detach threads"
            disabled={!canDelete()}
          >
            <Trash2 size={12} />
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}
