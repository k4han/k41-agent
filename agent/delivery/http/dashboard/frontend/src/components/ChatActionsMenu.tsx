import { ChartNoAxesCombined, Download, Ellipsis, Globe, Pencil } from "lucide-solid";
import { createEffect, createMemo, createSignal, createUniqueId, on, onCleanup, Show } from "solid-js";
import { Portal } from "solid-js/web";

import { computeFloatingPosition, floatingMenuStyle } from "@/lib/floating";
import type { ThreadUsagePayload, WorkspaceBackendKey } from "@/types";

export function ChatActionsMenu(props: {
  threadId: string;
  actionsDisabled: boolean;
  userMessages: number;
  contextTokens: number | null;
  usage: ThreadUsagePayload | null;
  backend?: WorkspaceBackendKey;
  browserAvailable: boolean;
  onOpen: () => void;
  onRename: () => void;
  onExport: () => void;
  onOpenBrowser: () => void;
}) {
  const [open, setOpen] = createSignal(false);
  const [position, setPosition] = createSignal<Record<string, string>>({});
  const menuId = createUniqueId();
  let trigger: HTMLButtonElement | undefined;
  let menu: HTMLDivElement | undefined;
  const usage = () => props.usage?.thread_id === props.threadId ? props.usage : null;
  const cost = createMemo(() => {
    const value = usage()?.estimated_cost_usd;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "Unavailable";
    const amount = value > 0 && value < 0.01 ? "<$0.01" : `$${value.toFixed(2)}`;
    return `≈ ${amount}${usage()?.cost_complete === false ? " (partial)" : ""}`;
  });
  const sessionSize = createMemo(() => {
    const tokens = props.contextTokens;
    if (tokens === null) return "—";
    if (tokens < 8000) return "XS";
    if (tokens < 32000) return "S";
    if (tokens < 64000) return "M";
    if (tokens < 128000) return "L";
    return "XL";
  });
  const platform = () => props.backend && props.backend !== "local" ? "Linux" : usage()?.host_platform || "—";
  const close = (restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) trigger?.focus({ preventScroll: true });
  };
  const updatePosition = () => {
    if (trigger) setPosition(floatingMenuStyle(computeFloatingPosition(trigger, {
      align: "right", minWidth: 280, defaultMaxHeight: 340,
    })));
  };
  const show = (last = false) => {
    updatePosition();
    setOpen(true);
    props.onOpen();
    queueMicrotask(() => {
      const buttons = menu?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
      (last ? buttons?.[buttons.length - 1] : buttons?.[0])?.focus();
    });
  };

  createEffect(on(() => props.threadId, () => close(false)));
  createEffect(() => {
    if (!open()) return;
    const outside = (event: PointerEvent) => {
      if (!menu?.contains(event.target as Node) && !trigger?.contains(event.target as Node)) close(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    onCleanup(() => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    });
  });

  return <>
    <button ref={trigger} class="chat-header-workspace-btn chat-actions-trigger" classList={{ "is-open": open() }}
      type="button" title="Conversation actions" aria-label="Conversation actions" aria-haspopup="menu"
      aria-expanded={open()} aria-controls={open() ? menuId : undefined}
      onClick={() => open() ? close() : show()}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault(); show(event.key === "ArrowUp");
        }
      }}><Ellipsis size={20} /></button>
    <Show when={open()}><Portal>
      <div ref={menu} id={menuId} class="chat-actions-menu" role="menu" aria-label="Conversation actions" style={position()}
        onKeyDown={(event) => {
          if (event.key === "Tab") { close(); return; }
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const buttons = Array.from(menu!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
            : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
          buttons[next]?.focus();
        }}>
        <button role="menuitem" type="button" disabled={props.actionsDisabled}
          onClick={() => { close(); props.onRename(); }}><Pencil size={15} />Rename conversation</button>
        <button role="menuitem" type="button" disabled={props.actionsDisabled}
          onClick={() => { close(); props.onExport(); }}><Download size={15} />Export conversation</button>
        <Show when={props.browserAvailable}>
          <button role="menuitem" type="button" onClick={() => { close(); props.onOpenBrowser(); }}><Globe size={15} />Open browser</button>
        </Show>
        <div class="chat-session-insights" role="group" aria-label="Session insights">
          <div class="chat-session-insights-title"><ChartNoAxesCombined size={14} /><span>Session insights</span></div>
          <dl>
            <div title="Estimated USD cost across this session and its subagents, using recorded tokens and catalog prices. Actual billing and cache discounts may differ.">
              <dt>On-demand usage:</dt><dd>{cost()}</dd>
            </div>
            <div><dt>User messages:</dt><dd>{props.userMessages}</dd></div>
            <div title={`Current context: ${props.contextTokens?.toLocaleString() ?? "unknown"} tokens. XS < 8K, S < 32K, M < 64K, L < 128K, XL ≥ 128K.`}>
              <dt>Session size:</dt><dd>{sessionSize()}</dd>
            </div>
            <div><dt>Platform:</dt><dd>{platform()}</dd></div>
          </dl>
          <Show when={usage()?.estimated_cost_usd != null && usage()?.cost_complete === false}>
            <p class="chat-session-insights-note">Some usage or model prices are unavailable.</p>
          </Show>
        </div>
      </div>
    </Portal></Show>
  </>;
}
