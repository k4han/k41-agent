import { createEffect, createSignal, onCleanup } from "solid-js";

import {
  attachmentKind,
  formatBytes,
  readFileAsBase64,
} from "@/lib/chatAttachments";
import {
  MAX_ATTACHMENTS,
  MAX_FILE_ATTACHMENT_BYTES,
  MAX_IMAGE_ATTACHMENT_BYTES,
  MAX_TEXT_ATTACHMENT_BYTES,
  MAX_TOTAL_ATTACHMENT_BYTES,
  type PendingAttachment,
} from "@/lib/chatTypes";

type ToastType = "success" | "error" | "warning";

export interface UseChatAttachmentsParams {
  getModelSupportsImage: () => boolean;
  showToast: (message: string, type?: ToastType) => void;
}

let nextAttachmentId = 1;

export function useChatAttachments(params: UseChatAttachmentsParams) {
  const { getModelSupportsImage, showToast } = params;
  const [attachments, setAttachments] = createSignal<PendingAttachment[]>([]);
  let draftVersion = 0;

  const revokeAttachmentPreview = (attachment: PendingAttachment) => {
    if (attachment.preview_url) {
      URL.revokeObjectURL(attachment.preview_url);
    }
  };

  const clearAttachments = (itemsToClear: PendingAttachment[]) => {
    if (!itemsToClear.length) {
      return;
    }
    itemsToClear.forEach(revokeAttachmentPreview);
    setAttachments((current) => current.filter((item) => !itemsToClear.includes(item)));
  };

  const clearAllAttachments = () => {
    draftVersion += 1;
    const currentAttachments = attachments();
    if (!currentAttachments.length) {
      return;
    }
    currentAttachments.forEach(revokeAttachmentPreview);
    setAttachments([]);
  };

  const removeAttachment = (id: number) => {
    const target = attachments().find((attachment) => attachment.id === id);
    if (!target) {
      return;
    }
    clearAttachments([target]);
  };

  const addFiles = async (fileList: FileList | File[] | null) => {
    const files = Array.from(fileList || []);
    if (!files.length) {
      return;
    }

    let nextAttachments = [...attachments()];
    const originalCount = nextAttachments.length;
    const version = draftVersion;
    let totalSize = nextAttachments.reduce((sum, attachment) => sum + attachment.size, 0);

    for (const file of files) {
      if (nextAttachments.length >= MAX_ATTACHMENTS) {
        showToast(`Attach up to ${MAX_ATTACHMENTS} files.`, "warning");
        break;
      }

      const fileName = file.name || (file.type.startsWith("image/") ? `screenshot-${Date.now()}.png` : `attachment-${Date.now()}`);
      const kind = attachmentKind(file);
      if (!kind) {
        showToast(`Unsupported file type: ${fileName}`, "warning");
        continue;
      }
      if (kind === "image" && !getModelSupportsImage()) {
        showToast("This model does not support images. Only text and data files can be attached.", "warning");
        continue;
      }

      const maxSize =
        kind === "image"
          ? MAX_IMAGE_ATTACHMENT_BYTES
          : kind === "file"
          ? MAX_FILE_ATTACHMENT_BYTES
          : MAX_TEXT_ATTACHMENT_BYTES;
      if (file.size > maxSize) {
        showToast(`${fileName} exceeds ${formatBytes(maxSize)}.`, "warning");
        continue;
      }
      if (totalSize + file.size > MAX_TOTAL_ATTACHMENT_BYTES) {
        showToast(`Attached files exceed ${formatBytes(MAX_TOTAL_ATTACHMENT_BYTES)}.`, "warning");
        continue;
      }

      try {
        if (kind === "image") {
          nextAttachments = [
            ...nextAttachments,
            {
              id: nextAttachmentId++,
              name: fileName,
              mime_type: file.type || "image/png",
              size: file.size,
              kind,
              base64: await readFileAsBase64(file),
              preview_url: URL.createObjectURL(file),
            },
          ];
        } else if (kind === "file") {
          nextAttachments = [
            ...nextAttachments,
            {
              id: nextAttachmentId++,
              name: file.name,
              mime_type: file.type || "application/octet-stream",
              size: file.size,
              kind,
              base64: await readFileAsBase64(file),
            },
          ];
        } else {
          nextAttachments = [
            ...nextAttachments,
            {
              id: nextAttachmentId++,
              name: file.name,
              mime_type: file.type || "text/plain",
              size: file.size,
              kind,
              content: await file.text(),
            },
          ];
        }
        totalSize += file.size;
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : `Failed to read ${file.name}.`,
          "error",
        );
      }
    }

    const added = nextAttachments.slice(originalCount);
    if (version !== draftVersion) {
      added.forEach(revokeAttachmentPreview);
      return;
    }
    const current = [...attachments()];
    let currentSize = current.reduce((sum, attachment) => sum + attachment.size, 0);
    for (const attachment of added) {
      if (current.length >= MAX_ATTACHMENTS || currentSize + attachment.size > MAX_TOTAL_ATTACHMENT_BYTES) {
        revokeAttachmentPreview(attachment);
        showToast(`Could not attach ${attachment.name} because the draft reached its attachment limit.`, "warning");
        continue;
      }
      current.push(attachment);
      currentSize += attachment.size;
    }
    setAttachments(current);
  };

  createEffect(() => {
    if (getModelSupportsImage()) {
      return;
    }
    const images = attachments().filter((item) => item.kind === "image");
    if (images.length) {
      clearAttachments(images);
      showToast("Removed image attachments: the selected model does not support images.", "warning");
    }
  });

  onCleanup(() => {
    draftVersion += 1;
    attachments().forEach(revokeAttachmentPreview);
  });

  const addTextContent = (
    text: string,
    fileName?: string,
  ) => {
    const size = new TextEncoder().encode(text).length;

    if (size > MAX_TEXT_ATTACHMENT_BYTES) {
      showToast(`Content exceeds ${formatBytes(MAX_TEXT_ATTACHMENT_BYTES)}.`, "warning");
      return;
    }

    let nextAttachments = [...attachments()];
    if (nextAttachments.length >= MAX_ATTACHMENTS) {
      showToast(`Attach up to ${MAX_ATTACHMENTS} files.`, "warning");
      return;
    }

    let totalSize = nextAttachments.reduce((sum, attachment) => sum + attachment.size, 0);
    if (totalSize + size > MAX_TOTAL_ATTACHMENT_BYTES) {
      showToast(`Attached files exceed ${formatBytes(MAX_TOTAL_ATTACHMENT_BYTES)}.`, "warning");
      return;
    }

    nextAttachments.push({
      id: nextAttachmentId++,
      name: fileName || `pasted-content-${Date.now()}.txt`,
      mime_type: "text/plain",
      size,
      kind: "text",
      content: text,
    });

    setAttachments(nextAttachments);
  };

  return {
    attachments,
    addFiles,
    addTextContent,
    removeAttachment,
    clearAttachments,
    clearAllAttachments,
  };
}
