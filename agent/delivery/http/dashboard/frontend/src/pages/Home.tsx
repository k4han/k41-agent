import { A } from "@solidjs/router";
import { createSignal, onCleanup, onMount, Show } from "solid-js";
import { MessageSquarePlus, Play, Square } from "lucide-solid";

import { AppShell } from "@/components/AppShell";
import { useToast } from "@/components/Toast";
import {
  ActiveSessionsPanel,
  HealthStrip,
  HealthStripSkeleton,
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
          <div class="stack">
            <HealthStripSkeleton />
          </div>
        }
      >
        {(payload) => (
          <div class="stack home-stack">
            <HealthStrip
              status={payload().system.status}
              uptimeDisplay={payload().system.uptime_display}
              version={payload().system.version}
              sessionsActive={payload().counters.sessions_active}
            />

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
