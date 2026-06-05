import { type JSXElement } from "solid-js";

import { classNames } from "@/lib/utils";

export function HealthStrip(props: {
  status: "healthy" | "degraded" | "down";
  uptimeDisplay: string;
  version: string;
  sessionsActive: number;
}) {
  const statusClass = () => `health-pill health-pill-${props.status}`;
  const statusLabel = () => {
    if (props.status === "healthy") return "Healthy";
    if (props.status === "degraded") return "Degraded";
    return "Down";
  };

  return (
    <div class="health-strip">
      <div class="health-strip-primary">
        <span class={statusClass()}>
          <span class="health-dot" />
          {statusLabel()}
        </span>
        <div class="health-strip-stats">
          <HealthStat label="Uptime" value={props.uptimeDisplay || "—"} />
          <HealthStat
            label="Version"
            value={props.version || "—"}
          />
          <HealthStat
            label="Active sessions"
            value={String(props.sessionsActive)}
          />
        </div>
      </div>
    </div>
  );
}

function HealthStat(props: { label: string; value: JSXElement }) {
  return (
    <div class="health-stat">
      <div class="health-stat-value">{props.value}</div>
      <div class="health-stat-label">{props.label}</div>
    </div>
  );
}

export function HealthStripSkeleton() {
  return <div class="health-strip skeleton-line" style="height: 64px;" />;
}

export function healthPanelClass(extra?: string): string {
  return classNames("panel", extra);
}
