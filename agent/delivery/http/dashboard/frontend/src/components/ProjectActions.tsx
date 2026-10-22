import { MoreVertical } from "lucide-solid";
import { createEffect, createSignal, createUniqueId, onCleanup, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { deleteJson, patchJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { notifyProjectsChanged } from "@/lib/projects";
import type { ProjectSummary } from "@/types";

type ActionProject = Pick<ProjectSummary, "id" | "label" | "is_pinned" | "status">;

export function ProjectActions(props: { project: ActionProject }) {
  const [open, setOpen] = createSignal(false);
  const [position, setPosition] = createSignal({ top: 0, left: 0 });
  const [renaming, setRenaming] = createSignal(false);
  const [removing, setRemoving] = createSignal(false);
  const [name, setName] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const { showToast } = useToast();
  const menuId = createUniqueId();
  let trigger: HTMLButtonElement | undefined;
  let menu: HTMLDivElement | undefined;

  const close = () => { setOpen(false); trigger?.focus({ preventScroll: true }); };
  const run = async (changes?: Partial<ActionProject>) => {
    close();
    setBusy(true);
    try {
      const url = `${API_PATHS.projects}/${props.project.id}`;
      if (changes) await patchJson(url, changes);
      else await deleteJson(url);
      setRenaming(false);
      setRemoving(false);
      notifyProjectsChanged();
      showToast("Project updated.", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Failed to update project.", "error");
    } finally { setBusy(false); }
  };

  createEffect(() => {
    if (!open()) return;
    queueMicrotask(() => menu?.querySelector<HTMLButtonElement>("button")?.focus());
    const outside = (event: PointerEvent) => {
      if (!menu?.contains(event.target as Node) && !trigger?.contains(event.target as Node)) setOpen(false);
    };
    const dismiss = () => setOpen(false);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    onCleanup(() => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    });
  });

  return <>
    <button ref={trigger} class="btn btn-icon project-actions-trigger" type="button"
      aria-label={`Manage ${props.project.label}`} title={`Manage ${props.project.label}`}
      aria-haspopup="menu" aria-expanded={open()} disabled={busy()}
      aria-controls={open() ? menuId : undefined}
      onClick={() => {
        if (open()) { close(); return; }
        const rect = trigger!.getBoundingClientRect();
        setPosition({ top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 204)),
          left: Math.max(8, Math.min(rect.right - 200, window.innerWidth - 208)) });
        setOpen(true);
      }}><MoreVertical size={15} /></button>
    <Show when={open()}><Portal>
      <div ref={menu} id={menuId} role="menu" aria-label={`Actions for ${props.project.label}`} class="project-menu"
        style={{ top: `${position().top}px`, left: `${position().left}px` }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); close(); }
          if (event.key === "Tab") setOpen(false);
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const buttons = Array.from(menu!.querySelectorAll<HTMLButtonElement>("button"));
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
            const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
              : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
            buttons[next]?.focus();
          }
        }}>
        <button role="menuitem" onClick={() => { setName(props.project.label); close(); setRenaming(true); }}>Rename</button>
        <button role="menuitem" onClick={() => void run({ is_pinned: !props.project.is_pinned })}>
          {props.project.is_pinned ? "Unpin project" : "Pin project"}</button>
        <button role="menuitem" onClick={() => void run({ status: props.project.status === "active" ? "archived" : "active" })}>
          {props.project.status === "active" ? "Archive project" : "Restore project"}</button>
        <Show when={props.project.status !== "removed"}>
          <button role="menuitem" class="danger-text" onClick={() => { close(); setRemoving(true); }}>Remove from projects</button>
        </Show>
      </div>
    </Portal></Show>
    <Dialog open={renaming()} title="Rename project" size="sm" onClose={() => { if (!busy()) setRenaming(false); }}>
      <form class="project-form" onSubmit={(event) => { event.preventDefault(); if (name().trim() && !busy()) void run({ label: name().trim() }); }}>
        <label class="field">Project name<input class="input" value={name()} maxLength={255} required
          disabled={busy()} onInput={(event) => setName(event.currentTarget.value)} /></label>
        <button class="btn btn-primary" type="submit" disabled={busy() || !name().trim()}>{busy() ? "Saving..." : "Save"}</button>
      </form>
    </Dialog>
    <ConfirmDialog open={removing()} title="Remove project?" confirmLabel="Remove project" loading={busy()}
      message={<>Remove <strong>{props.project.label}</strong> from the catalog? Conversations, files, and sandboxes will be kept. You can restore it from Removed projects.</>}
      onClose={() => { if (!busy()) setRemoving(false); }} onConfirm={() => void run()} />
  </>;
}
