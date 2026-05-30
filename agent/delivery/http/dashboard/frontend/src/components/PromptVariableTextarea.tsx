import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";

import { classNames } from "@/lib/utils";
import type { PromptVariable } from "@/types";

type PromptVariableTextareaProps = {
  value: string;
  onChange: (value: string) => void;
  variables: PromptVariable[];
  disabled?: boolean;
  rows?: number;
  placeholder?: string;
  class?: string;
  ref?: HTMLTextAreaElement | ((el: HTMLTextAreaElement) => void);
  containerClass?: string;
};

type TriggerState = {
  start: number;
  query: string;
};

type CaretPosition = {
  top: number;
  left: number;
  lineHeight: number;
};

const NAME_PATTERN = /^[A-Za-z0-9_-]*$/;

const MIRROR_COPY_PROPS = [
  "boxSizing",
  "width",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "borderTopWidth",
  "borderRightWidth",
  "borderBottomWidth",
  "borderLeftWidth",
  "fontFamily",
  "fontSize",
  "fontWeight",
  "fontStyle",
  "letterSpacing",
  "textTransform",
  "wordSpacing",
  "textIndent",
  "lineHeight",
  "tabSize",
  "whiteSpace",
  "wordWrap",
  "wordBreak",
] as const;

function findTrigger(value: string, cursor: number): TriggerState | null {
  const before = value.slice(0, cursor);
  const openIndex = before.lastIndexOf("{{");
  if (openIndex === -1) {
    return null;
  }
  const closeIndex = before.indexOf("}}", openIndex + 2);
  if (closeIndex !== -1 && closeIndex < cursor) {
    return null;
  }
  const query = before.slice(openIndex + 2);
  if (!NAME_PATTERN.test(query)) {
    return null;
  }
  return { start: openIndex, query };
}

function measureCaret(textarea: HTMLTextAreaElement, position: number): CaretPosition {
  const computed = window.getComputedStyle(textarea);
  const mirror = document.createElement("div");
  const style = mirror.style;
  style.position = "absolute";
  style.visibility = "hidden";
  style.top = "0";
  style.left = "-9999px";
  style.overflow = "hidden";
  for (const prop of MIRROR_COPY_PROPS) {
    style[prop as any] = computed[prop as any];
  }
  style.height = "auto";
  document.body.appendChild(mirror);

  const before = textarea.value.slice(0, position);
  mirror.textContent = before;
  const marker = document.createElement("span");
  marker.textContent = "\u200b";
  mirror.appendChild(marker);

  const top = marker.offsetTop - textarea.scrollTop;
  const left = marker.offsetLeft - textarea.scrollLeft;
  const lineHeight =
    parseFloat(computed.lineHeight) || parseFloat(computed.fontSize) * 1.2 || 16;

  document.body.removeChild(mirror);
  return { top, left, lineHeight };
}

export function PromptVariableTextarea(props: PromptVariableTextareaProps) {
  const [trigger, setTrigger] = createSignal<TriggerState | null>(null);
  const [activeIndex, setActiveIndex] = createSignal(0);
  const [caret, setCaret] = createSignal<CaretPosition>({ top: 0, left: 0, lineHeight: 16 });
  let textareaRef: HTMLTextAreaElement | undefined;
  let rootRef: HTMLDivElement | undefined;

  const filteredVariables = createMemo(() => {
    const state = trigger();
    if (!state) {
      return [];
    }
    const needle = state.query.toLowerCase();
    const list = props.variables.filter((variable) =>
      variable.name.toLowerCase().includes(needle),
    );
    list.sort((left, right) => {
      const leftStarts = left.name.toLowerCase().startsWith(needle) ? 0 : 1;
      const rightStarts = right.name.toLowerCase().startsWith(needle) ? 0 : 1;
      if (leftStarts !== rightStarts) {
        return leftStarts - rightStarts;
      }
      return left.name.localeCompare(right.name);
    });
    return list;
  });

  const closeSuggest = () => {
    setTrigger(null);
    setActiveIndex(0);
  };

  const refreshCaret = (state: TriggerState) => {
    const textarea = textareaRef;
    if (!textarea) {
      return;
    }
    setCaret(measureCaret(textarea, state.start));
  };

  const updateTrigger = (value: string, cursor: number) => {
    const state = findTrigger(value, cursor);
    if (!state) {
      closeSuggest();
      return;
    }
    setTrigger(state);
    setActiveIndex(0);
    refreshCaret(state);
  };

  const insertVariable = (variable: PromptVariable) => {
    const state = trigger();
    const textarea = textareaRef;
    if (!state || !textarea) {
      return;
    }
    const value = props.value;
    const cursor = textarea.selectionStart ?? state.start + 2 + state.query.length;
    const replacement = `{{${variable.name}}}`;
    const next = value.slice(0, state.start) + replacement + value.slice(cursor);
    props.onChange(next);
    closeSuggest();
    const newCursor = state.start + replacement.length;
    queueMicrotask(() => {
      textarea.focus();
      textarea.setSelectionRange(newCursor, newCursor);
    });
  };

  const handleInput = (event: InputEvent & { currentTarget: HTMLTextAreaElement }) => {
    const next = event.currentTarget.value;
    props.onChange(next);
    updateTrigger(next, event.currentTarget.selectionStart ?? next.length);
  };

  const handleKeyDown = (event: KeyboardEvent & { currentTarget: HTMLTextAreaElement }) => {
    const list = filteredVariables();
    if (!trigger() || list.length === 0) {
      if (event.key === "Escape" && trigger()) {
        event.preventDefault();
        closeSuggest();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % list.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + list.length) % list.length);
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      const variable = list[activeIndex()] ?? list[0];
      if (variable) {
        insertVariable(variable);
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeSuggest();
    }
  };

  const handleSelectionChange = () => {
    const textarea = textareaRef;
    if (!textarea) {
      return;
    }
    if (document.activeElement !== textarea) {
      return;
    }
    updateTrigger(textarea.value, textarea.selectionStart ?? textarea.value.length);
  };

  const handleScroll = () => {
    const state = trigger();
    if (state) {
      refreshCaret(state);
    }
  };

  onMount(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef && !rootRef.contains(event.target as Node)) {
        closeSuggest();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("selectionchange", handleSelectionChange);
    onCleanup(() => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("selectionchange", handleSelectionChange);
    });
  });

  return (
    <div class={classNames("prompt-variable-textarea", props.containerClass)} ref={rootRef}>
      <textarea
        ref={(el) => {
          textareaRef = el;
          if (typeof props.ref === "function") {
            props.ref(el);
          } else if (props.ref) {
            (props as any).ref = el;
          }
        }}
        class={classNames("textarea mono", props.class)}
        rows={props.rows ?? 12}
        value={props.value}
        disabled={props.disabled}
        placeholder={props.placeholder}
        onInput={handleInput}
        onKeyDown={handleKeyDown}
        onScroll={handleScroll}
        onBlur={() => {
          window.setTimeout(closeSuggest, 100);
        }}
      />
      <Show when={trigger() && !props.disabled}>
        <div
          class="prompt-variable-suggest"
          style={{
            top: `${caret().top + caret().lineHeight}px`,
            left: `${caret().left}px`,
          }}
        >
          <Show
            when={filteredVariables().length > 0}
            fallback={<div class="prompt-variable-suggest-empty">No matching prompt variables.</div>}
          >
            <For each={filteredVariables()}>
              {(variable, index) => (
                <button
                  type="button"
                  class={classNames(
                    "prompt-variable-suggest-option",
                    index() === activeIndex() && "active",
                  )}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveIndex(index())}
                  onClick={() => insertVariable(variable)}
                >
                  <span class="mono">{`{{${variable.name}}}`}</span>
                  <Show when={variable.value}>
                    <span class="hint">
                      {variable.value.length > 80
                        ? `${variable.value.slice(0, 80).trim()}…`
                        : variable.value}
                    </span>
                  </Show>
                </button>
              )}
            </For>
          </Show>
        </div>
      </Show>
    </div>
  );
}
