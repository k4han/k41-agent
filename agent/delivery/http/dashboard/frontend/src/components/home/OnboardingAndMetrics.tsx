import { For, Show } from "solid-js";
import { A } from "@solidjs/router";

import type {
  OnboardingState,
  HomeCounters,
} from "@/types";

export function OnboardingChecklist(props: { state: OnboardingState }) {
  return (
    <Show when={props.state.show_checklist}>
      <section class="panel onboarding-panel">
        <div class="panel-header">
          <div class="panel-title">Get started</div>
          <div class="panel-subtitle">
            Complete these steps to unlock the full agent experience.
          </div>
        </div>
        <ol class="onboarding-list">
          <OnboardingItem
            done={!props.state.needs_provider}
            title="Add an LLM provider"
            description="Configure at least one provider with an API key and default model."
            href="/settings/providers"
            cta="Configure provider"
          />
          <OnboardingItem
            done={!props.state.needs_channel}
            title="Connect a channel"
            description="Start a chat channel (Telegram, Discord) so the agent can talk to users."
            href="/settings/channels"
            cta="Configure channel"
          />
          <OnboardingItem
            done={!props.state.needs_agent}
            title="Create your first agent"
            description="Define an agent card with a system prompt, tools, and a default model."
            href="/settings/agents/new"
            cta="Create agent"
          />
        </ol>
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
        value={`${c.channels.running}/${c.channels.total}`}
        label="Channels running"
        tone={c.channels.error > 0 ? "warning" : "neutral"}
        href="/settings/channels"
      />
      <MetricCard
        value={String(c.agents)}
        label="Agents configured"
        href="/settings/agents"
      />
      <MetricCard
        value={String(c.tasks.active)}
        label={`Active tasks${c.tasks.failed ? ` (${c.tasks.failed} failed)` : ""}`}
        tone={c.tasks.failed > 0 ? "danger" : "neutral"}
        href="/tasks"
      />
      <MetricCard
        value={String(c.scheduler.upcoming)}
        label={`Scheduled jobs (${c.scheduler.total} total)`}
        href="/scheduler"
      />
      <MetricCard
        value={String(c.providers.ready)}
        label={`Providers ready (${c.providers.total})`}
        tone={c.providers.ready === 0 ? "warning" : "neutral"}
        href="/settings/providers"
      />
      <MetricCard
        value={`${c.mcp_servers.connected}/${c.mcp_servers.total}`}
        label="MCP servers connected"
        tone={c.mcp_servers.connected === 0 ? "neutral" : "neutral"}
        href="/settings/connections"
      />
    </div>
  );
}

function MetricCard(props: {
  value: string;
  label: string;
  tone?: "neutral" | "warning" | "danger";
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
