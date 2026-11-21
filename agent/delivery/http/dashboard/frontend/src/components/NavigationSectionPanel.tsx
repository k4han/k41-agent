import { A } from "@solidjs/router";
import { ChevronRight, Plus } from "lucide-solid";
import { createMemo, For, Match, onCleanup, onMount, Show, Switch } from "solid-js";

import { useDashboardData } from "@/lib/dashboardData";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import type { GitHubPayload, SchedulerJob } from "@/types";

export type NavigationSection = "home" | "repositories" | "scheduler";

type PreviewRow = { label: string; href?: string; detail?: string };

const runTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function NavigationList<T>(props: {
  label: string;
  href: string;
  path: string;
  empty: string;
  listTitle?: string;
  rows: (payload: T) => PreviewRow[];
  refreshEvent?: string;
}) {
  const preview = useDashboardData<T>(props.path);
  const rows = createMemo<PreviewRow[]>((previous) => {
    // Reading a pending resource here would suspend the enclosing route.
    if (preview.loading()) return previous;
    const data = preview.data();
    return data ? props.rows(data) : [];
  }, []);
  const refreshEvent = props.refreshEvent;
  const refresh = () => void preview.load();
  if (refreshEvent) {
    onMount(() => window.addEventListener(refreshEvent, refresh));
    onCleanup(() => window.removeEventListener(refreshEvent, refresh));
  }

  return (
    <nav class="navigation-section-panel" aria-label={props.label}>
      <A class="nav-link navigation-section-page-link" href={props.href} activeClass="" inactiveClass="">
        <span>{props.label}</span>
        <ChevronRight size={14} aria-hidden="true" />
      </A>
      <Show when={props.listTitle}>
        <h2 class="nav-section-title">{props.listTitle}</h2>
      </Show>
      <ul class="navigation-section-list" aria-busy={preview.loading()}>
        <For each={rows()}>
          {(row) => (
            <li>
              <Show when={row.href} fallback={
                <div class="navigation-section-row">
                  <span class="nav-label" title={row.label}>{row.label}</span>
                  <span class="navigation-section-detail">{row.detail}</span>
                </div>
              }>
                {(href) => <A href={href()} class="nav-link" title={row.label}><span class="nav-label">{row.label}</span></A>}
              </Show>
            </li>
          )}
        </For>
      </ul>
      <Show when={preview.error()} fallback={
        <Show when={rows().length === 0}>
          <p class="nav-history-status" role="status">{preview.loading() ? "Loading…" : props.empty}</p>
        </Show>
      }>
        {(error) => (
          <div class="navigation-section-error">
            <p class="nav-history-status" role="status">{error()}</p>
            <button class="nav-history-retry" type="button" onClick={refresh}>Retry</button>
          </div>
        )}
      </Show>
    </nav>
  );
}

export function NavigationSectionPanel(props: {
  section: NavigationSection;
  onNewChat: () => void;
  newChatDisabled: boolean;
}) {
  return (
    <Switch>
      <Match when={props.section === "home"}>
        <nav class="navigation-section-panel" aria-label="Chat actions">
          <button
            type="button"
            class="nav-link nav-new-chat"
            data-shortcut-action="new-session"
            disabled={props.newChatDisabled}
            onClick={props.onNewChat}
          >
            <Plus size={15} aria-hidden="true" />
            New chat
          </button>
        </nav>
      </Match>
      <Match when={props.section === "repositories"}>
        <NavigationList<GitHubPayload>
          label="All repositories"
          href="/repositories"
          path="/dashboard-api/github"
          empty="No repositories"
          rows={(payload) => [...payload.repositories]
            .sort((left, right) => left.full_name.localeCompare(right.full_name))
            .slice(0, 6)
            .map((repo) => ({ label: repo.full_name, href: `/repositories/${encodeURIComponent(repo.repository_id)}` }))}
        />
      </Match>
      <Match when={props.section === "scheduler"}>
        <NavigationList<{ jobs: SchedulerJob[] }>
          label="Open Scheduler"
          href="/scheduler"
          path="/dashboard-api/scheduler"
          empty="No upcoming jobs"
          listTitle="Upcoming jobs"
          refreshEvent={CUSTOM_DOM_EVENTS.SCHEDULER_RUN_UPDATED}
          rows={(payload) => payload.jobs
            .filter((job) => !job.paused && job.next_run_time)
            .map((job) => ({ label: job.task, nextRun: Date.parse(job.next_run_time!) }))
            .filter((job) => Number.isFinite(job.nextRun))
            .sort((left, right) => left.nextRun - right.nextRun)
            .slice(0, 5)
            .map((job) => ({ label: job.label, detail: runTimeFormat.format(job.nextRun) }))}
        />
      </Match>
    </Switch>
  );
}
