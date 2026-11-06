import { createEffect, createMemo, createSignal, on, type Accessor } from "solid-js";

import {
  isAcpCard,
  readAcpOptionChoices,
  resolveAcpOptionValues,
  writeAcpOptionChoices,
  type AcpConfigOption,
  type AcpOptionValues,
} from "@/lib/acp";
import { apiFetch } from "@/lib/api";
import type { AgentCard } from "@/types";

type AgentStatus = { auth: { config_options: AcpConfigOption[] } };

export function useAcpSessionOptions(card: Accessor<AgentCard | undefined>) {
  const [options, setOptions] = createSignal<AcpConfigOption[]>([]);
  const [choices, setChoices] = createSignal<AcpOptionValues>({});
  const cardName = createMemo(() => (isAcpCard(card()) ? card()?.name : undefined));
  const agentId = createMemo(() => (isAcpCard(card()) ? card()?.acp?.agent : undefined));

  createEffect(on(cardName, (name) => setChoices(name ? readAcpOptionChoices(name) : {})));

  // A plain request rather than a resource: the first probe starts the agent and
  // takes several seconds, which must not suspend the chat page.
  createEffect(on(agentId, (agent) => {
    setOptions([]);
    if (!agent) return;
    apiFetch<AgentStatus>(`/dashboard-api/acp/agents/${encodeURIComponent(agent)}`)
      .then((status) => {
        if (agentId() === agent) setOptions(status.auth.config_options);
      })
      // The picker stays hidden; the turn itself reports a missing install or sign-in.
      .catch(() => undefined);
  }));

  const values = createMemo(() => resolveAcpOptionValues(options(), choices(), card()?.acp?.config_options ?? {}));

  const setValue = (id: string, value: string) => {
    const name = cardName();
    if (!name) return;
    const next = { ...choices(), [id]: value };
    setChoices(next);
    writeAcpOptionChoices(name, next);
  };

  /** Sent whole so what the picker shows is what the session runs, whatever it ran before. */
  const requestOptions = (): AcpOptionValues | undefined => {
    if (!cardName()) return undefined;
    const request = options().length ? values() : choices();
    return Object.keys(request).length ? request : undefined;
  };

  return { options, values, setValue, requestOptions };
}
