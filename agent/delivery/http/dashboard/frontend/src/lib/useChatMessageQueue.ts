import { createMemo, createSignal } from "solid-js";

import { apiFetch, patchJson, postJson } from "@/lib/api";
import type { ChatAttachmentPayload } from "@/lib/chatTypes";
import { API_PATHS } from "@/lib/endpoints";
import type { PermissionMode } from "@/lib/permissionMode";

export type QueuedMessage = {
  id: string;
  revision: number;
  text: string;
  attachments: ChatAttachmentPayload[];
  priority: boolean;
  sending: boolean;
  local?: boolean;
  saving?: boolean;
  error?: string;
  permissionMode?: PermissionMode;
};

export type ChatQueueSnapshot = {
  thread_id: string;
  run_id: string | null;
  last_run_id: string | null;
  revision: number;
  status: "idle" | "running" | "waiting" | "paused";
  can_insert: boolean;
  messages: QueuedMessage[];
};

// An active chat stream can outlive the page that opened it.
const [snapshots, setSnapshots] = createSignal(new Map<string, ChatQueueSnapshot>());
const [drafts, setDrafts] = createSignal(new Map<string, QueuedMessage[]>());
const runIds = new Map<string, string>();
const uploads = new Set<string>();

function updateDrafts(scope: string, update: (messages: QueuedMessage[]) => QueuedMessage[]): void {
  setDrafts((current) => {
    const next = new Map(current);
    const messages = update(current.get(scope) || []);
    if (messages.length) next.set(scope, messages);
    else next.delete(scope);
    return next;
  });
}

interface ChatQueueOptions {
  scope: () => string;
  onError: (message: string) => void;
  onSendDraft: (message: QueuedMessage) => void;
}

export function useChatMessageQueue(options: ChatQueueOptions) {
  const state = () => snapshots().get(options.scope());
  const messages = createMemo(() => {
    const saved = state()?.messages || [];
    return [
      ...saved.filter((message) => !message.sending),
      ...(drafts().get(options.scope()) || []).filter((draft) => !saved.some((message) => message.id === draft.id)),
    ];
  });

  const inform = (scope: string, error: unknown) => {
    const message = error instanceof Error ? error.message : "Could not update the message queue.";
    if (options.scope() === scope) options.onError(message);
    return message;
  };

  const applySnapshot = (snapshot: ChatQueueSnapshot, previous?: ChatQueueSnapshot) => {
    const scope = snapshot.thread_id;
    const cached = snapshots().get(scope);
    if (cached && cached !== previous && cached.revision >= snapshot.revision) return;
    setSnapshots((current) => new Map(current).set(scope, snapshot));
    if (snapshot.run_id) bindRun(scope, snapshot.run_id);
    else if (!snapshot.last_run_id) runIds.delete(scope);
    else if (runIds.has(scope)) bindRun(scope, snapshot.last_run_id);
  };

  const refresh = async (threadId: string, signal?: AbortSignal) => {
    const previous = snapshots().get(threadId);
    const previousRunId = runIds.get(threadId);
    try {
      const snapshot = await apiFetch<ChatQueueSnapshot>(API_PATHS.chatMessageQueue(threadId), { signal });
      if (!signal?.aborted && runIds.get(threadId) === previousRunId
        && snapshot?.thread_id === threadId && Array.isArray(snapshot.messages)) {
        applySnapshot(snapshot, previous);
      }
    } catch {
      // The event stream can still synchronize the queue when this request fails.
    }
  };

  const forgetDraft = (scope: string, id: string) => {
    updateDrafts(scope, (entries) => entries.filter((entry) => entry.id !== id));
  };

  const updateDraft = (scope: string, id: string, patch: Partial<QueuedMessage>) => {
    updateDrafts(scope, (entries) => entries.map((entry) => entry.id === id ? { ...entry, ...patch } : entry));
  };

  const flush = async (scope: string) => {
    if (uploads.has(scope) || !runIds.has(scope)) return;
    uploads.add(scope);
    try {
      let runId: string | undefined;
      let entry: QueuedMessage | undefined;
      while ((runId = runIds.get(scope)) && (entry = drafts().get(scope)?.find((message) => !message.error))) {
        const { id, text, attachments, permissionMode } = entry;
        updateDraft(scope, id, { saving: true });
        try {
          const snapshot = await postJson<ChatQueueSnapshot>(API_PATHS.chatMessageQueue(scope), {
            id, text, attachments, run_id: runId, permission_mode: permissionMode,
          });
          applySnapshot(snapshot);
          forgetDraft(scope, id);
        } catch (error) {
          updateDraft(scope, id, { saving: false, error: inform(scope, error) });
          void refresh(scope);
        }
      }
    } finally {
      uploads.delete(scope);
    }
  };

  const bindRun = (scope: string, runId: string) => {
    runIds.set(scope, runId);
    void flush(scope);
  };

  const moveDrafts = (previous: string, scope: string) => {
    if (previous === scope) return;
    const entries = drafts().get(previous);
    if (!entries?.length) return;
    updateDrafts(previous, () => []);
    updateDrafts(scope, (current) => [...current, ...entries]);
  };

  const holdUnboundDrafts = (scope: string, draftScope: string) => {
    if (runIds.has(scope)) return;
    moveDrafts(scope, draftScope);
    updateDrafts(draftScope, (entries) => entries.map((entry) => ({
      ...entry, error: "The conversation did not start. Send this message again when ready.",
    })));
  };

  const enqueue = (text: string, attachments: ChatAttachmentPayload[], scope = options.scope()) => {
    const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    updateDrafts(scope, (entries) => [...entries, { id, revision: 0, text, attachments, priority: false, sending: false, local: true }]);
    void flush(scope);
  };

  const mutate = async (scope: string, request: Promise<ChatQueueSnapshot>) => {
    try {
      applySnapshot(await request);
      return true;
    } catch (error) {
      inform(scope, error);
      void refresh(scope);
      return false;
    }
  };

  const edit = (message: QueuedMessage, text: string) => {
    const scope = options.scope();
    if (message.local) {
      updateDraft(scope, message.id, { text });
      return Promise.resolve(true);
    }
    return mutate(scope, patchJson(`${API_PATHS.chatMessageQueue(scope)}/${encodeURIComponent(message.id)}`, {
      text, revision: message.revision,
    }));
  };

  const remove = (message: QueuedMessage) => {
    const scope = options.scope();
    if (message.local) {
      forgetDraft(scope, message.id);
      return;
    }
    void mutate(scope, apiFetch(`${API_PATHS.chatMessageQueue(scope)}/${encodeURIComponent(message.id)}?revision=${message.revision}`, {
      method: "DELETE",
    }));
  };

  const sendNow = async (message: QueuedMessage, permissionMode: PermissionMode) => {
    const scope = options.scope();
    let revision = message.revision;
    if (message.local) {
      if (message.error && runIds.has(scope)) await refresh(scope);
      if (options.scope() !== scope) return;
      const uploadRunId = runIds.get(scope);
      if (!uploadRunId) {
        options.onSendDraft(message);
        return;
      }
      updateDraft(scope, message.id, { error: undefined, permissionMode });
      await flush(scope);
      const snapshot = snapshots().get(scope);
      if (snapshot?.run_id && snapshot.run_id !== uploadRunId) return;
      const accepted = snapshot?.messages.find((entry) => entry.id === message.id && !entry.sending);
      if (!accepted) return;
      revision = accepted.revision;
    }
    await mutate(scope, postJson(`${API_PATHS.chatMessageQueue(scope)}/${encodeURIComponent(message.id)}/send-now`, {
      revision, permission_mode: permissionMode,
    }));
  };

  return {
    state, messages, enqueue, edit, remove, sendNow, refresh, applySnapshot, bindRun, moveDrafts, holdUnboundDrafts, forgetDraft,
    start: (scope: string) => runIds.delete(scope),
  };
}
