import { apiFetch } from "@/lib/api";
import type { ThreadMessagesPayload } from "@/lib/chatThreads";
import { API_PATHS } from "@/lib/endpoints";

export async function fetchEarlierThreadPage(
  current: ThreadMessagesPayload,
  signal?: AbortSignal,
): Promise<ThreadMessagesPayload> {
  const before = current.next_before;
  if (!current.has_more || !current.active_checkpoint_id || before == null || before <= 0) {
    throw new Error("Earlier conversation messages are unavailable. Reload the conversation.");
  }
  const page = await apiFetch<ThreadMessagesPayload>(
    API_PATHS.thread(current.thread_id, current.active_checkpoint_id, before), { signal },
  );
  if (page.thread_id !== current.thread_id || page.active_checkpoint_id !== current.active_checkpoint_id
    || !page.messages.length || (page.has_more
      && (page.next_before == null || page.next_before <= 0 || page.next_before >= before))) {
    throw new Error("Conversation history changed. Reload the conversation.");
  }
  return page;
}

export async function fetchCompleteThread(
  threadId: string,
  checkpointId?: string,
  signal?: AbortSignal,
): Promise<ThreadMessagesPayload> {
  const latest = await apiFetch<ThreadMessagesPayload>(API_PATHS.thread(threadId, checkpointId), { signal });
  const pages = [latest.messages];
  let page = latest;
  while (page.has_more) {
    page = await fetchEarlierThreadPage(page, signal);
    pages.push(page.messages);
  }
  return { ...latest, messages: pages.reverse().flat(), has_more: false, next_before: null };
}

export async function fetchThreadForRegeneration(
  current: ThreadMessagesPayload,
  messageIndex?: number,
  signal?: AbortSignal,
): Promise<ThreadMessagesPayload> {
  let expanded = current;
  while (!expanded.messages.some(message => message.role === "user"
    && message.message_index !== undefined && message.source_checkpoint_id
    && (messageIndex === undefined || message.message_index < messageIndex)) && expanded.has_more) {
    const page = await fetchEarlierThreadPage(expanded, signal);
    expanded = {
      ...expanded, messages: [...page.messages, ...expanded.messages],
      has_more: page.has_more, next_before: page.next_before,
    };
  }
  return expanded;
}
