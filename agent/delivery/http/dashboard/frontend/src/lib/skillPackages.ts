import type { WorkspaceRef } from "@/types";

export type SkillDiagnostic = { code?: string; message: string; severity: string };
export type SkillPackageSummary = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  shadowed: boolean;
  source: { scope: string; root: string; directory: string };
  diagnostics: SkillDiagnostic[];
};
export type SkillPackageDetail = SkillPackageSummary & {
  valid: boolean;
  content: string;
  file_version: string;
  frontmatter: Record<string, unknown>;
  body: string;
};
export type SkillEntry = { path: string; name: string; kind: string; size: number };
export type SkillFileDetail = {
  path: string;
  content: string | null;
  version: string;
  mime_type: string;
  size: number;
  preview_truncated?: boolean;
};

export function skillPackageUrl(workspace: WorkspaceRef, path = "", options: Record<string, string> = {}) {
  const query = new URLSearchParams({ workspace: JSON.stringify(workspace), ...options });
  return `/dashboard-api/skill-packages${path}?${query}`;
}

export function skillResourcePath(identity: string, path: string) {
  return `/${encodeURIComponent(identity)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}
