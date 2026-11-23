import { Show } from "solid-js";
import { Trash2 } from "lucide-solid";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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

  const message = () => (
    <>
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
    </>
  );

  return (
    <ConfirmDialog
      open={props.open}
      size="sm"
      title={isBulkDelete() ? "Delete Threads" : "Delete Thread"}
      icon={<Trash2 size={17} />}
      message={message()}
      confirmLabel={isBulkDelete() ? `Delete ${count()} Threads` : "Delete"}
      confirmVariant="danger"
      loading={props.deleting}
      onClose={props.onClose}
      onConfirm={props.onConfirm}
    />
  );
}
