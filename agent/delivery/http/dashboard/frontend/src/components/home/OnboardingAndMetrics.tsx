import { createMemo, createSignal, For, Show } from "solid-js";
import { A } from "@solidjs/router";
import { ChevronDown } from "lucide-solid";

import type {
  OnboardingState,
  HomeCounters,
} from "@/types";
import { STORAGE_KEYS } from "@/lib/uiConstants";

export function OnboardingChecklist(props: { state: OnboardingState }) {
  const [collapsed, setCollapsed] = createSignal(
    window.localStorage.getItem(STORAGE_KEYS.ONBOARDING_COLLAPSED) === "collapsed",
  );

  const steps = createMemo(() => [
    {
      done: !props.state.needs_provider,
      title: "Add an LLM provider",
      description: "Configure at least one provider with an API key and default model.",
      href: "/settings/providers",
      cta: "Configure provider",
    },
    {
      done: !props.state.needs_channel,
      title: "Connect a channel",
      description: "Start a chat channel (Telegram, Discord) so the agent can talk to users.",
      href: "/settings/channels",
      cta: "Configure channel",
    },
    {
      done: !props.state.needs_agent,
      title: "Create your first agent",
      description: "Define an agent card with a system prompt, tools, and a default model.",
      href: "/settings/agents/new",
      cta: "Create agent",
    },
  ]);

  const doneCount = () => steps().filter((step) => step.done).length;
  const totalCount = () => steps().length;
  const progressPercent = () => Math.round((doneCount() / totalCount()) * 100);

  return (
    <Show when={props.state.show_checklist && doneCount() < totalCount()}>
      <section class="panel onboarding-panel">
        <div class="panel-header split">
          <div>
            <div class="panel-title">Get started</div>
            <div class="panel-subtitle">
              Complete these steps to unlock the full agent experience.
            </div>
          </div>
          <button
            class="btn btn-icon btn-sm onboarding-collapse-btn"
            type="button"
            title={collapsed() ? "Expand checklist" : "Collapse checklist"}
            aria-label={collapsed() ? "Expand checklist" : "Collapse checklist"}
            aria-expanded={!collapsed()}
            onClick={() => {
              const next = !collapsed();
              setCollapsed(next);
              window.localStorage.setItem(
                STORAGE_KEYS.ONBOARDING_COLLAPSED,
                next ? "collapsed" : "expanded",
              );
            }}
          >
            <ChevronDown
              size={14}
              classList={{ "onboarding-collapse-caret": true, collapsed: collapsed() }}
            />
          </button>
        </div>
        <div class="onboarding-progress-row">
          <div
            class="onboarding-progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={totalCount()}
            aria-valuenow={doneCount()}
            aria-label="Setup progress"
          >
            <div class="onboarding-progress-fill" style={{ width: `${progressPercent()}%` }} />
          </div>
          <span class="muted onboarding-progress-count">{doneCount()}/{totalCount()}</span>
        </div>
        <Show when={!collapsed()}>
          <ol class="onboarding-list">
            <For each={steps()}>
              {(step) => (
                <OnboardingItem
                  done={step.done}
                  title={step.title}
                  description={step.description}
                  href={step.href}
                  cta={step.cta}
                />
              )}
            </For>
          </ol>
        </Show>
      </section>
    </Show>
  );
}

function OnboardingItem(props: {
  done: boolean;
  title: string;
  description: string;
  href: string;
  cta: string;
}) {
  return (
    <li class={props.done ? "onboarding-item done" : "onboarding-item"}>
      <div class="onboarding-marker">
        {props.done ? <span class="onboarding-check">✓</span> : <span class="onboarding-num">•</span>}
      </div>
      <div class="onboarding-body">
        <div class="onboarding-title">{props.title}</div>
        <div class="onboarding-desc">{props.description}</div>
      </div>
      <A class="btn btn-sm" href={props.href}>
        {props.done ? "Review" : props.cta}
      </A>
    </li>
  );
}

export function HomeMetrics(props: { counters: HomeCounters }) {
  const c = props.counters;
  return (
    <div class="grid-metrics">
      <MetricCard
        value={String(c.sessions_active)}
        label="Sessions running"
        tone={c.sessions_active > 0 ? "info" : "neutral"}
        href="/chat"
      />
      <MetricCard
        value={String(c.tasks.active)}
        label={`Active tasks${c.tasks.failed ? ` (${c.tasks.failed} failed)` : ""}`}
        tone={c.tasks.failed > 0 ? "danger" : "neutral"}
        href="/tasks"
      />
      <MetricCard
        value={`${c.channels.running}/${c.channels.total}`}
        label={`Channels running${c.channels.error ? ` (${c.channels.error} error)` : ""}`}
        tone={c.channels.error > 0 ? "warning" : "neutral"}
        href="/settings/channels"
      />
      <MetricCard
        value={`${c.providers.ready}/${c.providers.total}`}
        label="Providers ready"
        tone={c.providers.ready === 0 && c.providers.total > 0 ? "warning" : "neutral"}
        href="/settings/providers"
      />
    </div>
  );
}

function MetricCard(props: {
  value: string;
  label: string;
  tone?: "neutral" | "info" | "warning" | "danger";
  href?: string;
}) {
  const tone = props.tone || "neutral";
  const inner = (
    <div class={`panel metric metric-card metric-${tone}`}>
      <div class="metric-value">{props.value}</div>
      <div class="metric-label">{props.label}</div>
    </div>
  );
  if (props.href) {
    return <A class="metric-link" href={props.href}>{inner}</A>;
  }
  return inner;
}
