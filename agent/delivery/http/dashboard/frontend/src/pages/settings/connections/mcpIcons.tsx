import {
  Folder,
  Github,
  Gitlab,
  Database,
  Slack,
  Brain,
  Search,
  Globe,
  Clock,
  Bug,
  Server,
  Cloud,
  Cpu,
  FileText,
  FileCode,
  Trash2,
  Play,
  type LucideIcon,
} from "lucide-solid";

type IconEntry = { pattern: RegExp; icon: LucideIcon; color?: string; opacity?: number };

const SERVER_ICONS: IconEntry[] = [
  { pattern: /github/i, icon: Github },
  { pattern: /gitlab/i, icon: Gitlab },
  { pattern: /slack/i, icon: Slack },
  { pattern: /(filesystem|file)/i, icon: Folder },
  { pattern: /(postgres|sqlite)/i, icon: Database },
  { pattern: /(gdrive|google)/i, icon: Cloud },
  { pattern: /(memory|sequential)/i, icon: Brain },
  { pattern: /(search|brave)/i, icon: Search },
  { pattern: /(puppeteer|fetch)/i, icon: Globe },
  { pattern: /time/i, icon: Clock },
  { pattern: /sentry/i, icon: Bug },
];

const TOOL_ICONS: IconEntry[] = [
  {
    pattern: /(read|get|view|show|fetch)/i,
    icon: FileText,
    color: "var(--color-primary-light, #0076ff)",
    opacity: 0.9,
  },
  {
    pattern: /(write|create|save|update|edit|patch|set)/i,
    icon: FileCode,
    color: "#10b981",
    opacity: 0.9,
  },
  {
    pattern: /(delete|remove|clear|destroy|unset)/i,
    icon: Trash2,
    color: "#ef4444",
    opacity: 0.9,
  },
  {
    pattern: /(search|find|query|list|browse)/i,
    icon: Search,
    color: "#f59e0b",
    opacity: 0.9,
  },
  {
    pattern: /(run|execute|bash|shell|cmd|command|think|solve)/i,
    icon: Play,
    color: "#8b5cf6",
    opacity: 0.9,
  },
];

const SERVER_FALLBACK_ICONS: IconEntry[] = [
  { pattern: /github/i, icon: Github, opacity: 0.7 },
  { pattern: /gitlab/i, icon: Gitlab, opacity: 0.7 },
  { pattern: /slack/i, icon: Slack, opacity: 0.7 },
  { pattern: /filesystem/i, icon: Folder, opacity: 0.7 },
  { pattern: /(postgres|sqlite)/i, icon: Database, opacity: 0.7 },
];

function resolve(entries: IconEntry[], name: string, fallback: LucideIcon, fallbackOpacity = 0.7): IconEntry {
  return entries.find((entry) => entry.pattern.test(name)) ?? { pattern: /^.$/, icon: fallback, opacity: fallbackOpacity };
}

function renderIcon(entry: IconEntry, size: number) {
  const style: Record<string, string> = {};
  if (entry.color) {
    style.color = entry.color;
  }
  if (entry.opacity !== undefined) {
    style.opacity = String(entry.opacity);
  }
  const Icon = entry.icon;
  return <Icon size={size} style={style} />;
}

export function getServerIcon(name: string) {
  return renderIcon(resolve(SERVER_ICONS, name, Server), 16);
}

export function getToolIcon(toolName: string, serverName: string) {
  const toolMatch = TOOL_ICONS.find((entry) => entry.pattern.test(toolName));
  if (toolMatch) {
    return renderIcon(toolMatch, 14);
  }
  return renderIcon(resolve(SERVER_FALLBACK_ICONS, serverName, Cpu), 14);
}
