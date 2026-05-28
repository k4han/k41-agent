import type { WorkspaceRef } from "../types";

function normalizePath(value: string): string {
  return value.trim().replace(/\\/g, "/").replace(/\/+$/, "");
}

function comparablePath(value: string): string {
  const normalized = normalizePath(value);
  return /^[A-Za-z]:/.test(normalized) ? normalized.toLowerCase() : normalized;
}

function isAbsoluteLocalPath(value: string): boolean {
  const trimmed = value.trim();
  return (
    /^[A-Za-z]:[\\/]/.test(trimmed)
    || trimmed.startsWith("/")
    || trimmed.startsWith("\\\\")
    || trimmed.startsWith("~")
  );
}

function compactPathTail(value: string): string {
  const normalized = normalizePath(value);
  if (!normalized) {
    return "";
  }
  const parts = normalized.split("/").filter(Boolean);
  if (!parts.length) {
    return "";
  }

  const leaf = parts[parts.length - 1];
  let start = parts.length - 1;
  while (start > 0 && parts[start - 1].toLowerCase() === leaf.toLowerCase()) {
    start -= 1;
  }
  return start < parts.length - 1 ? parts.slice(start).join("/") : leaf;
}

function metadataText(metadata: Record<string, unknown> | undefined, key: string): string {
  const value = metadata?.[key];
  return typeof value === "string" ? value.trim() : "";
}

export function localWorkspaceRef(locator: string): WorkspaceRef | null {
  const trimmed = locator.trim();
  if (!trimmed) {
    return null;
  }
  return {
    backend: "local",
    locator: trimmed,
    label: trimmed,
    metadata: {},
  };
}

export function formatWorkspaceRoot(locator: string): string {
  const compactTail = compactPathTail(locator);
  return compactTail ? `${compactTail}/` : locator.trim();
}

export function workspaceDisplayLabelFromValues(
  label: string | undefined,
  locator: string | undefined,
  metadata?: Record<string, unknown>,
): string {
  const repository = metadataText(metadata, "repository_full_name");
  if (repository) {
    return repository;
  }

  const trimmedLabel = (label || "").trim();
  const trimmedLocator = (locator || "").trim();
  if (!trimmedLabel && !trimmedLocator) {
    return "";
  }

  if (
    trimmedLabel
    && trimmedLocator
    && comparablePath(trimmedLabel) !== comparablePath(trimmedLocator)
    && !isAbsoluteLocalPath(trimmedLabel)
  ) {
    return trimmedLabel;
  }

  return formatWorkspaceRoot(trimmedLocator || trimmedLabel);
}

export function workspaceDisplayLabel(workspace: WorkspaceRef | null | undefined): string {
  if (!workspace) {
    return "";
  }
  return workspaceDisplayLabelFromValues(
    workspace.label,
    workspace.locator,
    workspace.metadata,
  );
}
