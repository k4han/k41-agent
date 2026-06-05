import { For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { CalendarClock } from "lucide-solid";

import { truncateText } from "@/lib/utils";
import type { UpcomingJob } from "@/types";

export function UpcomingJobsPanel(props: { jobs: UpcomingJob[]; timezone: string }) {
  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">Upcoming jobs</div>
          <div class="panel-subtitle">
            Next scheduled runs ({props.timezone || "local time"}).
          </div>
        </div>
        <A class="btn btn-sm" href="/scheduler">
          Open scheduler
        </A>
      </div>
      <div class="panel-body">
        <Show
          when={props.jobs.length > 0}
          fallback={<div class="empty">No jobs scheduled.</div>}
        >
          <ul class="compact-list">
            <For each={props.jobs}>
              {(job) => (
                <li class="compact-list-item">
                  <CalendarClock size={14} class="muted" />
                  <div class="compact-list-main">
                    <div class="compact-list-title">
                      {truncateText(job.task || job.id, 60)}
                    </div>
                    <div class="muted compact-list-meta">
                      <span>{job.platform}</span>
                      <Show when={job.user_id}>
                        <span>·</span>
                        <span>{job.user_id}</span>
                      </Show>
                      <Show when={job.next_run_time}>
                        <span>·</span>
                        <span>{formatRunTime(job.next_run_time!)}</span>
                      </Show>
                    </div>
                  </div>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>
    </section>
  );
}

function formatRunTime(value: string): string {
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value;
  const delta = ts - Date.now();
  if (delta < 0) return "overdue";
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (delta < minute) return "in a moment";
  if (delta < hour) return `in ${Math.floor(delta / minute)}m`;
  if (delta < day) return `in ${Math.floor(delta / hour)}h`;
  return `in ${Math.floor(delta / day)}d`;
}
