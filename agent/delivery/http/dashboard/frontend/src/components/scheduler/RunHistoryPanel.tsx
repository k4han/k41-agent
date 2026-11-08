import { A } from "@solidjs/router";
import { createEffect, createSignal, For, on, onCleanup, Show } from "solid-js";
import { ExternalLink, Eye, History, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { Markdown } from "@/components/Markdown";
import { SelectControl } from "@/components/SelectControl";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { RunningPill } from "@/components/scheduler/RunningNowPanel";
import { apiFetch, deleteJson } from "@/lib/api";
import { chatThreadHref } from "@/lib/chatThreads";
import { API_PATHS } from "@/lib/endpoints";
import { truncateText } from "@/lib/utils";
import type { SchedulerJob, SchedulerRun, SchedulerRunsPayload } from "@/types";

const PAGE_SIZE = 25;
const POLL_INTERVAL_MS = 5000;
const IDLE_POLL_EVERY_TICKS = 6;
const ALL_JOBS = "";

export function formatRunTime(value: string | null): string {
  if (!value) return "-";
  return value.slice(0, 19).replace("T", " ");
}

export function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return "-";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

export function modelLabel(provider: string, model: string): string {
  if (!model) return provider || "-";
  return provider ? `${provider}/${model}` : model;
}

/** Models recorded by usage tracking win; the configured model is the fallback. */
export function runModels(run: SchedulerRun): { labels: string[]; fromUsage: boolean } {
  if (run.models_used.length > 0) {
    return { labels: run.models_used.map((item) => modelLabel(item.provider, item.model)), fromUsage: true };
  }
  return { labels: run.model || run.provider ? [modelLabel(run.provider, run.model)] : [], fromUsage: false };
}

function RunStatus(props: { status: string }) {
  return (
    <Show when={props.status === "running"} fallback={<StatusBadge status={props.status} />}>
      <RunningPill />
    </Show>
  );
}

export function RunHistoryPanel(props: {
  jobs: SchedulerJob[];
  jobFilter: string;
  onJobFilterChange: (jobId: string) => void;
  refreshToken: number;
}) {
  const [runs, setRuns] = createSignal<SchedulerRun[]>([]);
  const [total, setTotal] = createSignal(0);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [selected, setSelected] = createSignal<SchedulerRun | null>(null);
  const [deleteTarget, setDeleteTarget] = createSignal<SchedulerRun | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [confirmClear, setConfirmClear] = createSignal(false);
  const { showToast } = useToast();
  let loadVersion = 0;

  const load = async (mode: "reset" | "more" | "refresh" = "reset") => {
    const version = ++loadVersion;
    setLoading(true);
    const current = runs();
    const offset = mode === "more" ? current.length : 0;
    const limit = mode === "refresh" ? Math.max(PAGE_SIZE, current.length) : PAGE_SIZE;
    try {
      const payload = await apiFetch<SchedulerRunsPayload>(
        API_PATHS.schedulerRuns({ jobId: props.jobFilter || undefined, limit, offset }),
      );
      if (version !== loadVersion) return;
      setRuns(mode === "more" ? [...current, ...payload.runs] : payload.runs);
      setTotal(payload.total);
      setError("");
      const open = selected();
      if (open) {
        setSelected(payload.runs.find((run) => run.run_id === open.run_id) || open);
      }
    } catch (err) {
      if (version === loadVersion) setError(err instanceof Error ? err.message : "Failed to load run history");
    } finally {
      if (version === loadVersion) setLoading(false);
    }
  };

  createEffect(on(() => [props.jobFilter, props.refreshToken], () => void load("reset")));

  let tick = 0;
  const timer = setInterval(() => {
    tick += 1;
    const hasRunning = runs().some((run) => run.status === "running");
    if ((hasRunning || tick % IDLE_POLL_EVERY_TICKS === 0) && !loading()) {
      void load("refresh");
    }
  }, POLL_INTERVAL_MS);
  onCleanup(() => clearInterval(timer));

  const clearHistory = async () => {
    if (deleting()) return;
    setDeleting(true);
    try {
      await deleteJson(API_PATHS.schedulerRuns({ jobId: props.jobFilter || undefined }));
      showToast("Run history cleared.");
      setConfirmClear(false);
      await load("reset");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clear run history", "error");
    } finally {
      setDeleting(false);
    }
  };

  const deleteRun = async () => {
    const run = deleteTarget();
    if (!run || deleting()) return;
    setDeleting(true);
    try {
      await deleteJson(API_PATHS.schedulerRun(run.run_id));
      setRuns((current) => current.filter((item) => item.run_id !== run.run_id));
      setTotal((current) => Math.max(0, current - 1));
      if (selected()?.run_id === run.run_id) setSelected(null);
      setDeleteTarget(null);
      showToast("Run deleted.");
      await load("refresh");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete run", "error");
    } finally {
      setDeleting(false);
    }
  };

  const jobOptions = () => [
    { value: ALL_JOBS, label: "All jobs" },
    ...props.jobs.map((job) => ({ value: job.id, label: truncateText(job.task, 48) })),
    ...(props.jobFilter && !props.jobs.some((job) => job.id === props.jobFilter)
      ? [{ value: props.jobFilter, label: `Deleted job ${props.jobFilter.slice(0, 8)}` }]
      : []),
  ];

  return (
    <section class="panel" id="scheduler-run-history">
      <div class="panel-header split">
        <div class="panel-title scheduler-panel-title">
          <History size={15} /> Run History
          <span class="hint">{total()} run{total() === 1 ? "" : "s"}</span>
        </div>
        <div class="row-wrap scheduler-history-actions">
          <SelectControl
            value={props.jobFilter}
            options={jobOptions()}
            onChange={(value) => props.onJobFilterChange(value)}
            ariaLabel="Filter run history by job"
            align="right"
          />
          <button class="btn btn-secondary btn-sm" type="button" disabled={loading()} onClick={() => void load("refresh")}>
            <RefreshCw size={13} class={loading() ? "spinner-animate" : ""} /> Refresh
          </button>
          <button
            class="btn btn-sm btn-danger"
            type="button"
            disabled={deleting() || runs().length === 0}
            onClick={() => setConfirmClear(true)}
          >
            <Trash2 size={13} /> Clear
          </button>
        </div>
      </div>
      <Show when={error()}>
        <div class="panel-body">
          <p class="scheduler-run-error-text">{error()}</p>
        </div>
      </Show>
      <DashboardTable
        tableClass="scheduler-runs-table"
        columns={[
          { header: "Started" },
          { header: "Task" },
          { header: "Status" },
          { header: "Model" },
          { header: "Duration" },
          { header: "" },
        ]}
        rows={runs()}
        emptyMessage="No runs yet. Runs appear here after a job fires or you press Run."
      >
        {(run) => {
          const models = () => runModels(run);
          return (
            <tr>
              <td class="mono">{formatRunTime(run.started_at)}</td>
              <td>
                <div>{truncateText(run.task, 120)}</div>
                <div class="row-wrap scheduler-run-meta-line">
                  <span class="chip">{run.trigger_source === "manual" ? "manual" : "scheduled"}</span>
                  <span class="badge">{run.platform}</span>
                  <span class="mono hint">{run.user_id}</span>
                </div>
              </td>
              <td><RunStatus status={run.status} /></td>
              <td>
                <Show when={models().labels.length > 0} fallback={<span class="hint">-</span>}>
                  <For each={models().labels}>{(label) => <div class="mono">{label}</div>}</For>
                  <div class="hint">
                    {models().fromUsage
                      ? `${run.total_tokens.toLocaleString()} tokens`
                      : run.status === "running" ? "configured" : "configured (no usage recorded)"}
                  </div>
                </Show>
              </td>
              <td>{run.status === "running" ? <span class="hint">running…</span> : formatDuration(run.duration_ms)}</td>
              <td>
                <div class="row-wrap">
                  <button class="btn btn-sm" type="button" onClick={() => setSelected(run)}>
                    <Eye size={13} /> View
                  </button>
                  <button
                    class="btn btn-sm btn-danger"
                    type="button"
                    title={run.status === "running" ? "Running executions cannot be deleted" : "Delete run"}
                    aria-label="Delete run"
                    disabled={run.status === "running" || deleting()}
                    onClick={() => setDeleteTarget(run)}
                  >
                    <Trash2 size={13} /> Delete
                  </button>
                </div>
              </td>
            </tr>
          );
        }}
      </DashboardTable>
      <Show when={runs().length < total()}>
        <div class="panel-body row-wrap">
          <button class="btn btn-sm" type="button" disabled={loading()} onClick={() => void load("more")}>
            Load more ({total() - runs().length} remaining)
          </button>
        </div>
      </Show>

      <Dialog
        open={selected() !== null}
        title="Run Details"
        subtitle={selected() ? formatRunTime(selected()!.started_at) : undefined}
        size="lg"
        onClose={() => setSelected(null)}
        footer={
          <>
            <Show when={selected()?.thread_id}>
              {(threadId) => (
                <A class="btn" href={chatThreadHref(threadId())}>
                  <ExternalLink size={13} /> Open thread
                </A>
              )}
            </Show>
            <button class="btn" type="button" onClick={() => setSelected(null)}>
              Close
            </button>
          </>
        }
      >
        <Show when={selected()}>
          {(run) => (
            <div class="stack">
              <dl class="scheduler-run-details">
                <dt>Status</dt>
                <dd><RunStatus status={run().status} /></dd>
                <dt>Trigger</dt>
                <dd>{run().trigger_source === "manual" ? "Manual run" : "Schedule"}</dd>
                <dt>Agent</dt>
                <dd class="mono">{run().agent_name}</dd>
                <dt>Configured model</dt>
                <dd class="mono">{modelLabel(run().provider, run().model)}</dd>
                <dt>Models used</dt>
                <dd>
                  <Show when={run().models_used.length > 0} fallback={<span class="hint">No usage recorded</span>}>
                    <For each={run().models_used}>
                      {(item) => (
                        <div>
                          <span class="mono">{modelLabel(item.provider, item.model)}</span>{" "}
                          <span class="hint">
                            {item.calls} call{item.calls === 1 ? "" : "s"} · {item.total_tokens.toLocaleString()} tokens
                          </span>
                        </div>
                      )}
                    </For>
                  </Show>
                </dd>
                <dt>Duration</dt>
                <dd>{formatDuration(run().duration_ms)}</dd>
                <dt>Finished</dt>
                <dd class="mono">{formatRunTime(run().finished_at)}</dd>
                <dt>Target</dt>
                <dd><span class="badge">{run().platform}</span> <span class="mono">{run().user_id}</span></dd>
                <dt>Job ID</dt>
                <dd class="mono">{run().job_id || "-"}</dd>
              </dl>
              <div class="field">
                <label>Task</label>
                <div class="scheduler-run-text">{run().task}</div>
              </div>
              <Show when={run().error}>
                <div class="field">
                  <label>Error</label>
                  <pre class="scheduler-run-text scheduler-run-error-text">{run().error}</pre>
                </div>
              </Show>
              <Show when={run().response}>
                <div class="field">
                  <label>Result</label>
                  <div class="scheduler-run-text scheduler-run-markdown">
                    <Markdown text={run().response} />
                  </div>
                </div>
              </Show>
            </div>
          )}
        </Show>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget() !== null}
        title="Delete Run"
        message={
          <p>
            Delete the run of <strong>{truncateText(deleteTarget()?.task || "", 120)}</strong>
            {" from "}{formatRunTime(deleteTarget()?.started_at ?? null)}?
          </p>
        }
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting()}
        onClose={() => { if (!deleting()) setDeleteTarget(null); }}
        onConfirm={() => void deleteRun()}
      />

      <ConfirmDialog
        open={confirmClear()}
        title="Clear Run History"
        message={
          <p>
            {props.jobFilter
              ? "Delete all finished runs of this job?"
              : "Delete all finished runs of every job?"}
          </p>
        }
        confirmLabel="Clear"
        confirmVariant="danger"
        loading={deleting()}
        onClose={() => { if (!deleting()) setConfirmClear(false); }}
        onConfirm={() => void clearHistory()}
      />
    </section>
  );
}
