import { createEffect, createMemo, createSignal, For, type JSX, on, onCleanup, Show } from "solid-js";
import { Bot, CalendarClock, Clock, Edit3, History, Pause, Play, Plus, Trash2 } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Dialog } from "@/components/Dialog";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getPlatforms, getTriggerTypes } from "@/lib/catalogStore";
import { API_PATHS } from "@/lib/endpoints";
import { isSchedulerSession } from "@/lib/schedulerSessions";
import { useSessions } from "@/lib/sessionStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { dateTimeInTimeZone, triggerArgsFromDateInput, truncateText } from "@/lib/utils";
import { formatRunTime, modelLabel, RunHistoryPanel } from "@/components/scheduler/RunHistoryPanel";
import { RunningNowPanel, RunningPill } from "@/components/scheduler/RunningNowPanel";
import { SchedulerProjectSelect } from "@/components/scheduler/SchedulerProjectSelect";
import type {
  ActiveSession,
  Identity,
  SchedulerExecutor,
  SchedulerJob,
  SchedulerRun,
  SchedulerRunsPayload,
} from "@/types";

type SchedulerPayload = {
  jobs: SchedulerJob[];
  identities: Identity[];
  scheduler_timezone: string;
  executor?: SchedulerExecutor;
};

const RUN_HISTORY_REFRESH_DELAY_MS = 1500;

function ExecutorModelNotice(props: { executor?: SchedulerExecutor }) {
  return (
    <Show when={props.executor}>
      {(executor) => (
        <div class="scheduler-executor-notice">
          <Bot size={15} />
          <div>
            <div>
              Runs with agent <span class="mono">{executor().agent_name}</span> using{" "}
              <Show when={executor().model} fallback={<span class="hint">an unresolved model</span>}>
                <span class="mono scheduler-executor-model">{modelLabel(executor().provider, executor().model)}</span>
              </Show>
            </div>
            <div class="hint">
              {executor().configured === "default"
                ? "The agent card uses provider \"default\", so it follows the llm.default_model setting."
                : `Configured on the agent card as ${executor().configured}.`}
              {" "}The model actually used is recorded for each run in Run History.
            </div>
            <Show when={executor().error}>
              <div class="scheduler-run-error-text">{executor().error}</div>
            </Show>
          </div>
        </div>
      )}
    </Show>
  );
}

type TriggerType = "date" | "relative" | "interval" | "cron";

type ScheduleForm = {
  task: string;
  project_id: number | null;
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
    project_id: null,
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

const SCHEDULER_RUNS_LOOKUP_LIMIT = 20;
const CLOCK_TICK_MS = 1000;

const DURATION_UNITS = [
  ["weeks", "w"],
  ["days", "d"],
  ["hours", "h"],
  ["minutes", "m"],
  ["seconds", "s"],
] as const;

function durationLabel(args: Record<string, unknown>): string {
  return DURATION_UNITS.flatMap(([key, unit]) => {
    const value = Number(args[key] ?? 0);
    return Number.isFinite(value) && value > 0 ? [`${value}${unit}`] : [];
  }).join(" ");
}

function cronExpressionOf(job: SchedulerJob): string {
  const args = job.trigger_args || {};
  return buildCronExpression(
    args.minute as string,
    args.hour as string,
    args.day as string,
    args.month as string,
    args.day_of_week as string
  );
}

export function scheduleSummary(job: SchedulerJob): string {
  const args = job.trigger_args || {};
  if (job.trigger_type === "cron") {
    return formatCronDescription(
      args.minute as string,
      args.hour as string,
      args.day as string,
      args.month as string,
      args.day_of_week as string
    );
  }
  const duration = durationLabel(args);
  if (job.trigger_type === "interval") {
    return duration ? `Runs every ${duration}` : "Runs on an interval";
  }
  if (typeof args.run_date === "string" && args.run_date) {
    return `Runs once at ${formatRunTime(args.run_date)}`;
  }
  if (job.trigger_type === "relative" && duration) {
    return `Runs once, ${duration} after creation`;
  }
  return "Runs once";
}

export function formatRelativeTime(value: string | null, now: number): string {
  if (!value) return "";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "";
  const delta = timestamp - now;
  const distance = Math.abs(delta);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (distance < minute) return delta >= 0 ? "in a moment" : "just now";
  let amount: string;
  if (distance < hour) {
    amount = `${Math.floor(distance / minute)}m`;
  } else if (distance < day) {
    const minutes = Math.floor((distance % hour) / minute);
    amount = `${Math.floor(distance / hour)}h${minutes ? ` ${minutes}m` : ""}`;
  } else {
    amount = `${Math.floor(distance / day)}d`;
  }
  return delta >= 0 ? `in ${amount}` : `${amount} ago`;
}

function identityOptions(identities: Identity[]) {
  return [
    ...identities.map((identity) => ({
      value: `${identity.platform}:${identity.external_id}`,
      label: `${identity.platform} - ${identity.external_id}`,
    })),
    { value: "__manual__", label: "Enter manually" },
  ];
}

function JobFormFields(props: {
  form: ScheduleForm;
  setField: <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) => void;
  identities: Identity[];
}) {
  return (
    <div class="stack">
      <div class="field">
        <label>Task</label>
        <textarea
          class="textarea scheduler-task-input"
          placeholder="Describe what the agent should do each time this job fires."
          value={props.form.task}
          onInput={(event) => props.setField("task", event.currentTarget.value)}
        />
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Target User</label>
          <SelectControl
            value={props.form.identity}
            options={identityOptions(props.identities)}
            onChange={(value) => props.setField("identity", value)}
            ariaLabel="Target User"
          />
        </div>
        <div class="field">
          <label>Schedule</label>
          <SelectControl
            value={props.form.trigger_type}
            options={getTriggerTypes()}
            onChange={(value) => props.setField("trigger_type", value as TriggerType)}
            ariaLabel="Schedule"
          />
        </div>
      </div>
      <SchedulerProjectSelect value={props.form.project_id} onChange={value => props.setField("project_id", value)} />
      <Show when={props.form.identity === "__manual__"}>
        <div class="grid-2">
          <div class="field">
            <label>Platform</label>
            <SelectControl
              value={props.form.platform}
              options={getPlatforms()}
              onChange={(value) => props.setField("platform", value)}
              ariaLabel="Platform"
            />
          </div>
          <div class="field">
            <label>User ID</label>
            <input
              class="input"
              value={props.form.user_id}
              onInput={(event) => props.setField("user_id", event.currentTarget.value)}
            />
          </div>
        </div>
      </Show>
      <TriggerFields form={props.form} setField={props.setField} />
    </div>
  );
}

function MetricTile(props: { value: JSX.Element; label: string; tone?: "info"; title?: string }) {
  return (
    <div
      class={`panel metric metric-card scheduler-metric ${props.tone ? `metric-${props.tone}` : ""}`}
      title={props.title}
    >
      <div class="metric-value">{props.value}</div>
      <div class="metric-label">{props.label}</div>
    </div>
  );
}

function JobCard(props: {
  job: SchedulerJob;
  running: boolean;
  now: number;
  onEdit: () => void;
  onRun: () => void;
  onHistory: () => void;
  onTogglePause: () => void;
  onDelete: () => void;
}) {
  const nextRun = () => (props.job.paused ? null : props.job.next_run_time);
  return (
    <article
      class={`scheduler-job-card ${props.running ? "is-running" : ""} ${props.job.paused ? "is-paused" : ""}`}
    >
      <header class="scheduler-job-card-head">
        <div class="scheduler-job-tags">
          <Show when={props.running} fallback={<StatusBadge status={props.job.paused ? "paused" : "active"} />}>
            <RunningPill />
          </Show>
          <span class="chip">{props.job.trigger_type}</span>
          <span class="chip">{props.job.project_id ? `Project #${props.job.project_id}` : "No workspace"}</span>
        </div>
        <div class="scheduler-job-actions">
          <button class="btn btn-sm btn-ghost" type="button" title="Edit job" aria-label="Edit job" onClick={props.onEdit}>
            <Edit3 size={14} />
          </button>
          <button class="btn btn-sm btn-ghost" type="button" title="Run history" aria-label="Run history" onClick={props.onHistory}>
            <History size={14} />
          </button>
          <button
            class="btn btn-sm btn-ghost"
            type="button"
            title={props.job.paused ? "Resume job" : "Pause job"}
            aria-label={props.job.paused ? "Resume job" : "Pause job"}
            onClick={props.onTogglePause}
          >
            <Show when={props.job.paused} fallback={<Pause size={14} />}>
              <Play size={14} />
            </Show>
          </button>
          <button
            class="btn btn-sm btn-ghost scheduler-job-delete"
            type="button"
            title="Delete job"
            aria-label="Delete job"
            onClick={props.onDelete}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </header>

      <p class="scheduler-job-task" title={props.job.task}>{props.job.task}</p>

      <div class="scheduler-trigger-cell">
        <div class="scheduler-schedule-line">
          <Clock size={13} />
          <span class="scheduler-cron-summary">{scheduleSummary(props.job)}</span>
        </div>
        <Show when={props.job.trigger_type === "cron"}>
          <code class="mono scheduler-cron-expr">{cronExpressionOf(props.job)}</code>
        </Show>
      </div>

      <footer class="scheduler-job-foot">
        <div class="scheduler-job-foot-meta">
          <div class="scheduler-job-next">
            <span class="scheduler-job-foot-label">Next run</span>
            <Show
              when={nextRun()}
              fallback={<span class="hint">{props.job.paused ? "Paused" : "Not scheduled"}</span>}
            >
              {(value) => (
                <>
                  <span class="scheduler-job-next-relative">{formatRelativeTime(value(), props.now)}</span>
                  <span class="mono hint">{formatRunTime(value())}</span>
                </>
              )}
            </Show>
          </div>
          <div class="scheduler-job-target">
            <span class="badge">{props.job.platform}</span>
            <span class="mono hint" title={props.job.user_id}>{props.job.user_id}</span>
          </div>
        </div>
        <button class="btn btn-sm" type="button" disabled={props.running} onClick={props.onRun}>
          <Play size={13} />
          Run now
        </button>
      </footer>
    </article>
  );
}

export function SchedulerPage() {
  const [data, setData] = createSignal<SchedulerPayload>();
  const [error, setError] = createSignal("");
  const [form, setForm] = createSignal<ScheduleForm>(defaultScheduleForm());
  const [createOpen, setCreateOpen] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [editJob, setEditJob] = createSignal<SchedulerJob | null>(null);
  const [editForm, setEditForm] = createSignal<ScheduleForm>(defaultScheduleForm());
  const [deleteTarget, setDeleteTarget] = createSignal<SchedulerJob | null>(null);
  const [deleting, setDeleting] = createSignal(false);
  const [historyJobFilter, setHistoryJobFilter] = createSignal("");
  const [historyRefreshToken, setHistoryRefreshToken] = createSignal(0);
  const [recentRuns, setRecentRuns] = createSignal<SchedulerRun[]>([]);
  const [stopping, setStopping] = createSignal<ReadonlySet<string>>(new Set());
  const [now, setNow] = createSignal(Date.now());
  const { showToast } = useToast();
  const { sessions } = useSessions();

  const clock = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
  onCleanup(() => clearInterval(clock));

  const schedulerSessions = createMemo(() => sessions().filter(isSchedulerSession));
  const runsByThread = createMemo(() => new Map(recentRuns().map((run) => [run.thread_id, run])));
  const runningJobIds = createMemo(() => {
    const ids = new Set<string>();
    for (const session of schedulerSessions()) {
      const jobId = runsByThread().get(session.thread_id)?.job_id;
      if (jobId) ids.add(jobId);
    }
    return ids;
  });

  const summary = createMemo(() => {
    const jobs = data()?.jobs ?? [];
    const active = jobs.filter((job) => !job.paused);
    const upcoming = active
      .filter((job) => job.next_run_time && !Number.isNaN(Date.parse(job.next_run_time)))
      .sort((left, right) => Date.parse(left.next_run_time!) - Date.parse(right.next_run_time!));
    return { active: active.length, paused: jobs.length - active.length, next: upcoming[0] ?? null };
  });

  const refreshHistorySoon = () => {
    setTimeout(() => setHistoryRefreshToken((value) => value + 1), RUN_HISTORY_REFRESH_DELAY_MS);
  };

  const showJobHistory = (job: SchedulerJob) => {
    setHistoryJobFilter(job.id);
    document.getElementById("scheduler-run-history")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

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

  const loadRecentRuns = async () => {
    try {
      const payload = await apiFetch<SchedulerRunsPayload>(
        API_PATHS.schedulerRuns({ limit: SCHEDULER_RUNS_LOOKUP_LIMIT }),
      );
      setRecentRuns(payload.runs);
    } catch {
      // Live sessions still render without their run details.
    }
  };

  // A scheduler session starting or stopping changes run history and next run times.
  createEffect(on(
    () => schedulerSessions().map((session) => session.thread_id).sort().join("|"),
    (key, previous) => {
      const live = new Set(key ? key.split("|") : []);
      setStopping((current) => new Set([...current].filter((threadId) => live.has(threadId))));
      if (key) void loadRecentRuns();
      if (previous === undefined) return;
      setHistoryRefreshToken((value) => value + 1);
      void load();
    },
  ));

  const openCreate = () => setCreateOpen(true);

  const createJob = async () => {
    setSaving(true);
    try {
      const current = form();
      const identity = identityFromForm(current);
      await postJson("/scheduler/jobs", {
        task: current.task,
        project_id: current.project_id,
        platform: identity.platform,
        user_id: identity.user_id,
        trigger_type: current.trigger_type,
        trigger_args: triggerArgs(current),
      });
      showToast("Job created.");
      setCreateOpen(false);
      setForm(defaultScheduleForm(data()?.scheduler_timezone));
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create job", "error");
    } finally {
      setSaving(false);
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
      project_id: job.project_id ?? null,
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
    setSaving(true);
    try {
      const current = editForm();
      const identity = identityFromForm(current);
      await putJson(`/scheduler/jobs/${encodeURIComponent(job.id)}`, {
        task: current.task,
        project_id: current.project_id,
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
    } finally {
      setSaving(false);
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
      if (action === "run") {
        refreshHistorySoon();
      }
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Job action failed", "error");
    }
  };

  const confirmDeleteJob = async () => {
    const job = deleteTarget();
    if (!job || deleting()) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJson(API_PATHS.schedulerJob(job.id));
      setDeleteTarget(null);
      showToast("Job deleted.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Job action failed", "error");
    } finally {
      setDeleting(false);
    }
  };

  const stopSession = async (session: ActiveSession) => {
    const threadId = session.thread_id;
    setStopping((current) => new Set([...current, threadId]));
    try {
      await postJson(API_PATHS.sessionsStop, { thread_id: threadId });
      showToast("Stop requested.", "success");
    } catch (err) {
      setStopping((current) => new Set([...current].filter((id) => id !== threadId)));
      showToast(err instanceof Error ? err.message : "Failed to stop the task", "error");
    }
  };

  useCatalogAndLoad(load);

  return (
    <AppShell
      title="Scheduled Tasks"
      subtitle={
        <>
          Create recurring and one-off channel tasks.
          <Show when={data()?.scheduler_timezone}>
            {(timeZone) => <span class="scheduler-subtitle-tz"> Times use <span class="mono">{timeZone()}</span>.</span>}
          </Show>
        </>
      }
      actions={
        <button class="btn btn-primary" type="button" disabled={!data()} onClick={openCreate}>
          <Plus size={14} />
          New job
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack scheduler-page">
            <div class="scheduler-metrics">
              <MetricTile value={summary().active} label="Active jobs" />
              <MetricTile value={summary().paused} label="Paused jobs" />
              <MetricTile
                tone={schedulerSessions().length > 0 ? "info" : undefined}
                value={
                  <>
                    <Show when={schedulerSessions().length > 0}>
                      <span class="live-dot" aria-hidden="true" />
                    </Show>
                    {schedulerSessions().length}
                  </>
                }
                label="Running now"
              />
              <MetricTile
                value={
                  <span class="scheduler-metric-text">
                    {summary().next ? formatRelativeTime(summary().next!.next_run_time, now()) : "-"}
                  </span>
                }
                label={summary().next ? `Next: ${truncateText(summary().next!.task, 60)}` : "Next run"}
                title={summary().next?.task}
              />
            </div>

            <Show when={schedulerSessions().length > 0}>
              <RunningNowPanel
                sessions={schedulerSessions()}
                runsByThread={runsByThread()}
                now={now()}
                stopping={stopping()}
                onStop={(session) => void stopSession(session)}
              />
            </Show>

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title scheduler-panel-title">
                  <CalendarClock size={15} /> Jobs <span class="hint">{payload().jobs.length}</span>
                </div>
              </div>
              <Show
                when={payload().jobs.length > 0}
                fallback={
                  <div class="panel-body">
                    <div class="empty-state">
                      <div class="empty-state-icon" aria-hidden="true"><CalendarClock size={20} /></div>
                      <div class="empty-state-message">No scheduled jobs yet.</div>
                      <div class="empty-state-hint">
                        Run the agent on a schedule and deliver the result to a channel.
                      </div>
                      <button class="btn btn-sm btn-primary empty-state-action" type="button" onClick={openCreate}>
                        <Plus size={13} />
                        New job
                      </button>
                    </div>
                  </div>
                }
              >
                <div class="scheduler-job-grid">
                  <For each={payload().jobs}>
                    {(job) => (
                      <JobCard
                        job={job}
                        running={runningJobIds().has(job.id)}
                        now={now()}
                        onEdit={() => openEdit(job)}
                        onRun={() => void jobAction(job, "run")}
                        onHistory={() => showJobHistory(job)}
                        onTogglePause={() => void jobAction(job, job.paused ? "resume" : "pause")}
                        onDelete={() => void jobAction(job, "delete")}
                      />
                    )}
                  </For>
                </div>
              </Show>
            </section>

            <RunHistoryPanel
              jobs={payload().jobs}
              jobFilter={historyJobFilter()}
              onJobFilterChange={setHistoryJobFilter}
              refreshToken={historyRefreshToken()}
            />

            <Dialog
              open={createOpen()}
              title="New scheduled job"
              subtitle={`Times use ${payload().scheduler_timezone}`}
              icon={<CalendarClock size={16} />}
              size="lg"
              onClose={() => setCreateOpen(false)}
              footer={
                <>
                  <button class="btn" type="button" onClick={() => setCreateOpen(false)}>
                    Cancel
                  </button>
                  <button class="btn btn-primary" type="button" disabled={saving()} onClick={createJob}>
                    <Plus size={14} />
                    Create job
                  </button>
                </>
              }
            >
              <div class="stack">
                <ExecutorModelNotice executor={payload().executor} />
                <JobFormFields form={form()} setField={setField} identities={payload().identities} />
              </div>
            </Dialog>

            <Dialog
              open={Boolean(editJob())}
              title="Edit scheduled job"
              subtitle={editJob()?.id}
              icon={<Edit3 size={16} />}
              size="lg"
              onClose={() => setEditJob(null)}
              footer={
                <>
                  <button class="btn" type="button" onClick={() => setEditJob(null)}>
                    Close
                  </button>
                  <button class="btn btn-primary" type="button" disabled={saving()} onClick={updateJob}>
                    Save Changes
                  </button>
                </>
              }
            >
              <div class="stack">
                <ExecutorModelNotice executor={payload().executor} />
                <JobFormFields form={editForm()} setField={setEditField} identities={data()?.identities || []} />
              </div>
            </Dialog>

            <ConfirmDialog
              open={deleteTarget() !== null}
              title="Delete Job"
              message={<p>Delete the scheduled job <strong>{truncateText(deleteTarget()?.task || "", 120)}</strong>? Existing run history will be kept.</p>}
              confirmLabel="Delete"
              confirmVariant="danger"
              loading={deleting()}
              onClose={() => { if (!deleting()) setDeleteTarget(null); }}
              onConfirm={() => void confirmDeleteJob()}
            />
          </div>
        )}
      </DataGate>
    </AppShell>
  );
}
