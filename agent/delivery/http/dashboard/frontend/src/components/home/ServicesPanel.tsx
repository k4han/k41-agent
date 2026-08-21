import { createSignal, For, Show } from "solid-js";
import { Loader2, Play, Square } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";
import type { ServiceStatus } from "@/types";

export function ServicesPanel(props: {
  services: ServiceStatus[];
  onAction: (name: string, action: "start" | "stop") => Promise<void> | void;
  onAllAction: (action: "start-all" | "stop-all") => Promise<void> | void;
}) {
  const { showToast } = useToast();
  const [pendingKey, setPendingKey] = createSignal("");
  const [confirmStopAll, setConfirmStopAll] = createSignal(false);
  const [allActionPending, setAllActionPending] = createSignal(false);

  const runningCount = () => props.services.filter((s) => s.status === "running").length;

  const handleAction = async (service: ServiceStatus, action: "start" | "stop") => {
    const key = `${service.name}:${action}`;
    setPendingKey(key);
    try {
      await props.onAction(service.name, action);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Service action failed", "error");
    } finally {
      setPendingKey("");
    }
  };

  const handleAllAction = async (action: "start-all" | "stop-all") => {
    setAllActionPending(true);
    try {
      await props.onAllAction(action);
    } finally {
      setAllActionPending(false);
      setConfirmStopAll(false);
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
        <div class="row-wrap panel-header-actions">
          <button
            class="btn btn-sm"
            type="button"
            disabled={allActionPending() || props.services.length === 0}
            onClick={() => void handleAllAction("start-all")}
          >
            <Play size={12} />
            Start all
          </button>
          <button
            class="btn btn-sm btn-warning"
            type="button"
            disabled={allActionPending() || runningCount() === 0}
            onClick={() => setConfirmStopAll(true)}
          >
            <Square size={12} />
            Stop all
          </button>
        </div>
      </div>
      <div class="panel-body">
        <Show
          when={props.services.length > 0}
          fallback={<EmptyState message="No services registered." />}
        >
          <ul class="compact-list">
            <For each={props.services}>
              {(service) => (
                <li class="compact-list-item">
                  <div class="compact-list-main">
                    <div class="compact-list-title mono">{service.name}</div>
                    <Show when={service.error}>
                      <div class="muted compact-list-meta service-error-text">
                        {service.error}
                      </div>
                    </Show>
                  </div>
                  <StatusBadge status={service.status} />
                  <Show
                    when={service.status === "running"}
                    fallback={
                      <button
                        class="btn btn-sm"
                        type="button"
                        disabled={pendingKey() !== ""}
                        onClick={() => void handleAction(service, "start")}
                      >
                        <Show
                          when={pendingKey() === `${service.name}:start`}
                          fallback={<Play size={12} />}
                        >
                          <Loader2 size={12} class="spin-icon" />
                        </Show>
                        Start
                      </button>
                    }
                  >
                    <button
                      class="btn btn-sm btn-warning"
                      type="button"
                      disabled={pendingKey() !== ""}
                      onClick={() => void handleAction(service, "stop")}
                    >
                      <Show
                        when={pendingKey() === `${service.name}:stop`}
                        fallback={<Square size={12} />}
                      >
                        <Loader2 size={12} class="spin-icon" />
                      </Show>
                      Stop
                    </button>
                  </Show>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>

      <ConfirmDialog
        open={confirmStopAll()}
        title="Stop all services?"
        message={`This will stop ${runningCount()} running service${runningCount() === 1 ? "" : "s"}. Channels and background workers will go offline until started again.`}
        confirmLabel="Stop all"
        confirmVariant="warning"
        loading={allActionPending()}
        onClose={() => setConfirmStopAll(false)}
        onConfirm={() => void handleAllAction("stop-all")}
      />
    </section>
  );
}
