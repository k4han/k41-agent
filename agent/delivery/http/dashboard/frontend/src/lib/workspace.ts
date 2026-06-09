import type { WorkspaceRef } from "../types";
import { isSandboxBackend } from "../types";

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

export function isGitHubWorkspace(
  workspace: WorkspaceRef | null | undefined,
): boolean {
  if (!workspace) {
    return false;
  }
  const source = workspace.metadata?.source;
  return typeof source === "string" && source.trim().toLowerCase() === "github";
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

export function daytonaWorkspaceRef(locator: string, root = "workspace"): WorkspaceRef | null {
  const trimmed = locator.trim();
  if (!trimmed) {
    return null;
  }
  return {
    backend: "daytona",
    locator: trimmed,
    label: `daytona:${trimmed}`,
    metadata: { root: root.trim() || "workspace" },
  };
}

export function modalWorkspaceRef(locator: string, root = "/workspace"): WorkspaceRef | null {
  const trimmed = locator.trim();
  if (!trimmed) {
    return null;
  }
  return {
    backend: "modal",
    locator: trimmed,
    label: `modal:${trimmed}`,
    metadata: { root: root.trim() || "/workspace" },
  };
}

export function formatWorkspaceRoot(locator: string): string {
  const trimmed = locator.trim();
  if (!trimmed) {
    return "";
  }

  const compactTail = compactPathTail(trimmed);
  return compactTail ? `${compactTail}/` : trimmed;
}

export function workspaceDisplayLabelFromValues(
  label: string | undefined,
  locator: string | undefined,
  metadata?: Record<string, unknown>,
  backend: WorkspaceRef["backend"] = "local",
): string {
  if (isSandboxBackend(backend)) {
    const repository = metadataText(metadata, "repository_full_name");
    if (repository) {
      return repository;
    }
    const trimmedLabel = (label || "").trim();
    if (trimmedLabel) {
      return trimmedLabel;
    }
    const trimmedLocator = (locator || "").trim();
    const root = metadataText(metadata, "root");
    return root ? `${backend}:${trimmedLocator}:${root}` : `${backend}:${trimmedLocator}`;
  }

  const repository = metadataText(metadata, "repository_full_name") || metadataText(metadata, "repository");
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
    workspace.backend,
  );
}

function metadataRoot(metadata: Record<string, unknown> | undefined): string {
  const value = metadata?.root;
  return typeof value === "string" ? value.trim() : "";
}

export function resolveWorkspaceWorkingDir(
  workspace: WorkspaceRef | null | undefined,
): string {
  /*
   * Return the on-disk path the workspace backend uses as its cwd.
   *
   * For Daytona/Modal sandboxes the locator is a sandbox ID and is not a
   * usable filesystem path. Prefer metadata.root (which is updated to live
   * inside a cloned repository when a GitHub repo is attached) so the value
   * shown to the user matches the actual subprocess cwd.
   */
  if (!workspace) {
    return "";
  }
  if (isSandboxBackend(workspace.backend)) {
    const root = metadataRoot(workspace.metadata);
    if (root) {
      return root;
    }
  }
  return workspace.locator.trim();
}
