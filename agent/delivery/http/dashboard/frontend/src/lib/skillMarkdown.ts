type SkillFrontmatter = {
  name: string;
  description: string;
  license?: string;
  compatibility?: string;
  allowed_tools?: string[];
  metadata?: Record<string, string>;
};

type ParsedSkill = {
  frontmatter: SkillFrontmatter;
  body: string;
};

const FRONTMATTER_RE = /\A\s*---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)\z/;

function unquote(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length >= 2) {
    const first = trimmed[0];
    const last = trimmed[trimmed.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return trimmed.slice(1, -1);
    }
  }
  return trimmed;
}

function parseFrontmatterYaml(yaml: string): SkillFrontmatter {
  const lines = yaml.split(/\r?\n/);
  const result: SkillFrontmatter = { name: "", description: "" };
  let i = 0;

  const collectIndented = (start: number): { block: string[]; next: number } => {
    const block: string[] = [];
    let j = start;
    while (j < lines.length) {
      const raw = lines[j];
      if (raw.trim() === "") {
        j++;
        continue;
      }
      if (/^\s/.test(raw)) {
        block.push(raw);
        j++;
        continue;
      }
      break;
    }
    return { block, next: j };
  };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (trimmed === "" || trimmed.startsWith("#")) {
      i++;
      continue;
    }
    const match = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(trimmed);
    if (!match) {
      i++;
      continue;
    }
    const key = match[1];
    const value = match[2];

    if (value === "" || value === undefined) {
      const { block, next } = collectIndented(i + 1);
      if (block.length > 0) {
        if (block[0].trim().startsWith("- ")) {
          const items: string[] = [];
          for (const entry of block) {
            const itemMatch = /^\s*-\s+(.*)$/.exec(entry);
            if (itemMatch) {
              items.push(unquote(itemMatch[1]));
            }
          }
          if (key === "allowed-tools" || key === "allowed_tools") {
            result.allowed_tools = items;
          } else {
            (result as Record<string, unknown>)[key] = items;
          }
        } else {
          const dict: Record<string, string> = {};
          for (const entry of block) {
            const dm = /^\s*([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(entry);
            if (dm) {
              dict[dm[1]] = unquote(dm[2]);
            }
          }
          if (key === "metadata") {
            result.metadata = dict;
          } else {
            (result as Record<string, unknown>)[key] = dict;
          }
        }
        i = next;
        continue;
      }
      (result as Record<string, unknown>)[key] = "";
    } else {
      if (key === "allowed-tools" || key === "allowed_tools") {
        result.allowed_tools = value.trim().split(/\s+/).filter(Boolean).map(unquote);
      } else if (key === "name") {
        result.name = unquote(value);
      } else if (key === "description") {
        result.description = unquote(value);
      } else if (key === "license") {
        result.license = unquote(value);
      } else if (key === "compatibility") {
        result.compatibility = unquote(value);
      } else {
        (result as Record<string, unknown>)[key] = unquote(value);
      }
    }
    i++;
  }

  return result;
}

function parseSkillMarkdown(content: string): ParsedSkill {
  const text = String(content || "");
  const match = FRONTMATTER_RE.exec(text);
  if (!match) {
    return {
      frontmatter: { name: "", description: "" },
      body: text.trim(),
    };
  }
  const frontmatter = parseFrontmatterYaml(match[1]);
  const body = match[2].trim();
  return { frontmatter, body };
}

function escapeScalar(value: string): string {
  if (value === "") {
    return '""';
  }
  if (/^[A-Za-z0-9_\-./]+$/.test(value)) {
    return value;
  }
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function serializeFrontmatter(frontmatter: SkillFrontmatter): string {
  const lines: string[] = ["---"];
  lines.push(`name: ${escapeScalar(frontmatter.name)}`);
  lines.push(`description: ${escapeScalar(frontmatter.description)}`);
  if (frontmatter.license && frontmatter.license.trim()) {
    lines.push(`license: ${escapeScalar(frontmatter.license)}`);
  }
  if (frontmatter.compatibility && frontmatter.compatibility.trim()) {
    lines.push(`compatibility: ${escapeScalar(frontmatter.compatibility)}`);
  }
  if (frontmatter.allowed_tools && frontmatter.allowed_tools.length > 0) {
    lines.push(`allowed-tools: ${frontmatter.allowed_tools.map(escapeScalar).join(" ")}`);
  }
  if (frontmatter.metadata && Object.keys(frontmatter.metadata).length > 0) {
    lines.push("metadata:");
    for (const [key, value] of Object.entries(frontmatter.metadata)) {
      if (key.trim() === "") {
        continue;
      }
      lines.push(`  ${escapeScalar(key)}: ${escapeScalar(value ?? "")}`);
    }
  }
  lines.push("---");
  return lines.join("\n");
}

function serializeSkillMarkdown(frontmatter: SkillFrontmatter, body: string): string {
  const front = serializeFrontmatter(frontmatter);
  const trimmedBody = String(body || "").trim();
  if (!trimmedBody) {
    return `${front}\n`;
  }
  return `${front}\n\n${trimmedBody}\n`;
}

export {
  parseSkillMarkdown,
  serializeSkillMarkdown,
};

export type { ParsedSkill, SkillFrontmatter };
