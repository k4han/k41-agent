import { ChevronDown, FolderOpen, Lock, X } from "lucide-solid";
import { createEffect, createMemo, createSignal, createUniqueId, onCleanup, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { WorkspaceSelector, type WorkspaceSelectorProps } from "@/components/WorkspaceSelector";
import { getBackendDisplayName } from "@/lib/catalogStore";
import { isTempWorkspace, workspaceDisplayLabel, workspaceDisplayLabelFromValues } from "@/lib/workspace";

export function ComposerWorkspacePicker(props: WorkspaceSelectorProps) {
  const [open, setOpen] = createSignal(false);
  const [nestedDialogOpen, setNestedDialogOpen] = createSignal(false);
  const [position, setPosition] = createSignal({ left: 8, edge: 8, width: 540, maxHeight: 480, above: true });
  const panelId = createUniqueId();
  let triggerRef: HTMLButtonElement | undefined;
  let panelRef: HTMLDivElement | undefined;

  const backend = () => props.selection?.backend || props.workspace?.backend || "local";
  const backendLabel = () => {
    const label = getBackendDisplayName(backend());
    return label === "local" ? "Local" : label;
  };
  const projectLabel = createMemo(() => {
    if (props.selection?.source === "temp" || isTempWorkspace(props.workspace)) return "Temporary";
    if (props.selection) {
      return props.selection.label || props.selection.repositoryFullName
        || workspaceDisplayLabelFromValues("", props.selection.localPath) || "Select project";
    }
    return workspaceDisplayLabel(props.workspace)
      || workspaceDisplayLabelFromValues("", props.workingDir) || "Select project";
  });
  const description = () => `${projectLabel()} · ${backendLabel()}${props.locked ? " · Workspace locked" : ""}`;

  const close = () => {
    setOpen(false);
    triggerRef?.focus({ preventScroll: true });
  };

  const updatePosition = () => {
    if (!triggerRef) return;
    const rect = triggerRef.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop || 0;
    const viewportLeft = viewport?.offsetLeft || 0;
    const viewportWidth = viewport?.width || window.innerWidth;
    const viewportBottom = viewportTop + (viewport?.height || window.innerHeight);
    const width = Math.min(540, viewportWidth - 16);
    const above = rect.top - viewportTop >= viewportBottom - rect.bottom;
    setPosition({
      left: Math.max(viewportLeft + 8, Math.min(rect.left, viewportLeft + viewportWidth - width - 8)),
      edge: above ? window.innerHeight - rect.top + 6 : rect.bottom + 6,
      width,
      maxHeight: Math.max(0, Math.min(480, above ? rect.top - viewportTop - 14 : viewportBottom - rect.bottom - 14)),
      above,
    });
  };

  createEffect(() => {
    if (!open()) return;
    updatePosition();
    const observer = new ResizeObserver(updatePosition);
    if (triggerRef) observer.observe(triggerRef);
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
      observer.disconnect();
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      window.visualViewport?.removeEventListener("resize", updatePosition);
      window.visualViewport?.removeEventListener("scroll", updatePosition);
    });
  });

  createEffect(() => {
    props.workspace;
    props.selection;
    props.locked;
    if (open()) updatePosition();
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
        <Show when={props.locked}><Lock size={12} class="composer-workspace-lock" /></Show>
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
              top: position().above ? "auto" : `${position().edge}px`,
              bottom: position().above ? `${position().edge}px` : "auto",
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
            <Show when={props.locked}>
              <p class="composer-workspace-locked">{backendLabel()} · Workspace is locked to this conversation.</p>
            </Show>
            <WorkspaceSelector
              {...props}
              embedded
              onBrowserOpenChange={setNestedDialogOpen}
            />
          </div>
        </Portal>
      </Show>
    </div>
  );
}
