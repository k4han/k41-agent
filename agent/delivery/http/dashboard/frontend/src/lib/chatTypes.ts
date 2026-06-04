import type { TranscriptAttachment } from "@/components/Transcript";
import type {
  BackgroundTask,
  ActiveSession,
  WorkspaceRef,
} from "@/types";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";

// ── Attachment types ──

export type ChatAttachmentKind = "text" | "image";

export type ChatAttachmentPayload = {
  name: string;
  mime_type: string;
  size: number;
  kind: ChatAttachmentKind;
  content?: string;
  base64?: string;
};

export type PendingAttachment = ChatAttachmentPayload & {
  id: number;
  preview_url?: string;
};

// ── Chat payload ──

export type ChatPayload = {
  message: string;
  user_id: string;
  agent_name: string;
  workspace?: WorkspaceRef;
  provider?: string;
  model?: string;
  thread_id?: string;
  new_thread?: boolean;
  checkpoint_id?: string;
  attachments?: ChatAttachmentPayload[];
};

// ── Scroll & streaming ──

export type AppendScrollMode = "bottom" | "turn-start" | "none";

// ── Background task ──

export type BackgroundTaskSnapshot = ThreadMessagesPayload & {
  task?: BackgroundTask | null;
  active_session?: ActiveSession | null;
};

// ── Workspace ──

export type DefaultWorkspacePayload = {
  workspace: WorkspaceRef;
};

export type WorkspaceResolvePayload = {
  kind: string;
  label: string;
  workspace: WorkspaceRef;
};

export type WorkspaceBrowseEntry = {
  name: string;
  path: string;
};

export type WorkspaceBrowsePayload = {
  path: string;
  parent: string;
  entries: WorkspaceBrowseEntry[];
  roots: WorkspaceBrowseEntry[];
  truncated: boolean;
};

// ── Constants ──

export const MAX_ATTACHMENTS = 5;
export const MAX_TEXT_ATTACHMENT_BYTES = 100 * 1024;
export const MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024;
export const MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024;
export const DEFAULT_ATTACHMENT_MESSAGE = "Please review the attached file(s).";

export const WORKSPACE_EXPLORER_OPEN_KEY = "kaka-dashboard-workspace-explorer-open";
export const WORKSPACE_EXPLORER_WIDTH_KEY = "kaka-dashboard-workspace-explorer-width";
export const WORKSPACE_EXPLORER_DEFAULT_WIDTH = 560;
export const WORKSPACE_EXPLORER_MIN_WIDTH = 340;
export const WORKSPACE_EXPLORER_MAX_WIDTH = 920;

export const ATTACHMENT_ACCEPT = [
  "image/*",
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".xml",
  ".csv",
  ".html",
  ".css",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".py",
  ".go",
  ".rs",
  ".java",
  ".c",
  ".cpp",
  ".h",
  ".hpp",
  ".cs",
  ".php",
  ".rb",
  ".swift",
  ".kt",
  ".kts",
  ".dart",
  ".sql",
  ".sh",
  ".ps1",
  ".bat",
  ".env",
  ".gitignore",
  "Dockerfile",
].join(",");

export const TEXT_MIME_TYPES = new Set([
  "application/javascript",
  "application/json",
  "application/toml",
  "application/typescript",
  "application/xml",
  "application/x-yaml",
  "text/javascript",
]);

export const TEXT_EXTENSIONS = new Set([
  ".bat",
  ".c",
  ".cpp",
  ".cs",
  ".css",
  ".csv",
  ".dart",
  ".env",
  ".gitignore",
  ".go",
  ".h",
  ".hpp",
  ".html",
  ".java",
  ".js",
  ".json",
  ".jsx",
  ".kt",
  ".kts",
  ".md",
  ".markdown",
  ".php",
  ".ps1",
  ".py",
  ".rb",
  ".rs",
  ".sh",
  ".sql",
  ".swift",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
  "dockerfile",
]);
