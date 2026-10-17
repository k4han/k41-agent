export type WorkspacePreviewKind = "image" | "pdf" | "audio" | "video" | "markdown" | "html" | "table" | "text" | "unsupported";

export function workspacePreviewKind(path: string, binary: boolean): WorkspacePreviewKind {
  const name = path.replace(/\\/g, "/").split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  if (["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico", "svg"].includes(extension)) return "image";
  if (extension === "pdf") return "pdf";
  if (["mp3", "wav", "ogg", "oga", "m4a", "flac", "aac"].includes(extension)) return "audio";
  if (["mp4", "webm", "ogv", "mov"].includes(extension)) return "video";
  if (binary) return "unsupported";
  if (["md", "markdown", "mdown"].includes(extension)) return "markdown";
  if (["html", "htm"].includes(extension)) return "html";
  if (["csv", "tsv"].includes(extension)) return "table";
  return "text";
}

export function parseDelimitedPreview(content: string, delimiter: string, maxRows = 200, maxColumns = 100) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let truncated = false;
  const pushCell = () => {
    if (row.length < maxColumns) row.push(cell);
    else truncated = true;
    cell = "";
  };
  const source = content.replace(/^\uFEFF/, "");
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (char === '"') {
      if (quoted && source[index + 1] === '"') { cell += '"'; index += 1; }
      else if (quoted || !cell) quoted = !quoted;
      else cell += char;
    } else if (char === delimiter && !quoted) {
      pushCell();
    } else if ((char === "\n" || char === "\r") && !quoted) {
      pushCell();
      rows.push(row);
      row = [];
      if (char === "\r" && source[index + 1] === "\n") index += 1;
      if (rows.length >= maxRows) return { rows, truncated: truncated || index < source.length - 1 };
    } else cell += char;
  }
  if (cell || row.length) { pushCell(); rows.push(row); }
  return { rows, truncated };
}

export function sandboxedWorkspaceHtml(content: string): string {
  return `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'">${content}`;
}
