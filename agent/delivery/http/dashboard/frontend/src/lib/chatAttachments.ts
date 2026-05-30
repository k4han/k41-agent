import type { TranscriptAttachment } from "@/components/Transcript";
import type {
  ChatAttachmentKind,
  ChatAttachmentPayload,
  PendingAttachment,
} from "@/lib/chatTypes";
import { TEXT_MIME_TYPES, TEXT_EXTENSIONS } from "@/lib/chatTypes";

export function fileExtension(fileName: string): string {
  const lowerName = fileName.toLowerCase();
  const dotIndex = lowerName.lastIndexOf(".");
  return dotIndex >= 0 ? lowerName.slice(dotIndex) : lowerName;
}

export function attachmentKind(file: File): ChatAttachmentKind | null {
  if (file.type.startsWith("image/")) {
    return "image";
  }
  if (file.type.startsWith("text/") || TEXT_MIME_TYPES.has(file.type)) {
    return "text";
  }
  return TEXT_EXTENSIONS.has(fileExtension(file.name)) ? "text" : null;
}

export function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

export function toTranscriptAttachment(attachment: PendingAttachment): TranscriptAttachment {
  return {
    name: attachment.name,
    mime_type: attachment.mime_type,
    size: attachment.size,
    kind: attachment.kind,
  };
}

export function toPayloadAttachment(attachment: PendingAttachment): ChatAttachmentPayload {
  return {
    name: attachment.name,
    mime_type: attachment.mime_type,
    size: attachment.size,
    kind: attachment.kind,
    content: attachment.content,
    base64: attachment.base64,
  };
}
