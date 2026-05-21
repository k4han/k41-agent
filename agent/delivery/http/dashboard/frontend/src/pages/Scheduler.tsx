import { createSignal, For, onMount, Show } from "solid-js";
import { Edit3, Play, Square, Trash2 } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { Dialog } from "@/components/Dialog";
import { DataGate } from "@/components/State";
import { MetricCard, MetricsRow } from "@/components/Metrics";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { dateTimeLocal, triggerArgsFromDateInput } from "@/lib/utils";
import type { Identity, SchedulerJob } from "@/types";

type SchedulerPayload = {
  jobs: SchedulerJob[];
  identities: Identity[];
  scheduler_timezone: string;
};

type TriggerType = "date" | "relative" | "interval" | "cron";

type ScheduleForm = {
  task: string;
  identity: string;
  platform: string;
  user_id: string;
  trigger_type: TriggerType;
  run_date: string;
  weeks: string;
  days: string;
  hours: string;
  minutes: string;
  seconds: string;
  cron_minute: string;
  cron_hour: string;
  cron_day: string;
  cron_month: string;
  cron_day_of_week: string;
};

const defaultScheduleForm = (): ScheduleForm => {
  const date = new Date(Date.now() + 15 * 60 * 1000);
  return {
    task: "",
    identity: "",
    platform: "telegram",
    user_id: "",
    trigger_type: "date",
    run_date: dateTimeLocal(date),
    weeks: "",
    days: "",
    hours: "",
    minutes: "",
    seconds: "",
    cron_minute: "0",
    cron_hour: "9",
    cron_day: "*",
    cron_month: "*",
    cron_day_of_week: "*",
  };
};

function durationArgs(form: ScheduleForm) {
  const args: Record<string, number> = {};
  for (const key of ["weeks", "days", "hours", "minutes", "seconds"] as const) {
    const raw = form[key].trim();
    if (!raw) {
      continue;
    }
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      throw new Error("Duration values must be non-negative numbers.");
    }
    if (value > 0) {
      args[key] = value;
    }
  }
  if (Object.keys(args).length === 0) {
    throw new Error("Specify at least one duration value.");
  }
  return args;
}

function triggerArgs(form: ScheduleForm) {
  if (form.trigger_type === "date") {
    const args = triggerArgsFromDateInput(form.run_date);
    if (!args) {
      throw new Error("Select a run date.");
    }
    return args;
  }
  if (form.trigger_type === "relative" || form.trigger_type === "interval") {
    return durationArgs(form);
  }
  const args: Record<string, string> = {};
  const cronFields = {
    minute: form.cron_minute,
    hour: form.cron_hour,
    day: form.cron_day,
    month: form.cron_month,
    day_of_week: form.cron_day_of_week,
  };
  for (const [key, value] of Object.entries(cronFields)) {
    const normalized = value.trim();
    if (normalized && normalized !== "*") {
      args[key] = normalized;
    }
  }
  return args;
}

function identityFromForm(form: ScheduleForm): { platform: string; user_id: string } {
  if (form.identity && form.identity !== "__manual__") {
    const [platform, ...rest] = form.identity.split(":");
    return { platform, user_id: rest.join("::") };
  }
  if (!form.user_id.trim()) {
    throw new Error("Enter a user ID.");
  }
  return { platform: form.platform, user_id: form.user_id.trim() };
}

function DurationInputs(props: {
  form: ScheduleForm;
  setField: <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => void;
}) {
  return (
    <div class="grid-3">
      <For each={["weeks", "days", "hours", "minutes", "seconds"] as const}>
        {(field) => (
          <div class="field">
            <label>{field}</label>
            <input
              class="input"
              type="number"
              min="0"
              value={props.form[field]}
              onInput={(event) => props.setField(field, event.currentTarget.value)}
            />
          </div>
        )}
      </For>
    </div>
  );
}

function TriggerFields(props: {
  form: ScheduleForm;
  setField: <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => void;
}) {
  return (
    <>
      <Show when={props.form.trigger_type === "date"}>
        <div class="field">
          <label>Run at</label>
          <input
            class="input"
            type="datetime-local"
            value={props.form.run_date}
            onInput={(event) => props.setField("run_date", event.currentTarget.value)}
          />
        </div>
      </Show>
      <Show when={props.form.trigger_type === "relative" || props.form.trigger_type === "interval"}>
        <DurationInputs form={props.form} setField={props.setField} />
      </Show>
      <Show when={props.form.trigger_type === "cron"}>
        <div class="grid-3">
          <div class="field">
            <label>Minute</label>
            <input class="input" value={props.form.cron_minute} onInput={(event) => props.setField("cron_minute", event.currentTarget.value)} />
          </div>
          <div class="field">
            <label>Hour</label>
            <input class="input" value={props.form.cron_hour} onInput={(event) => props.setField("cron_hour", event.currentTarget.value)} />
          </div>
          <div class="field">
            <label>Day</label>
            <input class="input" value={props.form.cron_day} onInput={(event) => props.setField("cron_day", event.currentTarget.value)} />
          </div>
          <div class="field">
            <label>Month</label>
            <input class="input" value={props.form.cron_month} onInput={(event) => props.setField("cron_month", event.currentTarget.value)} />
          </div>
          <div class="field">
            <label>Day of Week</label>
            <input class="input" value={props.form.cron_day_of_week} onInput={(event) => props.setField("cron_day_of_week", event.currentTarget.value)} />
          </div>
        </div>
      </Show>
    </>
  );
}

export function SchedulerPage() {
  const [data, setData] = createSignal<SchedulerPayload>();
  const [error, setError] = createSignal("");
  const [form, setForm] = createSignal<ScheduleForm>(defaultScheduleForm());
  const [editJob, setEditJob] = createSignal<SchedulerJob | null>(null);
  const [editForm, setEditForm] = createSignal<ScheduleForm>(defaultScheduleForm());
  const { showToast } = useToast();

  const setField = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };
  const setEditField = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => {
    setEditForm((current) => ({ ...current, [key]: value }));
  };

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<SchedulerPayload>("/dashboard-api/scheduler");
      setData(payload);
      setForm((current) => ({
        ...current,
        identity: current.identity || (payload.identities[0] ? `${payload.identities[0].platform}:${payload.identities[0].external_id}` : "__manual__"),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load scheduler");
    }
  };

  const createJob = async () => {
    try {
      const current = form();
      const identity = identityFromForm(current);
      await postJson("/scheduler/jobs", {
        task: current.task,
        platform: identity.platform,
        user_id: identity.user_id,
        trigger_type: current.trigger_type,
        trigger_args: triggerArgs(current),
      });
      showToast("Job created.");
      setForm(defaultScheduleForm());
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create job", "error");
    }
  };

  const openEdit = (job: SchedulerJob) => {
    setEditJob(job);
    const args = job.trigger_args || {};
    const identityKey = `${job.platform}:${job.user_id}`;
    const matchedIdentity = data()?.identities.find(
      (id) => `${id.platform}:${id.external_id}` === identityKey
    );
    const identity = matchedIdentity ? `${matchedIdentity.platform}:${matchedIdentity.external_id}` : "__manual__";

    setEditForm({
      task: job.task,
      identity,
      platform: job.platform,
      user_id: job.user_id,
      trigger_type: (job.trigger_type || "date") as TriggerType,
      run_date: (args.run_date as string) || dateTimeLocal(new Date()),
      weeks: String(args.weeks ?? ""),
      days: String(args.days ?? ""),
      hours: String(args.hours ?? ""),
      minutes: String(args.minutes ?? ""),
      seconds: String(args.seconds ?? ""),
      cron_minute: (args.minute as string) || "*",
      cron_hour: (args.hour as string) || "*",
      cron_day: (args.day as string) || "*",
      cron_month: (args.month as string) || "*",
      cron_day_of_week: (args.day_of_week as string) || "*",
    });
  };

  const updateJob = async () => {
    const job = editJob();
    if (!job) {
      return;
    }
    try {
      const current = editForm();
      const identity = identityFromForm(current);
      await putJson(`/scheduler/jobs/${encodeURIComponent(job.id)}`, {
        task: current.task,
        platform: identity.platform,
        user_id: identity.user_id,
        trigger_type: current.trigger_type,
        trigger_args: triggerArgs(current),
      });
      showToast("Job updated.");
      setEditJob(null);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to update job", "error");
    }
  };

  const jobAction = async (job: SchedulerJob, action: "run" | "pause" | "resume" | "delete") => {
    try {
      if (action === "delete") {
        if (!window.confirm(`Delete job ${job.id}?`)) {
          return;
        }
        await deleteJson(`/scheduler/jobs/${encodeURIComponent(job.id)}`);
      } else {
        await postJson(`/scheduler/jobs/${encodeURIComponent(job.id)}/${action}`);
      }
      showToast(`Job ${action} requested.`);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Job action failed", "error");
    }
  };

  onMount(load);

  return (
    <AppShell
      title="Scheduled Tasks"
      subtitle="Create recurring and one-off channel tasks."
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => {
          const active = payload.jobs.filter((job) => !job.paused).length;
          const paused = payload.jobs.filter((job) => job.paused).length;
          return (
            <div class="stack">
              <MetricsRow>
                <MetricCard value={payload.jobs.length} label="Total jobs" />
                <MetricCard value={active} label="Active" />
                <MetricCard value={paused} label="Paused" />
              </MetricsRow>

              <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">Create Scheduled Job</div>
                  <span class="hint">{payload.scheduler_timezone}</span>
                </div>
                <div class="panel-body stack">
                  <div class="field">
                    <label>Task</label>
                    <textarea class="textarea" value={form().task} onInput={(event) => setField("task", event.currentTarget.value)} />
                  </div>
                  <div class="grid-2">
                    <div class="field">
                      <label>Target User</label>
                      <select class="select" value={form().identity} onChange={(event) => setField("identity", event.currentTarget.value)}>
                        <For each={payload.identities}>
                          {(identity) => <option value={`${identity.platform}:${identity.external_id}`}>{identity.platform} - {identity.external_id}</option>}
                        </For>
                        <option value="__manual__">Enter manually</option>
                      </select>
                    </div>
                    <div class="field">
                      <label>Schedule</label>
                      <select class="select" value={form().trigger_type} onChange={(event) => setField("trigger_type", event.currentTarget.value as TriggerType)}>
                        <option value="date">Run once at a time</option>
                        <option value="relative">Run once after a delay</option>
                        <option value="interval">Run every interval</option>
                        <option value="cron">Cron schedule</option>
                      </select>
                    </div>
                  </div>
                  <Show when={form().identity === "__manual__"}>
                    <div class="grid-2">
                      <div class="field">
                        <label>Platform</label>
                        <select class="select" value={form().platform} onChange={(event) => setField("platform", event.currentTarget.value)}>
                          <option value="telegram">telegram</option>
                          <option value="discord">discord</option>
                        </select>
                      </div>
                      <div class="field">
                        <label>User ID</label>
                        <input class="input" value={form().user_id} onInput={(event) => setField("user_id", event.currentTarget.value)} />
                      </div>
                    </div>
                  </Show>
                  <TriggerFields form={form()} setField={setField} />
                  <div class="row-wrap">
                    <button class="btn btn-primary" type="button" onClick={createJob}>
                      <Play size={14} />
                      Create Job
                    </button>
                  </div>
                </div>
              </section>

              <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">All Scheduled Jobs</div>
                </div>
                <div class="table-wrap">
                  <table class="table">
                    <thead>
                      <tr>
                        <th>Job</th>
                        <th>Target</th>
                        <th>Trigger</th>
                        <th>Next Run</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      <For
                        each={payload.jobs}
                        fallback={
                          <tr>
                            <td colSpan={6}>
                              <div class="empty">No scheduled jobs.</div>
                            </td>
                          </tr>
                        }
                      >
                        {(job) => (
                          <tr>
                            <td>
                              <div>{job.task}</div>
                              <div class="mono hint">{job.id}</div>
                            </td>
                            <td>
                              <span class="badge">{job.platform}</span>
                              <div class="mono hint">{job.user_id}</div>
                            </td>
                            <td><span class="chip">{job.trigger_type}</span></td>
                            <td>{job.next_run_time || "-"}</td>
                            <td><StatusBadge status={job.paused ? "paused" : "active"} /></td>
                            <td>
                              <div class="row-wrap">
                                <button class="btn btn-sm" type="button" onClick={() => openEdit(job)}>
                                  <Edit3 size={13} />
                                  Edit
                                </button>
                                <button class="btn btn-sm" type="button" onClick={() => jobAction(job, "run")}>
                                  <Play size={13} />
                                  Run
                                </button>
                                {job.paused ? (
                                  <button class="btn btn-sm" type="button" onClick={() => jobAction(job, "resume")}>
                                    <Play size={13} />
                                    Resume
                                  </button>
                                ) : (
                                  <button class="btn btn-sm btn-warning" type="button" onClick={() => jobAction(job, "pause")}>
                                    <Square size={13} />
                                    Pause
                                  </button>
                                )}
                                <button class="btn btn-sm btn-danger" type="button" onClick={() => jobAction(job, "delete")}>
                                  <Trash2 size={13} />
                                  Delete
                                </button>
                              </div>
                            </td>
                          </tr>
                        )}
                      </For>
                    </tbody>
                  </table>
                </div>
              </section>

              <Dialog
                open={Boolean(editJob())}
                title={`Edit ${editJob()?.id || "job"}`}
                onClose={() => setEditJob(null)}
                footer={
                  <>
                    <button class="btn" type="button" onClick={() => setEditJob(null)}>
                      Close
                    </button>
                    <button class="btn btn-primary" type="button" onClick={updateJob}>
                      Save Changes
                    </button>
                  </>
                }
              >
                <div class="stack">
                  <div class="field">
                    <label>Task</label>
                    <textarea class="textarea" value={editForm().task} onInput={(event) => setEditField("task", event.currentTarget.value)} />
                  </div>
                  <div class="grid-2">
                    <div class="field">
                      <label>Target User</label>
                      <select class="select" value={editForm().identity} onChange={(event) => setEditField("identity", event.currentTarget.value)}>
                        <For each={data()?.identities || []}>
                          {(identity) => <option value={`${identity.platform}:${identity.external_id}`}>{identity.platform} - {identity.external_id}</option>}
                        </For>
                        <option value="__manual__">Enter manually</option>
                      </select>
                    </div>
                    <div class="field">
                      <label>Schedule</label>
                      <select class="select" value={editForm().trigger_type} onChange={(event) => setEditField("trigger_type", event.currentTarget.value as TriggerType)}>
                        <option value="date">Run once at a time</option>
                        <option value="relative">Run once after a delay</option>
                        <option value="interval">Run every interval</option>
                        <option value="cron">Cron schedule</option>
                      </select>
                    </div>
                  </div>
                  <Show when={editForm().identity === "__manual__"}>
                    <div class="grid-2">
                      <div class="field">
                        <label>Platform</label>
                        <select class="select" value={editForm().platform} onChange={(event) => setEditField("platform", event.currentTarget.value)}>
                          <option value="telegram">telegram</option>
                          <option value="discord">discord</option>
                        </select>
                      </div>
                      <div class="field">
                        <label>User ID</label>
                        <input class="input" value={editForm().user_id} onInput={(event) => setEditField("user_id", event.currentTarget.value)} />
                      </div>
                    </div>
                  </Show>
                  <TriggerFields form={editForm()} setField={setEditField} />
                </div>
              </Dialog>
            </div>
          );
        }}
      </DataGate>
    </AppShell>
  );
}
