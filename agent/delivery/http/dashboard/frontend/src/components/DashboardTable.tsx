import { For } from "solid-js";
import type { JSX } from "solid-js";

import { EmptyTableRow } from "./EmptyTableRow";

export type DashboardTableColumn = {
  header?: JSX.Element;
  class?: string;
  style?: JSX.CSSProperties | string;
};

type DashboardTableProps<T> = {
  columns: readonly DashboardTableColumn[];
  rows: readonly T[];
  tableClass?: string;
  emptyMessage?: string;
  emptyFallback?: JSX.Element;
  children: (row: T, index: () => number) => JSX.Element;
};

export function DashboardTable<T>(props: DashboardTableProps<T>) {
  return (
    <div class="table-wrap">
      <table class={props.tableClass ? `table ${props.tableClass}` : "table"}>
        <thead>
          <tr>
            <For each={props.columns}>
              {(column) => (
                <th class={column.class} style={column.style}>
                  {column.header}
                </th>
              )}
            </For>
          </tr>
        </thead>
        <tbody>
          <For
            each={props.rows}
            fallback={
              props.emptyFallback || (
                <EmptyTableRow
                  colSpan={props.columns.length}
                  message={props.emptyMessage}
                />
              )
            }
          >
            {props.children}
          </For>
        </tbody>
      </table>
    </div>
  );
}
