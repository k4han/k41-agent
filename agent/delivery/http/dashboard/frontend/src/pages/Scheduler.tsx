import { createSignal, For, Show } from "solid-js";
import { Clock, Edit3, Play, Square, Trash2 } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getPlatforms, getTriggerTypes } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { dateTimeInTimeZone, triggerArgsFromDateInput } from "@/lib/utils";
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

const defaultScheduleForm = (timeZone?: string): ScheduleForm => {
  const date = new Date(Date.now() + 15 * 60 * 1000);
  return {
    task: "",
    identity: "",
    platform: "telegram",
    user_id: "",
    trigger_type: "date",
    run_date: dateTimeInTimeZone(date, timeZone),
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

export type CronPresetId = "hourly" | "daily" | "weekdays" | "weekly" | "monthly" | "custom";

export interface CronPreset {
  id: CronPresetId;
  label: string;
  expression: string;
  description: string;
  fields: {
    minute: string;
    hour: string;
    day: string;
    month: string;
    day_of_week: string;
  };
}

export const CRON_PRESETS: readonly CronPreset[] = [
  {
    id: "hourly",
    label: "Hourly",
    expression: "0 * * * *",
    description: "At the start of every hour",
    fields: { minute: "0", hour: "*", day: "*", month: "*", day_of_week: "*" },
  },
  {
    id: "daily",
    label: "Daily",
    expression: "0 9 * * *",
    description: "Every day at 09:00 AM",
    fields: { minute: "0", hour: "9", day: "*", month: "*", day_of_week: "*" },
  },
  {
    id: "weekdays",
    label: "Weekdays",
    expression: "0 9 * * 1-5",
    description: "Every weekday at 09:00 AM",
    fields: { minute: "0", hour: "9", day: "*", month: "*", day_of_week: "1-5" },
  },
  {
    id: "weekly",
    label: "Weekly",
    expression: "0 9 * * 1",
    description: "Every Monday at 09:00 AM",
    fields: { minute: "0", hour: "9", day: "*", month: "*", day_of_week: "1" },
  },
  {
    id: "monthly",
    label: "Monthly",
    expression: "0 0 1 * *",
    description: "First day of every month at 12:00 AM",
    fields: { minute: "0", hour: "0", day: "1", month: "*", day_of_week: "*" },
  },
  {
    id: "custom",
    label: "Custom",
    expression: "",
    description: "Custom schedule expression",
    fields: { minute: "", hour: "", day: "", month: "", day_of_week: "" },
  },
] as const;

export function detectCronPreset(
  minute: string | number | undefined,
  hour: string | number | undefined,
  day: string | number | undefined,
  month: string | number | undefined,
  dayOfWeek: string | number | undefined
): CronPresetId {
  const norm = (s: string | number | undefined) => String(s ?? "").trim();
  const m = norm(minute);
  const h = norm(hour);
  const d = norm(day);
  const mo = norm(month);
  const dow = norm(dayOfWeek);

  if (m === "0" && h === "*" && d === "*" && mo === "*" && dow === "*") return "hourly";
  if (m === "0" && h === "9" && d === "*" && mo === "*" && dow === "*") return "daily";
  if (
    m === "0" &&
    h === "9" &&
    d === "*" &&
    mo === "*" &&
    (dow === "1-5" || dow.toLowerCase() === "mon-fri")
  ) {
    return "weekdays";
  }
  if (
    m === "0" &&
    h === "9" &&
    d === "*" &&
    mo === "*" &&
    (dow === "1" || dow.toLowerCase() === "mon")
  ) {
    return "weekly";
  }
  if (m === "0" && h === "0" && d === "1" && mo === "*" && dow === "*") return "monthly";
  return "custom";
}

export function buildCronExpression(
  minute: string | number | undefined,
  hour: string | number | undefined,
  day: string | number | undefined,
  month: string | number | undefined,
  dayOfWeek: string | number | undefined
): string {
  const norm = (v: string | number | undefined) => {
    if (v === undefined || v === null || String(v).trim() === "") return "*";
    return String(v).trim();
  };
  return `${norm(minute)} ${norm(hour)} ${norm(day)} ${norm(month)} ${norm(dayOfWeek)}`;
}

export function formatTime(hour: number | string, minute: number | string): string {
  const h = Number(hour);
  const m = Number(minute);
  if (!Number.isInteger(h) || !Number.isInteger(m) || h < 0 || h > 23 || m < 0 || m > 59) {
    return `${hour}:${minute}`;
  }
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  const padHour = String(h12).padStart(2, "0");
  const padMin = String(m).padStart(2, "0");
  return `${padHour}:${padMin} ${period}`;
}

function getOrdinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

const DOW_MAP: Record<string, string> = {
  "0": "Sunday",
  "1": "Monday",
  "2": "Tuesday",
  "3": "Wednesday",
  "4": "Thursday",
  "5": "Friday",
  "6": "Saturday",
  "7": "Sunday",
  sun: "Sunday",
  mon: "Monday",
  tue: "Tuesday",
  wed: "Wednesday",
  thu: "Thursday",
  fri: "Friday",
  sat: "Saturday",
};

const MONTH_MAP: Record<string, string> = {
  "1": "January",
  "2": "February",
  "3": "March",
  "4": "April",
  "5": "May",
  "6": "June",
  "7": "July",
  "8": "August",
  "9": "September",
  "10": "October",
  "11": "November",
  "12": "December",
};

export function formatCronDescription(
  minute: string | number | undefined,
  hour: string | number | undefined,
  day: string | number | undefined,
  month: string | number | undefined,
  dayOfWeek: string | number | undefined
): string {
  const m = (String(minute ?? "*")).trim() || "*";
  const h = (String(hour ?? "*")).trim() || "*";
  const d = (String(day ?? "*")).trim() || "*";
  const mo = (String(month ?? "*")).trim() || "*";
  const dow = (String(dayOfWeek ?? "*")).trim() || "*";

  // 1. All wildcards
  if (m === "*" && h === "*" && d === "*" && mo === "*" && dow === "*") {
    return "Runs every minute";
  }

  // 2. Step minutes e.g. */15 * * * *
  if (m.startsWith("*/") && h === "*" && d === "*" && mo === "*" && dow === "*") {
    const step = m.slice(2);
    return `Runs every ${step} minutes`;
  }

  // 3. Hourly at specific minute e.g. 0 * * * * or 30 * * * *
  if (h === "*" && d === "*" && mo === "*" && dow === "*") {
    if (m === "0") return "Runs every hour";
    const numM = Number(m);
    if (Number.isInteger(numM) && numM >= 0 && numM <= 59) {
      return `Runs every hour at minute ${numM}`;
    }
  }

  // 4. Step hours e.g. 0 */2 * * *
  if (h.startsWith("*/") && d === "*" && mo === "*" && dow === "*") {
    const step = h.slice(2);
    if (m === "0") return `Runs every ${step} hours`;
    return `Runs every ${step} hours at minute ${m}`;
  }

  // 5. Fixed hour & minute e.g. 0 9 * * *
  const isFixedH = /^\d+$/.test(h) && Number(h) >= 0 && Number(h) <= 23;
  const isFixedM = /^\d+$/.test(m) && Number(m) >= 0 && Number(m) <= 59;

  if (isFixedH && isFixedM) {
    const timeStr = formatTime(h, m);

    // 5a. Every day: 0 9 * * *
    if (d === "*" && mo === "*" && dow === "*") {
      return `Runs every day at ${timeStr}`;
    }

    // 5b. By day of week
    if (d === "*" && mo === "*" && dow !== "*") {
      const lowerDow = dow.toLowerCase();
      if (lowerDow === "1-5" || lowerDow === "mon-fri") {
        return `Runs every weekday at ${timeStr}`;
      }
      if (lowerDow === "0,6" || lowerDow === "6,0" || lowerDow === "sat,sun") {
        return `Runs every weekend at ${timeStr}`;
      }
      if (DOW_MAP[lowerDow]) {
        return `Runs every ${DOW_MAP[lowerDow]} at ${timeStr}`;
      }
      if (lowerDow.includes(",")) {
        const days = lowerDow.split(",").map((part) => DOW_MAP[part.trim()] || part.trim());
        return `Runs every ${days.join(", ")} at ${timeStr}`;
      }
      return `Runs on ${dow} at ${timeStr}`;
    }

    // 5c. Monthly: 0 0 1 * *
    if (d !== "*" && mo === "*" && dow === "*") {
      const numD = Number(d);
      if (Number.isInteger(numD) && numD >= 1 && numD <= 31) {
        return `Runs on the ${getOrdinal(numD)} of every month at ${timeStr}`;
      }
      return `Runs on day ${d} of every month at ${timeStr}`;
    }

    // 5d. Yearly on specific month and day: 0 9 1 1 *
    if (d !== "*" && mo !== "*" && dow === "*") {
      const numD = Number(d);
      const monthName = MONTH_MAP[mo] || `month ${mo}`;
      if (Number.isInteger(numD) && numD >= 1 && numD <= 31) {
        return `Runs on ${monthName} ${getOrdinal(numD)} at ${timeStr}`;
      }
      return `Runs on day ${d} of ${monthName} at ${timeStr}`;
    }
  }

  // 6. Fallback for custom / complex expressions
  return `Runs on schedule: ${m} ${h} ${d} ${mo} ${dow}`;
}

function CronScheduleEditor(props: {
  form: ScheduleForm;
  setField: <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => void;
}) {
  const activePreset = () =>
    detectCronPreset(
      props.form.cron_minute,
      props.form.cron_hour,
      props.form.cron_day,
      props.form.cron_month,
      props.form.cron_day_of_week
    );

  const cronExpression = () =>
    buildCronExpression(
      props.form.cron_minute,
      props.form.cron_hour,
      props.form.cron_day,
      props.form.cron_month,
      props.form.cron_day_of_week
    );

  const cronSummary = () =>
    formatCronDescription(
      props.form.cron_minute,
      props.form.cron_hour,
      props.form.cron_day,
      props.form.cron_month,
      props.form.cron_day_of_week
    );

  const selectPreset = (preset: CronPreset) => {
    if (preset.id === "custom") return;
    props.setField("cron_minute", preset.fields.minute);
    props.setField("cron_hour", preset.fields.hour);
    props.setField("cron_day", preset.fields.day);
    props.setField("cron_month", preset.fields.month);
    props.setField("cron_day_of_week", preset.fields.day_of_week);
  };

  return (
    <div class="cron-editor">
      <div class="cron-presets-section">
        <div class="cron-presets-header">
          <label class="cron-presets-title">Frequency Presets</label>
          <span class="hint">Select a common schedule or customize below</span>
        </div>
        <div class="cron-presets-group" role="radiogroup" aria-label="Frequency Presets">
          <For each={CRON_PRESETS}>
            {(preset) => (
              <button
                type="button"
                class={`cron-preset-pill ${activePreset() === preset.id ? "active" : ""}`}
                onClick={() => selectPreset(preset)}
                aria-checked={activePreset() === preset.id}
                role="radio"
              >
                <span class="cron-preset-label">{preset.label}</span>
                <Show when={preset.expression}>
                  <span class="cron-preset-expression mono">{preset.expression}</span>
                </Show>
              </button>
            )}
          </For>
        </div>
      </div>

      <div class="cron-summary-banner">
        <div class="cron-summary-icon-wrap">
          <Clock size={16} />
        </div>
        <div class="cron-summary-body">
          <div class="cron-summary-text">{cronSummary()}</div>
          <div class="cron-summary-meta">
            <span class="hint">Expression:</span>
            <code class="cron-summary-badge mono">{cronExpression()}</code>
          </div>
        </div>
      </div>

      <div class="cron-fields-section">
        <label class="cron-fields-title">Schedule Fields</label>
        <div class="cron-fields-grid">
          <div class="field">
            <label>Minute</label>
            <input
              class="input mono"
              value={props.form.cron_minute}
              onInput={(event) => props.setField("cron_minute", event.currentTarget.value)}
              placeholder="0-59, *"
            />
            <span class="hint">0-59</span>
          </div>
          <div class="field">
            <label>Hour</label>
            <input
              class="input mono"
              value={props.form.cron_hour}
              onInput={(event) => props.setField("cron_hour", event.currentTarget.value)}
              placeholder="0-23, *"
            />
            <span class="hint">0-23</span>
          </div>
          <div class="field">
            <label>Day of Month</label>
            <input
              class="input mono"
              value={props.form.cron_day}
              onInput={(event) => props.setField("cron_day", event.currentTarget.value)}
              placeholder="1-31, *"
            />
            <span class="hint">1-31</span>
          </div>
          <div class="field">
            <label>Month</label>
            <input
              class="input mono"
              value={props.form.cron_month}
              onInput={(event) => props.setField("cron_month", event.currentTarget.value)}
              placeholder="1-12, *"
            />
            <span class="hint">1-12</span>
          </div>
          <div class="field">
            <label>Day of Week</label>
            <input
              class="input mono"
              value={props.form.cron_day_of_week}
              onInput={(event) => props.setField("cron_day_of_week", event.currentTarget.value)}
              placeholder="0-6 or 1-5, mon-fri"
            />
            <span class="hint">0-6, 1-5</span>
          </div>
        </div>
      </div>
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
        <CronScheduleEditor form={props.form} setField={props.setField} />
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
  const [deleteTarget, setDeleteTarget] = createSignal<SchedulerJob | null>(null);
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
        run_date: current.task ? current.run_date : defaultScheduleForm(payload.scheduler_timezone).run_date,
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
      setForm(defaultScheduleForm(data()?.scheduler_timezone));
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
      run_date: (args.run_date as string) || dateTimeInTimeZone(new Date(), data()?.scheduler_timezone),
      weeks: String(args.weeks ?? ""),
      days: String(args.days ?? ""),
      hours: String(args.hours ?? ""),
      minutes: String(args.minutes ?? ""),
      seconds: String(args.seconds ?? ""),
      cron_minute: String(args.minute ?? "*"),
      cron_hour: String(args.hour ?? "*"),
      cron_day: String(args.day ?? "*"),
      cron_month: String(args.month ?? "*"),
      cron_day_of_week: String(args.day_of_week ?? "*"),
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
    if (action === "delete") {
      setDeleteTarget(job);
      return;
    }
    try {
      await postJson(`/scheduler/jobs/${encodeURIComponent(job.id)}/${action}`);
      showToast(`Job ${action} requested.`);
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Job action failed", "error");
    }
  };

  const confirmDeleteJob = async () => {
    const job = deleteTarget();
    if (!job) {
      return;
    }
    try {
      await deleteJson(`/scheduler/jobs/${encodeURIComponent(job.id)}`);
      showToast("Job delete requested.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Job action failed", "error");
    } finally {
      setDeleteTarget(null);
    }
  };

  useCatalogAndLoad(load);

  return (
    <AppShell
      title="Scheduled Tasks"
      subtitle="Create recurring and one-off channel tasks."
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <section class="panel">
                <div class="panel-header">
                  <div class="panel-title">Create Scheduled Job</div>
                  <span class="hint">{payload().scheduler_timezone}</span>
                </div>
                <div class="panel-body stack">
                  <div class="field">
                    <label>Task</label>
                    <textarea class="textarea" value={form().task} onInput={(event) => setField("task", event.currentTarget.value)} />
                  </div>
                  <div class="grid-2">
                    <div class="field">
                      <label>Target User</label>
                      <SelectControl
                        value={form().identity}
                        options={[
                          ...payload().identities.map((identity) => ({
                            value: `${identity.platform}:${identity.external_id}`,
                            label: `${identity.platform} - ${identity.external_id}`,
                          })),
                          { value: "__manual__", label: "Enter manually" },
                        ]}
                        onChange={(value) => setField("identity", value)}
                        ariaLabel="Target User"
                      />
                    </div>
                    <div class="field">
                      <label>Schedule</label>
                      <SelectControl
                        value={form().trigger_type}
                        options={getTriggerTypes()}
                        onChange={(value) => setField("trigger_type", value as TriggerType)}
                        ariaLabel="Schedule"
                      />
                    </div>
                  </div>
                  <Show when={form().identity === "__manual__"}>
                    <div class="grid-2">
                      <div class="field">
                        <label>Platform</label>
                        <SelectControl
                          value={form().platform}
                          options={getPlatforms()}
                          onChange={(value) => setField("platform", value)}
                          ariaLabel="Platform"
                        />
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
                <DashboardTable
                  tableClass="scheduler-jobs-table"
                  columns={[
                    { header: "Job" },
                    { header: "Target" },
                    { header: "Trigger" },
                    { header: "Next Run" },
                    { header: "Status" },
                    { header: "Actions" },
                  ]}
                  rows={payload().jobs}
                  emptyMessage="No scheduled jobs."
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
                      <td>
                        <div class="scheduler-trigger-cell">
                          <span class="chip">{job.trigger_type}</span>
                          <Show when={job.trigger_type === "cron"}>
                            <span class="mono hint scheduler-cron-expr">
                              {buildCronExpression(
                                job.trigger_args?.minute as string,
                                job.trigger_args?.hour as string,
                                job.trigger_args?.day as string,
                                job.trigger_args?.month as string,
                                job.trigger_args?.day_of_week as string
                              )}
                            </span>
                            <div class="scheduler-cron-summary hint">
                              {formatCronDescription(
                                job.trigger_args?.minute as string,
                                job.trigger_args?.hour as string,
                                job.trigger_args?.day as string,
                                job.trigger_args?.month as string,
                                job.trigger_args?.day_of_week as string
                              )}
                            </div>
                          </Show>
                        </div>
                      </td>
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
                </DashboardTable>
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
                      <SelectControl
                        value={editForm().identity}
                        options={[
                          ...(data()?.identities || []).map((identity) => ({
                            value: `${identity.platform}:${identity.external_id}`,
                            label: `${identity.platform} - ${identity.external_id}`,
                          })),
                          { value: "__manual__", label: "Enter manually" },
                        ]}
                        onChange={(value) => setEditField("identity", value)}
                        ariaLabel="Target User"
                      />
                    </div>
                    <div class="field">
                      <label>Schedule</label>
                      <SelectControl
                        value={editForm().trigger_type}
                        options={getTriggerTypes()}
                        onChange={(value) => setEditField("trigger_type", value as TriggerType)}
                        ariaLabel="Schedule"
                      />
                    </div>
                  </div>
                  <Show when={editForm().identity === "__manual__"}>
                    <div class="grid-2">
                      <div class="field">
                        <label>Platform</label>
                        <SelectControl
                          value={editForm().platform}
                          options={getPlatforms()}
                          onChange={(value) => setEditField("platform", value)}
                          ariaLabel="Platform"
                        />
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

              <ConfirmDialog
                open={deleteTarget() !== null}
                title="Delete Job"
                message={<p>Are you sure you want to delete job <span class="mono">{deleteTarget()?.id}</span>?</p>}
                confirmLabel="Delete"
                confirmVariant="danger"
                onClose={() => setDeleteTarget(null)}
                onConfirm={() => void confirmDeleteJob()}
              />
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
