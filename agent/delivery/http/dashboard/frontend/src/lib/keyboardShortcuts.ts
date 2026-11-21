export const SHORTCUT_SETTINGS_PATH = "/settings/keyboard-shortcuts";

export const SHORTCUT_LABELS = {
  "new-session": "New session",
  back: "Back",
  forward: "Forward",
  "copy-link": "Copy link",
  "command-palette": "Command palette",
  "show-keyboard-shortcuts": "Show keyboard shortcuts",
  "search-sessions": "Search sessions",
  "toggle-sidebar": "Toggle sidebar",
  "focus-message-input": "Focus message input",
} as const;

export type ShortcutAction = keyof typeof SHORTCUT_LABELS;
export type ShortcutOverrides = Partial<Record<ShortcutAction, string[]>>;
export type PrimaryModifier = "Ctrl" | "Meta";
export type ShortcutSettings = {
  defaults: Record<ShortcutAction, string[]>;
  overrides: ShortcutOverrides;
  revision: number;
};

export const SHORTCUT_ACTIONS = Object.keys(SHORTCUT_LABELS) as ShortcutAction[];

const keyCodePatterns = [
  /^(?:Key[A-Z]|Digit[0-9]|F(?:[1-9]|1[0-9]|2[0-4]))$/,
  /^Numpad(?:[0-9]|Add|Subtract|Multiply|Divide|Decimal|Enter|Equal|Comma)$/,
  /^(?:Space|Enter|Backspace|Delete|Insert|Home|End|Intl(?:Backslash|Ro|Yen))$/,
];
const keyLabels: Record<string, string> = {
  ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓",
  PageUp: "Page Up", PageDown: "Page Down",
  Backquote: "`", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]",
  Backslash: "\\", Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/",
};

export function isTypingKeyEvent(event: KeyboardEvent): boolean {
  return event.isComposing || event.keyCode === 229 || event.key === "Dead" || event.key === "Process";
}

export function bindingFromEvent(
  event: KeyboardEvent,
  primary: PrimaryModifier,
  allowBareSlash = false,
): string | undefined {
  if (event.repeat || isTypingKeyEvent(event)) return;

  const usePrimary = !(event.ctrlKey && event.metaKey)
    && (primary === "Ctrl" ? event.ctrlKey : event.metaKey);
  const modifiers = [
    usePrimary && "Mod",
    event.ctrlKey && !(usePrimary && primary === "Ctrl") && "Ctrl",
    event.altKey && "Alt",
    event.shiftKey && "Shift",
    event.metaKey && !(usePrimary && primary === "Meta") && "Meta",
  ].filter(Boolean);
  if (!modifiers.length) return allowBareSlash && event.key === "/" ? "Slash" : undefined;
  if (!Object.hasOwn(keyLabels, event.code) && !keyCodePatterns.some((pattern) => pattern.test(event.code))) return;
  return [...modifiers, event.code].join("+");
}

export function bindingSignature(binding: string, primary: PrimaryModifier): string {
  const keys = binding.split("+");
  const code = keys.pop();
  return [...keys.map((key) => key === "Mod" ? primary : key).sort(), code].join("+");
}

export function bindingLabels(binding: string, primary: PrimaryModifier): string[] {
  return binding.split("+").map((key) => {
    switch (key === "Mod" ? primary : key) {
      case "Ctrl": return "Ctrl";
      case "Meta": return primary === "Meta" ? "Cmd" : "Meta";
      case "Alt": return primary === "Meta" ? "Option" : "Alt";
      default: return keyLabels[key] ?? key.replace(/^(?:Key|Digit)/, "").replace(/^Numpad/, "Num ");
    }
  });
}

export function findBindingConflict(
  bindings: ShortcutOverrides,
  action: ShortcutAction,
  candidate: string,
  replacing?: string,
): ShortcutAction | undefined {
  return SHORTCUT_ACTIONS.find((id) => bindings[id]?.some((existing) => (
    (id !== action || existing !== replacing)
    && (["Ctrl", "Meta"] as const).some((primary) =>
      bindingSignature(existing, primary) === bindingSignature(candidate, primary))
  )));
}

export function hasOpenDialog(): boolean {
  return document.querySelector('[aria-modal="true"]') !== null;
}

export function ignoreGlobalShortcut(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.repeat || isTypingKeyEvent(event) || hasOpenDialog()) return true;

  const target = event.target instanceof Element ? event.target : document.activeElement;
  const editable = target instanceof HTMLElement ? target : target?.closest<HTMLElement>("[contenteditable]");
  return Boolean(editable?.isContentEditable
    || target?.closest("input, textarea, select, [role='textbox'], [data-remote-keyboard]"));
}
