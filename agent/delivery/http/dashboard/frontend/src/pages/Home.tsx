import { A } from "@solidjs/router";
import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { MessageSquarePlus, Play, Square } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { useToast } from "@/components/Toast";
import {
  ActiveSessionsPanel,
  HomeMetrics,
  OnboardingChecklist,
  ProvidersHealthPanel,
  RecentTasksPanel,
  RecentThreadsPanel,
  ServicesPanel,
  UpcomingJobsPanel,
} from "@/components/home";
import { apiFetch, postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { HomePayload } from "@/types";

const HOME_POLL_INTERVAL_MS = 10000;

export function HomePage() {
  const [data, setData] = createSignal<HomePayload>();
  const [error, setError] = createSignal("");
  const { showToast } = useToast();
  let timer: number | undefined;
  let loading = false;
  let disposed = false;

  const clearRefreshTimer = () => {
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timer = undefined;
    }
  };

  const scheduleRefresh = () => {
    clearRefreshTimer();
    if (disposed || document.hidden) {
      return;
    }

    timer = window.setTimeout(() => {
      timer = undefined;
      void load();
    }, HOME_POLL_INTERVAL_MS);
  };

  const load = async () => {
    if (disposed || loading) {
      return;
    }

    loading = true;
    clearRefreshTimer();
    setError("");
    try {
      const payload = await apiFetch<HomePayload>("/dashboard-api/home");
      if (disposed) {
        return;
      }

      setData(payload);
    } catch (err) {
      if (!disposed) {
        setError(err instanceof Error ? err.message : "Failed to load home");
      }
    } finally {
      loading = false;
      scheduleRefresh();
    }
  };

  const serviceAction = async (name: string, action: "start" | "stop") => {
    const path =
      action === "start"
        ? API_PATHS.serviceStart(name)
        : API_PATHS.serviceStop(name);
    await postJson(path);
    showToast(`Service ${action} requested.`);
    await load();
  };

  const allAction = async (action: "start-all" | "stop-all") => {
    try {
      await postJson(`/services/${action}`);
      showToast("Service state updated.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Service action failed", "error");
    }
  };

  const handleVisibilityChange = () => {
    if (disposed) {
      return;
    }

    if (document.hidden) {
      clearRefreshTimer();
      return;
    }

    void load();
  };

  onMount(() => {
    document.addEventListener("visibilitychange", handleVisibilityChange);
    void load();
  });
  onCleanup(() => {
    disposed = true;
    clearRefreshTimer();
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  });

  return (
    <AppShell
      title="Home"
      subtitle="Command center for the Kai Agent runtime."
      actions={
        <>
          <A class="btn" href="/chat">
            <MessageSquarePlus size={14} />
            New chat
          </A>
          <button class="btn" type="button" onClick={() => allAction("start-all")}>
            <Play size={14} />
            Start all
          </button>
          <button
            class="btn btn-warning"
            type="button"
            onClick={() => allAction("stop-all")}
          >
            <Square size={14} />
            Stop all
          </button>
        </>
      }
    >
      <Show
        when={data()}
        fallback={
          <HomeSkeleton />
        }
      >
        {(payload) => (
          <div class="stack home-stack">
            <HomeMetrics counters={payload().counters} />

            <OnboardingChecklist state={payload().onboarding} />

            <div class="home-grid">
              <div class="home-col">
                <ActiveSessionsPanel initial={payload().active_sessions} />
                <RecentTasksPanel tasks={payload().recent.tasks} />
                <RecentThreadsPanel threads={payload().recent.threads} />
              </div>
              <div class="home-col">
                <ServicesPanel
                  services={payload().services}
                  onAction={serviceAction}
                />
                <UpcomingJobsPanel
                  jobs={payload().recent.upcoming_jobs}
                  timezone={payload().scheduler_timezone}
                />
                <ProvidersHealthPanel providers={payload().providers_health} />
              </div>
            </div>

            <Show when={error()}>
              <div class="badge badge-danger">{error()}</div>
            </Show>
          </div>
        )}
      </Show>
    </AppShell>
  );
}

function HomeSkeleton() {
  return (
    <div class="stack home-stack" aria-busy="true" aria-label="Loading dashboard">
      <div class="grid-metrics">
        <For each={Array.from({ length: 6 })}>
          {() => (
            <div class="panel metric metric-card home-skeleton-metric">
              <span class="skeleton-line home-skeleton-value" />
              <span class="skeleton-line home-skeleton-label" />
            </div>
          )}
        </For>
      </div>

      <section class="panel onboarding-panel home-skeleton-panel">
        <div class="panel-header">
          <span class="skeleton-line home-skeleton-title" />
          <span class="skeleton-line home-skeleton-subtitle" />
        </div>
        <div class="panel-body home-skeleton-list">
          <For each={Array.from({ length: 3 })}>
            {() => (
              <div class="home-skeleton-row">
                <span class="skeleton-line home-skeleton-dot" />
                <div class="home-skeleton-row-main">
                  <span class="skeleton-line home-skeleton-row-title" />
                  <span class="skeleton-line home-skeleton-row-text" />
                </div>
                <span class="skeleton-line home-skeleton-action" />
              </div>
            )}
          </For>
        </div>
      </section>

      <div class="home-grid">
        <div class="home-col">
          <HomePanelSkeleton rows={3} />
          <HomePanelSkeleton rows={3} />
          <HomePanelSkeleton rows={4} />
        </div>
        <div class="home-col">
          <HomePanelSkeleton rows={4} />
          <HomePanelSkeleton rows={3} />
          <HomePanelSkeleton rows={3} />
        </div>
      </div>
    </div>
  );
}

function HomePanelSkeleton(props: { rows: number }) {
  return (
    <section class="panel home-skeleton-panel">
      <div class="panel-header split">
        <div class="home-skeleton-heading">
          <span class="skeleton-line home-skeleton-title" />
          <span class="skeleton-line home-skeleton-subtitle" />
        </div>
        <span class="skeleton-line home-skeleton-button" />
      </div>
      <div class="panel-body home-skeleton-list">
        <For each={Array.from({ length: props.rows })}>
          {() => (
            <div class="home-skeleton-row">
              <span class="skeleton-line home-skeleton-icon" />
              <div class="home-skeleton-row-main">
                <span class="skeleton-line home-skeleton-row-title" />
                <span class="skeleton-line home-skeleton-row-text" />
              </div>
              <span class="skeleton-line home-skeleton-badge" />
            </div>
          )}
        </For>
      </div>
    </section>
  );
}
