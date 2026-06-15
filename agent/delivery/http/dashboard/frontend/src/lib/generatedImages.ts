const GENERATED_IMAGE_EXTENSIONS = new Set(["gif", "jpeg", "jpg", "png", "webp"]);
const GENERATED_IMAGE_PATH_RE =
  /(^|[\s("'`])(?:(?:(?:[A-Za-z]:)?[\\/][^<>"'`\r\n]+)*[\\/])?\.k41-agent[\\/]generated-images[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp))(?=$|[\s)"'`,.;])/gi;
const GENERATED_IMAGE_TAIL_RE =
  /(?:^|[\\/])\.k41-agent[\\/]generated-images[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp))$/i;

export const GENERATE_IMAGE_TOOL_NAME = "generate_image";

export function generatedImageUrlFromFilename(filename: string, threadId?: string | null): string {
  const base = `/dashboard-api/generated-images/${encodeURIComponent(filename)}`;
  const normalizedThreadId = (threadId || "").trim();
  if (!normalizedThreadId) {
    return base;
  }
  return `${base}?thread_id=${encodeURIComponent(normalizedThreadId)}`;
}

export function generatedImageMimeTypeFromFilename(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase() || "";
  if (extension === "jpg" || extension === "jpeg") {
    return "image/jpeg";
  }
  if (extension === "webp") {
    return "image/webp";
  }
  if (extension === "gif") {
    return "image/gif";
  }
  return "image/png";
}

export function generatedImageUrlFromPath(value: unknown, threadId?: string | null): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const match = value.match(GENERATED_IMAGE_TAIL_RE);
  const filename = match?.[1] || "";
  const extension = filename.split(".").pop()?.toLowerCase() || "";
  if (!filename || !GENERATED_IMAGE_EXTENSIONS.has(extension)) {
    return null;
  }
  return generatedImageUrlFromFilename(filename, threadId);
}

export function generatedImageFromToolResult(
  result: unknown,
  threadId?: string | null,
): {
  filename: string;
  url: string;
} | null {
  const text = typeof result === "string" ? result : "";
  const match = text.match(
    /Generated image saved to:\s*(.+?[\\/](generated-images)[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp)))\s*$/i,
  );
  const filename = match?.[3] || "";
  const url = generatedImageUrlFromPath(match?.[1] || "", threadId);
  if (!filename || !url) {
    return null;
  }
  return { filename, url };
}

export function generatedImageAttachmentFromToolResult(
  result: unknown,
  threadId?: string | null,
): {
  name: string;
  mime_type: string;
  size: number;
  kind: "image";
  preview_url: string;
} | null {
  const image = generatedImageFromToolResult(result, threadId);
  if (!image) {
    return null;
  }
  return {
    name: image.filename,
    mime_type: generatedImageMimeTypeFromFilename(image.filename),
    size: 0,
    kind: "image",
    preview_url: image.url,
  };
}

export function rewriteGeneratedImagePaths(markdown: string, threadId?: string | null): string {
  return markdown.replace(GENERATED_IMAGE_PATH_RE, (_match, prefix: string, filename: string) => {
    return `${prefix}${generatedImageUrlFromFilename(filename, threadId)}`;
  });
}
