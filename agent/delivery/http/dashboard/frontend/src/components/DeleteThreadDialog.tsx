import { Show } from "solid-js";
import { Trash2 } from "lucide-solid";
import { Dialog } from "@/components/Dialog";
import { truncateText } from "@/lib/utils";

type ThreadLike = {
  thread_id: string;
  title?: string;
};

export function DeleteThreadDialog(props: {
  open: boolean;
  thread: ThreadLike | null;
  deleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={props.open}
      title="Delete Thread"
      onClose={props.onClose}
      footer={
        <div class="row-wrap">
          <button class="btn" type="button" onClick={props.onClose} disabled={props.deleting}>
            Cancel
          </button>
          <button class="btn btn-danger" type="button" onClick={props.onConfirm} disabled={props.deleting}>
            <Trash2 size={14} />
            {props.deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      }
    >
      <p>
        Are you sure you want to delete thread{" "}
        <span class="mono">{truncateText(props.thread?.title || props.thread?.thread_id || "", 60)}</span>?
      </p>
      <p class="muted" style="margin-top: 8px;">This action cannot be undone.</p>
    </Dialog>
  );
}
