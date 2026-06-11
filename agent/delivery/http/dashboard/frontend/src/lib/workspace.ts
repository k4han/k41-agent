import type { SandboxBackendKey, WorkspaceBinding, WorkspaceRef, WorkspaceScope } from "../types";
import { isSandboxBackend, sandboxBackendDefaultRoot } from "../types";

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

export function workspaceExecution(
  workspace: WorkspaceBinding | WorkspaceRef | null | undefined,
): WorkspaceRef | null {
  if (!workspace) {
    return null;
  }
  if ("execution" in workspace) {
    return workspace.execution;
  }
  return workspace;
}

export function deriveWorkspaceScope(execution: WorkspaceRef): WorkspaceScope {
  const source = typeof execution.metadata.source === "string"
    ? execution.metadata.source.trim().toLowerCase()
    : "";
  if (source === "github") {
    const repositoryId = String(execution.metadata.repository_id || "").trim();
    const repositoryFullName = metadataText(execution.metadata, "repository_full_name")
      || metadataText(execution.metadata, "repository");
    return {
      key: repositoryId
        ? `github:${repositoryId}`
        : `github:${repositoryFullName.toLowerCase() || `${execution.backend}:${execution.locator}`}`,
      kind: "github",
      label: repositoryFullName || execution.label || execution.locator,
      metadata: {
        source: "github",
        repository_id: execution.metadata.repository_id,
        repository_full_name: repositoryFullName,
      },
    };
  }
  if (execution.backend === "local") {
    return {
      key: `local:${execution.locator}`,
      kind: "local",
      label: workspaceDisplayLabelFromValues(
        execution.label,
        execution.locator,
        execution.metadata,
        execution.backend,
      ),
      metadata: {},
    };
  }
  return {
    key: `sandbox:${execution.backend}:${execution.locator}`,
    kind: "sandbox",
    label: workspaceDisplayLabelFromValues(
      execution.label,
      execution.locator,
      execution.metadata,
      execution.backend,
    ),
    metadata: {
      backend: execution.backend,
      root: metadataText(execution.metadata, "root"),
    },
  };
}

export function bindWorkspaceRef(
  workspace: WorkspaceBinding | WorkspaceRef | null | undefined,
): WorkspaceBinding | null {
  if (!workspace) {
    return null;
  }
  if ("scope" in workspace) {
    return workspace;
  }
  return {
    scope: deriveWorkspaceScope(workspace),
    execution: workspace,
  };
}

export function isGitHubWorkspace(
  workspace: WorkspaceBinding | WorkspaceRef | null | undefined,
): boolean {
  const execution = workspaceExecution(workspace);
  if (!execution) {
    return false;
  }
  const source = execution.metadata?.source;
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

export function sandboxWorkspaceRef(
  backend: SandboxBackendKey,
  locator: string,
  root?: string,
): WorkspaceRef | null {
  const trimmed = locator.trim();
  if (!trimmed) {
    return null;
  }
  const resolvedRoot = root?.trim() || sandboxBackendDefaultRoot(backend);
  return {
    backend,
    locator: trimmed,
    label: `${backend}:${trimmed}`,
    metadata: { root: resolvedRoot },
  };
}

export function daytonaWorkspaceRef(locator: string, root = "workspace"): WorkspaceRef | null {
  return sandboxWorkspaceRef("daytona", locator, root);
}

export function modalWorkspaceRef(locator: string, root = "/workspace"): WorkspaceRef | null {
  return sandboxWorkspaceRef("modal", locator, root);
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

export function workspaceDisplayLabel(
  workspace: WorkspaceBinding | WorkspaceRef | null | undefined,
): string {
  if (!workspace) {
    return "";
  }
  if ("scope" in workspace) {
    return workspace.scope.label;
  }
  const execution = workspaceExecution(workspace);
  if (!execution) {
    return "";
  }
  return workspaceDisplayLabelFromValues(
    execution.label,
    execution.locator,
    execution.metadata,
    execution.backend,
  );
}

function metadataRoot(metadata: Record<string, unknown> | undefined): string {
  const value = metadata?.root;
  return typeof value === "string" ? value.trim() : "";
}

export function resolveWorkspaceWorkingDir(
  workspace: WorkspaceBinding | WorkspaceRef | null | undefined,
): string {
  /*
   * Return the on-disk path the workspace backend uses as its cwd.
   *
   * For Daytona/Modal sandboxes the locator is a sandbox ID and is not a
   * usable filesystem path. Prefer metadata.root (which is updated to live
   * inside a cloned repository when a GitHub repo is attached) so the value
   * shown to the user matches the actual subprocess cwd.
   */
  const execution = workspaceExecution(workspace);
  if (!execution) {
    return "";
  }
  if (isSandboxBackend(execution.backend)) {
    const root = metadataRoot(execution.metadata);
    if (root) {
      return root;
    }
  }
  return execution.locator.trim();
}
