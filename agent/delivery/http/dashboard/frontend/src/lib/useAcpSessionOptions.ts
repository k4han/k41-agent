import { createEffect, createMemo, createSignal, on, onCleanup, type Accessor } from "solid-js";

import {
  isAcpCard,
  acpOptionScope,
  readAcpOptionChoices,
  resolveAcpOptionValues,
  writeAcpOptionChoices,
  type AcpAgentStatus,
  type AcpConfigOption,
  type AcpOptionValues,
} from "@/lib/acp";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import type { AgentCard } from "@/types";

export function useAcpSessionOptions(card: Accessor<AgentCard | undefined>, cwd: Accessor<string> = () => "") {
  const [options, setOptions] = createSignal<AcpConfigOption[]>([]);
  const [choices, setChoices] = createSignal<AcpOptionValues>({});
  const cardName = createMemo(() => (isAcpCard(card()) ? card()?.name : undefined));
  const scope = createMemo(() => {
    const settings = card()?.acp;
    return cardName() && settings ? acpOptionScope(cardName()!, settings, cwd()) : undefined;
  });
  const statusUrl = createMemo(() => {
    const settings = isAcpCard(card()) ? card()?.acp : undefined;
    return settings ? API_PATHS.acpAgent(settings.agent, settings.agent_name, cwd()) : undefined;
  });

  createEffect(on(scope, (name) => setChoices(name
    ? readAcpOptionChoices(name, card()?.acp?.agent_name?.trim() ? undefined : cardName())
    : {})));

  // A plain request rather than a resource: the first probe starts the agent and
  // takes several seconds, which must not suspend the chat page.
  createEffect(on(statusUrl, (url) => {
    setOptions([]);
    if (!url) return;
    let active = true;
    onCleanup(() => { active = false; });
    apiFetch<AcpAgentStatus>(url)
      .then((status) => {
        if (active) setOptions(status.auth.config_options);
      })
      // The picker stays hidden; the turn itself reports a missing install or sign-in.
      .catch(() => undefined);
  }));

  const values = createMemo(() => resolveAcpOptionValues(options(), choices(), card()?.acp?.config_options ?? {}));

  const setValue = (id: string, value: string) => {
    const name = scope();
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
