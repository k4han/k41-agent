import { createVirtualizer } from "@tanstack/solid-virtual";
import { createEffect, createMemo, For, on, onCleanup, Show } from "solid-js";

const COLUMN_WIDTH = 200;

export function WorkspaceTablePreview(props: { rows: string[][] }) {
  let scrollElement: HTMLDivElement | undefined;
  // Keep column widths stable as rows enter and leave the viewport.
  const columns = createMemo(() => Array.from({ length: Math.max(0, ...props.rows.map(row => row.length)) }, (_, index) => index));
  const virtualizer = createVirtualizer<HTMLDivElement, HTMLTableRowElement>({
    get count() { return props.rows.length; },
    getScrollElement: () => scrollElement ?? null,
    estimateSize: () => 36,
    overscan: 4,
  });
  const virtualRows = createMemo(() => new Map(
    virtualizer.getVirtualItems().map(row => [row.index, { start: row.start, end: row.end }]),
  ));
  const virtualKeys = createMemo(() => Array.from(virtualRows().keys()));
  const paddingTop = () => virtualRows().get(virtualKeys()[0])?.start ?? 0;
  const paddingBottom = () => Math.max(0, virtualizer.getTotalSize() - (virtualRows().get(virtualKeys().at(-1)!)?.end ?? 0));

  createEffect(on(() => props.rows, () => {
    virtualizer.measure();
    scrollElement?.scrollTo(0, 0);
    virtualizer.scrollToOffset(0);
  }));

  return (
    <div ref={scrollElement} class="workspace-table-preview" tabIndex={0} role="region" aria-label="Table preview">
      <Show when={props.rows.length} fallback={<div class="empty compact">Empty table.</div>}>
        <table style={{ width: `${columns().length * COLUMN_WIDTH}px` }} aria-rowcount={props.rows.length} aria-colcount={columns().length}>
          <colgroup><For each={columns()}>{() => <col style={{ width: `${COLUMN_WIDTH}px` }} />}</For></colgroup>
          <tbody>
            <Show when={paddingTop() > 0}>
              <tr aria-hidden="true"><td class="workspace-table-spacer" colSpan={columns().length} style={{ height: `${paddingTop()}px` }} /></tr>
            </Show>
            <For each={virtualKeys()}>
              {key => {
                // Retain outgoing values until Solid removes the keyed row.
                const row = createMemo<string[]>(previous => props.rows[key] ?? previous!);
                let element!: HTMLTableRowElement;
                createEffect(() => {
                  row();
                  virtualizer.measureElement(element);
                });
                onCleanup(() => virtualizer.measureElement(null));
                return (
                  <tr ref={element} data-index={key} aria-rowindex={key + 1}>
                    <For each={row()}>{cell => <td>{cell}</td>}</For>
                  </tr>
                );
              }}
            </For>
            <Show when={paddingBottom() > 0}>
              <tr aria-hidden="true"><td class="workspace-table-spacer" colSpan={columns().length} style={{ height: `${paddingBottom()}px` }} /></tr>
            </Show>
          </tbody>
        </table>
      </Show>
    </div>
  );
}
