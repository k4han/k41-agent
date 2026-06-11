const GENERATED_IMAGE_EXTENSIONS = new Set(["gif", "jpeg", "jpg", "png", "webp"]);
const GENERATED_IMAGE_PATH_RE =
  /(?:^|[\s("'`])(?:[A-Za-z]:)?(?:[\\/][^<>"'`\r\n]+)*[\\/]generated-images[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp))(?=$|[\s)"'`,.;])/gi;

export const GENERATE_IMAGE_TOOL_NAME = "generate_image";

export function generatedImageUrlFromFilename(filename: string): string {
  return `/dashboard-api/generated-images/${encodeURIComponent(filename)}`;
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

export function generatedImageUrlFromPath(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const match = value.match(
    /(?:^|[\\/])generated-images[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp))$/i,
  );
  const filename = match?.[1] || "";
  const extension = filename.split(".").pop()?.toLowerCase() || "";
  if (!filename || !GENERATED_IMAGE_EXTENSIONS.has(extension)) {
    return null;
  }
  return generatedImageUrlFromFilename(filename);
}

export function generatedImageFromToolResult(result: unknown): {
  filename: string;
  url: string;
} | null {
  const text = typeof result === "string" ? result : "";
  const match = text.match(
    /Generated image saved to:\s*(.+?[\\/](generated-images)[\\/]([A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp)))\s*$/i,
  );
  const filename = match?.[3] || "";
  const url = generatedImageUrlFromPath(match?.[1] || "");
  if (!filename || !url) {
    return null;
  }
  return { filename, url };
}

export function generatedImageAttachmentFromToolResult(result: unknown): {
  name: string;
  mime_type: string;
  size: number;
  kind: "image";
  preview_url: string;
} | null {
  const image = generatedImageFromToolResult(result);
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

export function rewriteGeneratedImagePaths(markdown: string): string {
  return markdown.replace(GENERATED_IMAGE_PATH_RE, (match, filename: string) => {
    const prefix = match[0].match(/[\s("'`]/) ? match[0] : "";
    return `${prefix}${generatedImageUrlFromFilename(filename)}`;
  });
}
