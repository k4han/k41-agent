export function EmptyTableRow(props: { colSpan: number; message?: string }) {
  return (
    <tr>
      <td colSpan={props.colSpan}>
        <div class="empty">{props.message || "No data available."}</div>
      </td>
    </tr>
  );
}
