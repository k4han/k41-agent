import { A } from "@solidjs/router";
import { MessageSquare } from "lucide-solid";
import { createMemo, createSignal, For, Match, Switch } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { chatThreadHref, sortThreadsByLatest, threadWorkspaceLabel, type ThreadListPayload } from "@/lib/chatThreads";
import { useDashboardData } from "@/lib/dashboardData";
import { API_PATHS } from "@/lib/endpoints";

function ConversationSearch(props: { onSelect: () => void }) {
  const [search, setSearch] = createSignal("");
  const query = createMemo(() => search().trim().toLowerCase());
  const history = useDashboardData<ThreadListPayload>(API_PATHS.chatHistory);
  const conversations = createMemo(() => {
    if (history.loading()) return [];
    return sortThreadsByLatest(history.data()?.threads ?? []).map((thread) => {
      const title = thread.title?.trim() || thread.thread_id;
      const project = threadWorkspaceLabel(thread);
      return { title, project, href: chatThreadHref(thread.thread_id), searchText: `${title}\n${project}`.toLowerCase() };
    });
  });
  const matches = createMemo(() => {
    const term = query();
    return term ? conversations().filter((conversation) => conversation.searchText.includes(term)) : conversations().slice(0, 10);
  });

  const handleResultClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    props.onSelect();
  };

  return (
    <>
      <input
        class="input"
        type="search"
        aria-label="Search conversations"
        placeholder="Search by title or project"
        autofocus
        value={search()}
        onInput={(event) => setSearch(event.currentTarget.value)}
      />
      <Switch fallback={
        <ul class="conversation-search-results" aria-label={query() ? "Matching conversations" : "Recent conversations"}>
          <For each={matches()}>
            {(conversation) => (
              <li>
                <A class="nav-link conversation-search-result" activeClass="" href={conversation.href} onClick={handleResultClick}>
                  <MessageSquare size={15} aria-hidden="true" />
                  <span class="conversation-search-result-text">
                    <span class="nav-label" title={conversation.title}>{conversation.title}</span>
                    <span class="nav-label conversation-search-project">{conversation.project}</span>
                  </span>
                </A>
              </li>
            )}
          </For>
        </ul>
      }>
        <Match when={history.loading()}>
          <p class="muted" role="status">Loading conversations…</p>
        </Match>
        <Match when={history.error()}>
          {(error) => (
            <div class="stack">
              <p role="alert">{error()}</p>
              <button class="btn btn-sm" type="button" onClick={() => void history.load()}>Retry</button>
            </div>
          )}
        </Match>
        <Match when={matches().length === 0}>
          <p class="muted" role="status">{query() ? "No matching conversations." : "No conversations yet."}</p>
        </Match>
      </Switch>
    </>
  );
}

export function ConversationSearchDialog(props: { open: boolean; onClose: () => void; onSelect: () => void }) {
  return (
    <Dialog
      open={props.open}
      title="Search conversations"
      class="conversation-search-dialog"
      bodyClass="conversation-search-body"
      onClose={props.onClose}
    >
      <ConversationSearch onSelect={props.onSelect} />
    </Dialog>
  );
}
