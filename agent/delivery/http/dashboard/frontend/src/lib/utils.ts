export function classNames(
  ...values: Array<string | false | null | undefined>
): string {
  return values.filter(Boolean).join(" ");
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "(empty)";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

export function parseModelList(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function uniqueSorted(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.filter(Boolean) as string[])).sort();
}

export function statusBadgeClass(status: string): string {
  if (["running", "completed", "success", "valid", "active"].includes(status)) {
    return "badge badge-success";
  }
  if (["failed", "error", "invalid"].includes(status)) {
    return "badge badge-danger";
  }
  if (["pending", "starting", "stopping", "paused", "cancelled"].includes(status)) {
    return "badge badge-warning";
  }
  return "badge";
}

export function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength)}...`;
}

export function dateTimeLocal(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
  ].join("");
}

export function triggerArgsFromDateInput(value: string): { run_date: string } | null {
  if (!value) {
    return null;
  }
  const normalized = value.replace("T", " ");
  return { run_date: normalized.length === 16 ? `${normalized}:00` : normalized };
}

