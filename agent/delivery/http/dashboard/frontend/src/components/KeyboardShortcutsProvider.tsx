import { useLocation, useNavigate } from "@solidjs/router";
import { Command } from "lucide-solid";
import {
  createContext, createEffect, createMemo, createSignal, lazy, on, onCleanup, onMount, Show,
  Suspense, useContext, type Accessor, type ParentProps,
} from "solid-js";

import { CommandPalette, ShortcutHelpDialog, type PaletteItem } from "@/components/ShortcutDialogs";
import { ShortcutTooltip } from "@/components/ShortcutTooltip";
import { useToast } from "@/components/Toast";
import { ApiError, apiFetch, putJson } from "@/lib/api";
import { newChatNavigationState } from "@/lib/chatNavigation";
import { API_PATHS } from "@/lib/endpoints";
import {
  bindingFromEvent, bindingSignature, hasOpenDialog, ignoreGlobalShortcut,
  SHORTCUT_ACTIONS, SHORTCUT_LABELS, SHORTCUT_SETTINGS_PATH,
  type PrimaryModifier, type ShortcutAction, type ShortcutOverrides, type ShortcutSettings,
} from "@/lib/keyboardShortcuts";
import { closeMobileDrawer } from "@/lib/useMobileDrawer";
import { writeToClipboard } from "@/lib/utils";

const ConversationSearchDialog = lazy(() => import("@/components/ConversationSearchDialog")
  .then(({ ConversationSearchDialog }) => ({ default: ConversationSearchDialog })));

type ShortcutCommand = { run: () => void; enabled?: () => boolean };
type ShortcutDialog = "palette" | "help" | "sessions";
type SaveResult = "saved" | "conflict" | "failed";
type ShortcutContextValue = {
  settings: Accessor<ShortcutSettings | undefined>;
  bindings: Accessor<ShortcutOverrides>;
  loadError: Accessor<string>;
  saving: Accessor<boolean>;
  primary: PrimaryModifier;
  refresh: () => Promise<void>;
  save: (overrides: ShortcutOverrides, revision: number) => Promise<SaveResult>;
  run: (action: ShortcutAction) => void;
  register: (action: ShortcutAction, command: ShortcutCommand) => () => void;
};

const ShortcutContext = createContext<ShortcutContextValue>();

const pages = [
  ["Home", "/"],
  ["Chat history", "/history"],
  ["Projects", "/projects"],
  ["Repositories", "/repositories"],
  ["Scheduler", "/scheduler"],
  ["Settings", "/settings"],
  ["Keyboard shortcuts", SHORTCUT_SETTINGS_PATH],
] as const;

export function KeyboardShortcutsProvider(props: ParentProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { showToast } = useToast();
  const primary = /^(?:Mac|iP(?:hone|ad|od))/i.test(navigator.platform) ? "Meta" : "Ctrl";
  const active = () => !/^\/(?:login|change-password)\/?$/.test(location.pathname);
  const [settings, setSettings] = createSignal<ShortcutSettings>();
  const [loadError, setLoadError] = createSignal("");
  const [saving, setSaving] = createSignal(false);
  const [dialog, setDialog] = createSignal<ShortcutDialog>();
  const [sessionSearchMounted, setSessionSearchMounted] = createSignal(false);
  const [registered, setRegistered] = createSignal<Partial<Record<ShortcutAction, ShortcutCommand>>>({});
  let loadRequest: AbortController | undefined;

  const bindings = createMemo<ShortcutOverrides>(() => ({ ...settings()?.defaults, ...settings()?.overrides }));
  const byCombination = createMemo(() => {
    const index = new Map<string, ShortcutAction>();
    for (const action of SHORTCUT_ACTIONS) {
      for (const binding of bindings()[action] ?? []) index.set(bindingSignature(binding, primary), action);
    }
    return index;
  });

  const cancelLoad = () => {
    loadRequest?.abort();
    loadRequest = undefined;
  };

  const load = async () => {
    if (loadRequest || !active()) return;
    const request = new AbortController();
    loadRequest = request;
    setLoadError("");
    try {
      const result = await apiFetch<ShortcutSettings>(API_PATHS.keyboardShortcuts, { signal: request.signal });
      if (!result?.defaults || !result.overrides || !Number.isInteger(result.revision)) {
        throw new Error("Could not load keyboard shortcuts.");
      }
      if (!request.signal.aborted) setSettings(result);
    } catch (error) {
      if (!request.signal.aborted) {
        setLoadError(error instanceof Error ? error.message : "Could not load keyboard shortcuts.");
      }
    } finally {
      if (loadRequest === request) loadRequest = undefined;
    }
  };
  const refresh = async () => {
    if (!saving()) await load();
  };

  const save = async (overrides: ShortcutOverrides, revision: number): Promise<SaveResult> => {
    const current = settings();
    if (!active() || !current || saving()) return "failed";
    if (current.revision !== revision) {
      showToast("Keyboard shortcuts changed in another tab or device. Try your edit again.", "warning");
      return "conflict";
    }
    cancelLoad();
    setSaving(true);
    try {
      const result = await putJson<ShortcutSettings>(API_PATHS.keyboardShortcuts, { overrides, revision });
      if (active()) {
        setSettings(result);
        setLoadError("");
        showToast("Keyboard shortcuts saved.");
      }
      return "saved";
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Could not save keyboard shortcuts.", "error");
      if (error instanceof ApiError && error.status === 409) {
        await load();
        return "conflict";
      }
      return "failed";
    } finally {
      setSaving(false);
    }
  };

  const closeDialog = () => setDialog(undefined);
  const openDialog = (next: ShortcutDialog) => {
    if (!active() || hasOpenDialog()) return;
    if (next === "sessions") setSessionSearchMounted(true);
    setDialog(next);
  };
  const goTo = (href: string) => {
    closeMobileDrawer();
    navigate(href);
  };
  const commands: Partial<Record<ShortcutAction, ShortcutCommand>> = {
    "new-session": { run: () => {
      closeMobileDrawer();
      navigate("/chat", { state: newChatNavigationState() });
    } },
    back: { run: () => navigate(-1) },
    forward: { run: () => navigate(1) },
    "copy-link": { run: async () => {
      try {
        await writeToClipboard(window.location.href);
        showToast("Link copied.");
      } catch {
        showToast("Could not copy the link.", "error");
      }
    } },
    "command-palette": { run: () => openDialog("palette") },
    "show-keyboard-shortcuts": { run: () => openDialog("help") },
    "search-sessions": { run: () => openDialog("sessions") },
  };
  const commandFor = (action: ShortcutAction) => {
    const command = registered()[action] ?? commands[action];
    return command?.enabled?.() === false ? undefined : command;
  };
  const run = (action: ShortcutAction) => {
    if (active()) commandFor(action)?.run();
  };
  const register = (action: ShortcutAction, command: ShortcutCommand) => {
    setRegistered((current) => ({ ...current, [action]: command }));
    return () => setRegistered((current) => {
      if (current[action] !== command) return current;
      const next = { ...current };
      delete next[action];
      return next;
    });
  };

  const paletteItems = createMemo<PaletteItem[]>(() => {
    const items: PaletteItem[] = [];
    for (const action of SHORTCUT_ACTIONS) {
      if (action === "command-palette" || !commandFor(action)) continue;
      items.push({ label: SHORTCUT_LABELS[action], binding: bindings()[action]?.[0], run: () => run(action) });
    }
    return items.concat(pages.map(([label, href]) => ({ label, run: () => goTo(href) })));
  });
  const onKeyDown = (event: KeyboardEvent) => {
    if (!active()) return;
    if (dialog()) {
      if (event.key === "Escape" && !hasOpenDialog()) closeDialog();
      return;
    }
    const binding = bindingFromEvent(event, primary, true);
    const action = binding && byCombination().get(bindingSignature(binding, primary));
    if (!action || ignoreGlobalShortcut(event)) return;
    const command = commandFor(action);
    if (command) {
      event.preventDefault();
      command.run();
    }
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") void refresh();
  };

  createEffect(on(active, (enabled) => {
    if (enabled) {
      void refresh();
    } else {
      cancelLoad();
      setSettings(undefined);
      setLoadError("");
      closeDialog();
    }
  }));
  createEffect(on(() => location.pathname, closeDialog, { defer: true }));
  onMount(() => {
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
  });
  onCleanup(() => {
    cancelLoad();
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("focus", onVisible);
    document.removeEventListener("visibilitychange", onVisible);
  });

  return (
    <ShortcutContext.Provider value={{ settings, bindings, loadError, saving, primary, refresh, save, run, register }}>
      {props.children}
      <ShortcutTooltip bindings={bindings()} primary={primary} path={location.pathname} />
      <CommandPalette
        open={dialog() === "palette"}
        items={paletteItems()}
        primary={primary}
        onClose={closeDialog}
        onSelect={(item) => {
          closeDialog();
          item.run();
        }}
      />
      <ShortcutHelpDialog
        open={dialog() === "help"}
        ready={Boolean(settings())}
        bindings={bindings()}
        primary={primary}
        error={loadError()}
        onRetry={() => void refresh()}
        onClose={closeDialog}
      />
      <Show when={sessionSearchMounted()}>
        <Suspense fallback={<span class="sr-only" role="status">Loading conversations...</span>}>
          <ConversationSearchDialog
            open={dialog() === "sessions"}
            onClose={closeDialog}
            onSelect={() => {
              closeDialog();
              closeMobileDrawer();
            }}
          />
        </Suspense>
      </Show>
    </ShortcutContext.Provider>
  );
}

export function useKeyboardShortcuts(): ShortcutContextValue {
  const context = useContext(ShortcutContext);
  if (!context) throw new Error("KeyboardShortcutsProvider is missing");
  return context;
}

export function useShortcutAction(action: ShortcutAction, command: ShortcutCommand) {
  const shortcuts = useContext(ShortcutContext);
  if (shortcuts) {
    onCleanup(shortcuts.register(action, command));
  }
}

export function CommandPaletteButton() {
  const shortcuts = useContext(ShortcutContext);
  if (!shortcuts) return null;
  return (
    <button
      class="nav-link shortcut-launcher"
      type="button"
      aria-label="Command palette"
      aria-haspopup="dialog"
      data-shortcut-action="command-palette"
      onClick={() => shortcuts.run("command-palette")}
    >
      <Command size={15} aria-hidden="true" />
      <span class="nav-label">Command palette</span>
    </button>
  );
}
