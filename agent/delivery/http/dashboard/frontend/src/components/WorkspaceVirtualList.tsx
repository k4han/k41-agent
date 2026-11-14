import { createVirtualizer } from "@tanstack/solid-virtual";
import { createEffect, createMemo, For, onCleanup, type Accessor, type JSX } from "solid-js";

export function WorkspaceVirtualList<T>(props: {
  items: readonly T[];
  itemKey: (item: T) => string;
  estimateSize: number;
  gap?: number;
  class: string;
  busy?: boolean;
  children: (item: Accessor<T>) => JSX.Element;
}) {
  let scrollElement: HTMLDivElement | undefined;
  const itemsByKey = createMemo(() => new Map(props.items.map(item => [props.itemKey(item), item])));
  const itemKeys = createMemo(() => Array.from(itemsByKey().keys()));
  const getItemKey = createMemo(() => {
    const keys = itemKeys();
    return (index: number) => keys[index];
  });
  const virtualizer = createVirtualizer<HTMLDivElement, HTMLDivElement>({
    get count() { return itemKeys().length; },
    // Resolve the ref on mount, after Solid adopts it into the live document.
    getScrollElement: () => scrollElement ?? null,
    get getItemKey() { return getItemKey(); },
    estimateSize: () => props.estimateSize,
    get gap() { return props.gap ?? 0; },
    overscan: 6,
  });
  const virtualRows = createMemo(() => new Map(
    virtualizer.getVirtualItems().map(row => [String(row.key), { index: row.index, start: row.start }]),
  ));
  const virtualKeys = createMemo(() => Array.from(virtualRows().keys()));

  createEffect(() => {
    const element = scrollElement;
    if (!element) return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (width === element.clientWidth) return;
      width = element.clientWidth;
      virtualizer.measure();
    });
    observer.observe(element);
    onCleanup(() => observer.disconnect());
  });

  return (
    <div ref={scrollElement} class={props.class} aria-busy={props.busy}>
      <div class="workspace-virtual-content" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        <For each={virtualKeys()}>
          {key => {
            // Retain outgoing values until Solid removes the keyed row.
            const item = createMemo<T>(previous => itemsByKey().get(key) ?? previous!);
            const row = createMemo<{ index: number; start: number }>(previous => virtualRows().get(key) ?? previous!);
            let element!: HTMLDivElement;
            createEffect(() => {
              row().index;
              virtualizer.measureElement(element);
            });
            onCleanup(() => virtualizer.measureElement(null));
            return (
              <div
                ref={element}
                class="workspace-virtual-row"
                data-index={row().index}
                style={{ top: `${row().start}px` }}
              >
                {props.children(item)}
              </div>
            );
          }}
        </For>
      </div>
    </div>
  );
}
