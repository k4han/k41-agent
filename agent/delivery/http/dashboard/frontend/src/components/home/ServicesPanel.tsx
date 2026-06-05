import { For, Show } from "solid-js";
import { Play, Square } from "lucide-solid";

import { DashboardTable } from "@/components/DashboardTable";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";
import type { ServiceStatus } from "@/types";

export function ServicesPanel(props: {
  services: ServiceStatus[];
  onAction: (name: string, action: "start" | "stop") => Promise<void> | void;
}) {
  const { showToast } = useToast();

  const handleAction = async (service: ServiceStatus, action: "start" | "stop") => {
    try {
      await props.onAction(service.name, action);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Service action failed", "error");
    }
  };

  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">Services</div>
          <div class="panel-subtitle">
            Runtime channels and background services.
          </div>
        </div>
      </div>
      <div class="panel-body">
        <Show
          when={props.services.length > 0}
          fallback={<div class="empty">No services registered.</div>}
        >
          <DashboardTable
            columns={[
              { header: "Name" },
              { header: "Status" },
              { header: "Error" },
              { header: "Actions" },
            ]}
            rows={props.services}
            emptyMessage="No services registered."
          >
            {(service) => (
              <tr>
                <td class="mono">{service.name}</td>
                <td>
                  <StatusBadge status={service.status} />
                </td>
                <td class="muted">{service.error || "-"}</td>
                <td>
                  <Show
                    when={service.status === "running"}
                    fallback={
                      <button
                        class="btn btn-sm"
                        type="button"
                        onClick={() => handleAction(service, "start")}
                      >
                        <Play size={12} />
                        Start
                      </button>
                    }
                  >
                    <button
                      class="btn btn-sm btn-warning"
                      type="button"
                      onClick={() => handleAction(service, "stop")}
                    >
                      <Square size={12} />
                      Stop
                    </button>
                  </Show>
                </td>
              </tr>
            )}
          </DashboardTable>
        </Show>
      </div>
    </section>
  );
}
