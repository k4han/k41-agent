import { For, Show } from "solid-js";
import { ChevronDown, ChevronRight } from "lucide-solid";

export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

export interface TodoProgress {
  current: number;
  total: number;
  activeText: string;
  activeStatus: "pending" | "in_progress" | "completed";
}

export interface ChatTodosProps {
  todos: TodoItem[] | null;
  progress: TodoProgress;
  expanded: boolean;
  onToggle: () => void;
}

export function ChatTodos(props: ChatTodosProps) {
  return (
    <Show when={props.todos && props.todos.length > 0}>
      <div class="chat-todos-box">
        <div
          class="chat-todos-header"
          onClick={() => props.onToggle()}
        >
          <div class="chat-todos-header-left">
            <span class="chat-todos-toggle-icon">
              <Show when={props.expanded} fallback={<ChevronRight size={14} />}>
                <ChevronDown size={14} />
              </Show>
            </span>

            <Show
              when={props.expanded}
              fallback={
                <div class="chat-todos-title">
                  <Show when={props.progress.activeStatus === "completed"}>
                    <span class="todo-status-icon completed">
                      <svg
                        viewBox="0 0 24 24"
                        width="14"
                        height="14"
                        stroke="currentColor"
                        stroke-width="3"
                        fill="none"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      >
                        <polyline points="20 6 9 17 4 12"></polyline>
                      </svg>
                    </span>
                  </Show>
                  <Show when={props.progress.activeStatus === "in_progress"}>
                    <span class="todo-status-icon in-progress">
                      <span class="todo-status-dot"></span>
                    </span>
                  </Show>
                  <Show when={props.progress.activeStatus === "pending"}>
                    <span class="todo-status-icon pending"></span>
                  </Show>

                  <span class="chat-todos-collapsed-text">
                    {props.progress.activeText}
                  </span>
                  <span class="chat-todos-progress">
                    ({props.progress.current}/{props.progress.total})
                  </span>
                </div>
              }
            >
              <span class="chat-todos-collapsed-text" style="font-weight: 600;">
                Todos ({props.progress.current}/{props.progress.total})
              </span>
            </Show>
          </div>

          <div class="chat-todos-header-right">
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              stroke="currentColor"
              stroke-width="2"
              fill="none"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <line x1="8" y1="6" x2="21" y2="6"></line>
              <line x1="8" y1="12" x2="21" y2="12"></line>
              <line x1="8" y1="18" x2="21" y2="18"></line>
              <path d="M3 6h.01"></path>
              <path d="M3 12h.01"></path>
              <path d="M3 18h.01"></path>
            </svg>
          </div>
        </div>

        <Show when={props.expanded}>
          <div class="chat-todos-list">
            <For each={props.todos}>
              {(todo) => (
                <div class={`chat-todo-item ${todo.status}`}>
                  <Show when={todo.status === "completed"}>
                    <span class="todo-status-icon completed">
                      <svg
                        viewBox="0 0 24 24"
                        width="14"
                        height="14"
                        stroke="currentColor"
                        stroke-width="3"
                        fill="none"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      >
                        <polyline points="20 6 9 17 4 12"></polyline>
                      </svg>
                    </span>
                  </Show>
                  <Show when={todo.status === "in_progress"}>
                    <span class="todo-status-icon in-progress">
                      <span class="todo-status-dot"></span>
                    </span>
                  </Show>
                  <Show when={todo.status === "pending"}>
                    <span class="todo-status-icon pending"></span>
                  </Show>

                  <span>{todo.content}</span>
                </div>
              )}
            </For>
          </div>
        </Show>
      </div>
    </Show>
  );
}
