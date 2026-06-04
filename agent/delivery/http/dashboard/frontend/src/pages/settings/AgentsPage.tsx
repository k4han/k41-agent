import { Show } from "solid-js";
import { useParams } from "@solidjs/router";

import { AgentEditPage } from "./agents/AgentEditPage";
import { AgentListPage } from "./agents/AgentListPage";

export function AgentsPage() {
  const params = useParams<{ agentName?: string; newFlag?: string }>();

  return (
    <Show
      when={params.agentName}
      keyed
      fallback={<AgentListPage />}
    >
      {(name) => <AgentEditPage agentName={name} />}
    </Show>
  );
}
