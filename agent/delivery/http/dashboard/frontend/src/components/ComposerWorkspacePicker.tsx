import { ChevronDown, FolderOpen, Lock, X } from "lucide-solid";
import { createEffect, createMemo, createSignal, createUniqueId, onCleanup, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { WorkspaceSelector, type WorkspaceSelectorProps } from "@/components/WorkspaceSelector";
import { WorkspaceRuntimePicker } from "@/components/WorkspaceRuntimePicker";
import type { ThreadRuntimePayload, WorkspaceBackendKey } from "@/types";
import { getBackendDisplayName } from "@/lib/catalogStore";
import { isTempWorkspace, workspaceDisplayLabel, workspaceDisplayLabelFromValues } from "@/lib/workspace";

export function ComposerWorkspacePicker(props: WorkspaceSelectorProps & {
  runtimePayload?: ThreadRuntimePayload;
  runtimeBusy?: boolean;
  runtimeError?: string;
  onSwitchBackend?: (backend: WorkspaceBackendKey) => Promise<void>;
}) {
  const [open, setOpen] = createSignal(false);
  const [nestedDialogOpen, setNestedDialogOpen] = createSignal(false);
  const [position, setPosition] = createSignal({ left: 0, top: 0, width: 540, maxHeight: 480 });
  const panelId = createUniqueId();
  let triggerRef: HTMLButtonElement | undefined;
  let panelRef: HTMLDivElement | undefined;

  const backend = () => props.selection?.backend || props.workspace?.backend || "local";
  const backendLabel = () => {
    const label = getBackendDisplayName(backend());
    const titles: Record<string, string> = { local: "Local", daytona: "Daytona", modal: "Modal" };
    return label === backend() ? titles[backend()] || label : label;
  };
  const projectLabel = createMemo(() => {
    if (props.selection?.source === "temp" || isTempWorkspace(props.workspace)) return "Temporary";
    if (props.selection) {
      return props.selection.label || props.selection.repositoryFullName
        || workspaceDisplayLabelFromValues("", props.selection.localPath) || "Temporary";
    }
    return workspaceDisplayLabel(props.workspace)
      || workspaceDisplayLabelFromValues("", props.workingDir) || "Temporary";
  });
  const canSwitchRuntime = () => props.locked && props.runtimePayload?.kind === "shared" && props.onSwitchBackend;
  const description = () => `${projectLabel()} · ${backendLabel()}${props.locked && !canSwitchRuntime() ? " · Workspace locked" : ""}`;

  const close = () => {
    setOpen(false);
    triggerRef?.focus({ preventScroll: true });
  };

  const updatePosition = () => {
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop || 0;
    const viewportLeft = viewport?.offsetLeft || 0;
    const viewportWidth = viewport?.width || window.innerWidth;
    const viewportHeight = viewport?.height || window.innerHeight;
    setPosition({
      left: viewportLeft + viewportWidth / 2,
      top: viewportTop + viewportHeight / 2,
      width: Math.max(0, Math.min(540, viewportWidth - 16)),
      maxHeight: Math.max(0, Math.min(480, viewportHeight - 16)),
    });
  };

  createEffect(() => {
    if (!open()) return;
    updatePosition();
    const onPointerDown = (event: PointerEvent) => {
      if (nestedDialogOpen()) return;
      const target = event.target as Node | null;
      if (target && (triggerRef?.contains(target) || panelRef?.contains(target))) return;
      close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (nestedDialogOpen() || event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
      if (event.key === "Tab" && panelRef) {
        const controls = Array.from(panelRef.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        )).filter(element => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    const focusTimer = window.setTimeout(() => panelRef?.querySelector<HTMLElement>("button:not(:disabled)")?.focus(), 0);
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    window.visualViewport?.addEventListener("resize", updatePosition);
    window.visualViewport?.addEventListener("scroll", updatePosition);
    onCleanup(() => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      window.visualViewport?.removeEventListener("resize", updatePosition);
      window.visualViewport?.removeEventListener("scroll", updatePosition);
    });
  });

  return (
    <div class="composer-workspace-picker">
      <button
        ref={triggerRef}
        class="composer-workspace-trigger"
        classList={{ "is-open": open() }}
        type="button"
        title={description()}
        aria-label={`Select project and backend: ${description()}`}
        aria-haspopup="dialog"
        aria-expanded={open()}
        aria-controls={open() ? panelId : undefined}
        onClick={() => open() ? close() : setOpen(true)}
      >
        <FolderOpen size={16} />
        <span class="composer-workspace-label">{projectLabel()}</span>
        <span class="composer-workspace-backend">· {backendLabel()}</span>
        <Show when={props.locked && !canSwitchRuntime()}><Lock size={12} class="composer-workspace-lock" /></Show>
        <ChevronDown size={12} class="composer-workspace-caret" />
      </button>
      <Show when={open()}>
        <Portal>
          <div
            ref={panelRef}
            id={panelId}
            class="composer-workspace-popover"
            role="dialog"
            aria-label="Project and backend"
            style={{
              left: `${position().left}px`,
              top: `${position().top}px`,
              width: `${position().width}px`,
              "max-height": `${position().maxHeight}px`,
            }}
          >
            <div class="composer-workspace-heading">
              <strong>Project and backend</strong>
              <button class="chat-composer-icon" type="button" aria-label="Close project picker" onClick={close}>
                <X size={16} />
              </button>
            </div>
            <Show when={props.locked && !canSwitchRuntime()}>
              <p class="composer-workspace-locked">{backendLabel()} · Workspace is locked to this conversation.</p>
            </Show>
            <Show when={canSwitchRuntime()} fallback={<WorkspaceSelector
              {...props}
              embedded
              onBrowserOpenChange={setNestedDialogOpen}
            />}>
              <WorkspaceRuntimePicker payload={props.runtimePayload!} busy={Boolean(props.runtimeBusy)}
                disabled={props.disabled} error={props.runtimeError || ""} onSwitch={props.onSwitchBackend!} portalMount={panelRef} />
            </Show>
          </div>
        </Portal>
      </Show>
    </div>
  );
}
