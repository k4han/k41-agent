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
  Terminal,
  Cpu,
  FileText,
  FileCode,
  Trash2,
  Play
} from "lucide-solid";

// Helper lấy Icon cho Server dựa trên tên server
export function getServerIcon(name: string) {
  const norm = name.toLowerCase();
  if (norm.includes("github")) return <Github size={16} />;
  if (norm.includes("gitlab")) return <Gitlab size={16} />;
  if (norm.includes("slack")) return <Slack size={16} />;
  if (norm.includes("filesystem") || norm.includes("file")) return <Folder size={16} />;
  if (norm.includes("postgres")) return <Database size={16} />;
  if (norm.includes("sqlite")) return <Database size={16} />;
  if (norm.includes("gdrive") || norm.includes("google")) return <Cloud size={16} />;
  if (norm.includes("memory")) return <Brain size={16} />;
  if (norm.includes("sequential")) return <Brain size={16} />;
  if (norm.includes("search") || norm.includes("brave")) return <Search size={16} />;
  if (norm.includes("puppeteer")) return <Globe size={16} />;
  if (norm.includes("fetch")) return <Globe size={16} />;
  if (norm.includes("time")) return <Clock size={16} />;
  if (norm.includes("sentry")) return <Bug size={16} />;
  return <Server size={16} />;
}

// Helper lấy Icon cho Tool dựa trên tên tool
export function getToolIcon(toolName: string, serverName: string) {
  const norm = toolName.toLowerCase();
  
  // Ánh xạ theo hành vi của tool
  if (norm.includes("read") || norm.includes("get") || norm.includes("view") || norm.includes("show") || norm.includes("fetch")) {
    return <FileText size={14} style={{ color: "var(--color-primary-light, #0076ff)", opacity: 0.9 }} />;
  }
  if (norm.includes("write") || norm.includes("create") || norm.includes("save") || norm.includes("update") || norm.includes("edit") || norm.includes("patch") || norm.includes("set")) {
    return <FileCode size={14} style={{ color: "#10b981", opacity: 0.9 }} />; // Emerald green
  }
  if (norm.includes("delete") || norm.includes("remove") || norm.includes("clear") || norm.includes("destroy") || norm.includes("unset")) {
    return <Trash2 size={14} style={{ color: "#ef4444", opacity: 0.9 }} />; // Red
  }
  if (norm.includes("search") || norm.includes("find") || norm.includes("query") || norm.includes("list") || norm.includes("browse")) {
    return <Search size={14} style={{ color: "#f59e0b", opacity: 0.9 }} />; // Amber
  }
  if (norm.includes("run") || norm.includes("execute") || norm.includes("bash") || norm.includes("shell") || norm.includes("cmd") || norm.includes("command") || norm.includes("think") || norm.includes("solve")) {
    return <Play size={14} style={{ color: "#8b5cf6", opacity: 0.9 }} />; // Purple
  }
  
  // Dùng fallback là icon của chính Server đó nhưng size nhỏ hơn
  const serverNorm = serverName.toLowerCase();
  if (serverNorm.includes("github")) return <Github size={14} style={{ opacity: 0.7 }} />;
  if (serverNorm.includes("gitlab")) return <Gitlab size={14} style={{ opacity: 0.7 }} />;
  if (serverNorm.includes("slack")) return <Slack size={14} style={{ opacity: 0.7 }} />;
  if (serverNorm.includes("filesystem")) return <Folder size={14} style={{ opacity: 0.7 }} />;
  if (serverNorm.includes("postgres") || serverNorm.includes("sqlite")) return <Database size={14} style={{ opacity: 0.7 }} />;
  return <Cpu size={14} style={{ opacity: 0.7 }} />;
}
