import type { TranscriptAttachment } from "@/components/Transcript";
import type {
  BackgroundTask,
  ActiveSession,
  WorkspaceBinding,
  WorkspaceRef,
} from "@/types";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import type { UserAnswerResumePayload } from "@/lib/userInputRequest";

// ── Attachment types ──

export type ChatAttachmentKind = "text" | "image" | "file";

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

export type ReasoningEffort = string;

export type ChatPayload = {
  message: string;
  user_id: string;
  agent_name: string;
  workspace?: WorkspaceRef | WorkspaceBinding;
  provider?: string;
  model?: string;
  reasoning_effort?: ReasoningEffort;
  thread_id?: string;
  new_thread?: boolean;
  checkpoint_id?: string;
  attachments?: ChatAttachmentPayload[];
  resume?: boolean;
  resume_payload?: ChatResumePayload;
};

export type PlanResumePayload =
  | { action: "approve"; target_agent: string }
  | { action: "revise"; feedback: string };

export type ChatResumePayload = PlanResumePayload | UserAnswerResumePayload | { action: "permission"; request_id: string; decision: "allow_once" | "allow_thread" | "deny" };

// ── Scroll & streaming ──

export type AppendScrollMode = "bottom" | "turn-start" | "none";

// ── Background task ──

export type BackgroundTaskSnapshot = ThreadMessagesPayload & {
  task?: BackgroundTask | null;
  active_session?: ActiveSession | null;
};

// ── Workspace ──

export type DefaultWorkspacePayload = {
  workspace: WorkspaceBinding;
};

export type WorkspaceResolvePayload = {
  kind: string;
  label: string;
  workspace: WorkspaceBinding;
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

export const MAX_ATTACHMENTS = 10;
export const PASTE_AS_ATTACHMENT_THRESHOLD = 200;
export const MAX_TEXT_ATTACHMENT_BYTES = 30 * 1024 * 1024;
export const MAX_IMAGE_ATTACHMENT_BYTES = 30 * 1024 * 1024;
export const MAX_FILE_ATTACHMENT_BYTES = 30 * 1024 * 1024;
export const MAX_TOTAL_ATTACHMENT_BYTES = 60 * 1024 * 1024;
export const DEFAULT_ATTACHMENT_MESSAGE = "Please review the attached file(s).";

export const WORKSPACE_EXPLORER_OPEN_KEY = "k41-dashboard-workspace-explorer-open";
export const WORKSPACE_EXPLORER_WIDTH_KEY = "k41-dashboard-workspace-explorer-width";
export const WORKSPACE_EXPLORER_DEFAULT_WIDTH = 560;
export const WORKSPACE_EXPLORER_MIN_WIDTH = 340;
export const WORKSPACE_EXPLORER_MAX_WIDTH = 1200;
export const CHAT_COMPOSER_MAX_WIDTH = 960;
export const CHAT_COMPOSER_SIDE_GAP = 16;
export const CHAT_PANEL_MIN_WIDTH = 380;

export const ATTACHMENT_ACCEPT = [
  "image/*",
  // Documents & Spreadsheets
  ".xlsx",
  ".xls",
  ".csv",
  ".tsv",
  ".parquet",
  ".pdf",
  ".docx",
  ".doc",
  ".pptx",
  ".ppt",
  ".odt",
  ".ods",
  // Text & Data
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".xml",
  ".log",
  ".sql",
  ".sqlite",
  ".db",
  // Archives
  ".zip",
  ".tar",
  ".gz",
  ".tgz",
  ".7z",
  ".rar",
  // Media
  ".mp3",
  ".wav",
  ".ogg",
  ".m4a",
  ".mp4",
  ".webm",
  ".mov",
  // Code & Config
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
