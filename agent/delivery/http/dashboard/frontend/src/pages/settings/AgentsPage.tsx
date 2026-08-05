import { Show } from "solid-js";
import { useLocation, useParams } from "@solidjs/router";

import { AgentEditPage } from "./agents/AgentEditPage";
import { AgentListPage } from "./agents/AgentListPage";

const AGENTS_NEW_HREF = "/settings/agents/new";

export function AgentsPage() {
  const params = useParams<{ agentName?: string }>();
  const location = useLocation();
  const isCreate = () => location.pathname === AGENTS_NEW_HREF;

  return (
    <Show
      when={params.agentName}
      keyed
      fallback={
        <Show when={isCreate()} fallback={<AgentListPage />}>
          <AgentEditPage />
        </Show>
      }
    >
      {(name) => <AgentEditPage agentName={name} />}
    </Show>
  );
}
