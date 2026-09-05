import { Show } from "solid-js";
import { Trash2, Loader2 } from "lucide-solid";
import { Dialog } from "@/components/Dialog";
import { truncateText } from "@/lib/utils";

type ThreadLike = {
  thread_id: string;
  title?: string;
};

export function DeleteThreadDialog(props: {
  open: boolean;
  thread: ThreadLike | null;
  threadCount?: number;
  deleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const count = () => props.threadCount ?? (props.thread ? 1 : 0);
  const isBulkDelete = () => count() > 1;

  return (
    <Dialog
      open={props.open}
      size="sm"
      title={isBulkDelete() ? "Delete Threads" : "Delete Thread"}
      icon={<Trash2 size={17} />}
      iconVariant="danger"
      onClose={props.onClose}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", gap: "10px", width: "100%" }}>
          <button class="btn" type="button" onClick={props.onClose} disabled={props.deleting}>
            Cancel
          </button>
          <button class="btn btn-danger" type="button" onClick={props.onConfirm} disabled={props.deleting}>
            <Show when={props.deleting} fallback={<Trash2 size={14} />}>
              <Loader2 size={14} class="spin-icon" />
            </Show>
            {props.deleting ? "Deleting..." : isBulkDelete() ? `Delete ${count()} Threads` : "Delete"}
          </button>
        </div>
      }
    >
      <div class="confirm-dialog-content">
        <Show
          when={isBulkDelete()}
          fallback={
            <p class="confirm-dialog-message">
              Are you sure you want to delete{" "}
              <strong class="mono" style={{ "word-break": "break-all" }}>
                {truncateText(props.thread?.title || props.thread?.thread_id || "", 60)}
              </strong>?
            </p>
          }
        >
          <p class="confirm-dialog-message">
            Are you sure you want to delete <strong>{count()}</strong> selected threads?
          </p>
        </Show>
        <p class="confirm-dialog-warning">This action is permanent and cannot be undone.</p>
      </div>
    </Dialog>
  );
}

