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

export function useAcpSessionOptions(
  card: Accessor<AgentCard | undefined>,
  cwd: Accessor<string> = () => "",
  conversation?: Accessor<string>,
) {
  const [options, setOptions] = createSignal<AcpConfigOption[]>([]);
  const [choices, setChoices] = createSignal<AcpOptionValues>({});
  const [conversationChoices, setConversationChoices] = createSignal<AcpOptionValues>({});
  const conversationDrafts = new Map<string, AcpOptionValues>();
  const saveConversationChoices = (key: string, values: AcpOptionValues) => {
    conversationDrafts.set(key, values);
    writeAcpOptionChoices(key, values);
  };
  const cardName = createMemo(() => (isAcpCard(card()) ? card()?.name : undefined));
  const scope = createMemo(() => {
    const settings = card()?.acp;
    return cardName() && settings ? acpOptionScope(cardName()!, settings, cwd()) : undefined;
  });
  const conversationScope = (id: string): string | undefined => {
    const settings = card()?.acp;
    return cardName() && settings && id
      ? JSON.stringify([cardName(), settings.agent, "conversation", id])
      : undefined;
  };
  const conversationKey = createMemo(() => conversationScope(conversation?.() || ""));
  const statusUrl = createMemo(() => {
    const settings = isAcpCard(card()) ? card()?.acp : undefined;
    return settings ? API_PATHS.acpAgent(settings.agent, undefined, cwd()) : undefined;
  });

  createEffect(on(scope, (name) => setChoices(name
    ? readAcpOptionChoices(name, cardName())
    : {})));
  createEffect(on(conversationKey, (name) => setConversationChoices(name
    ? conversationDrafts.get(name) ?? readAcpOptionChoices(name)
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

  const values = createMemo(() => {
    const chosen = { ...choices(), ...conversationChoices() };
    const configured = card()?.acp?.config_options ?? {};
    // Workspace discovery starts a fresh probe while the submitted turn keeps running.
    return options().length ? resolveAcpOptionValues(options(), chosen, configured) : { ...configured, ...chosen };
  });

  const setValue = (id: string, value: string) => {
    const name = scope();
    if (!name) return;
    const next = { ...choices(), [id]: value };
    setChoices(next);
    writeAcpOptionChoices(name, next);
    const key = conversationKey();
    if (key) {
      const nextConversation = { ...conversationChoices(), [id]: value };
      setConversationChoices(nextConversation);
      saveConversationChoices(key, nextConversation);
    }
  };

  /** Sent whole so what the picker shows is what the session runs, whatever it ran before. */
  const requestOptions = (): AcpOptionValues | undefined => {
    if (!cardName()) return undefined;
    const request = values();
    if (!Object.keys(request).length) return undefined;
    const key = conversationKey();
    if (key) {
      // Pin defaults too, so a new probe cannot change what the conversation uses.
      setConversationChoices(request);
      saveConversationChoices(key, request);
    }
    return request;
  };

  const bindConversation = (id: string) => {
    const key = conversationScope(id);
    if (key) saveConversationChoices(key, values());
  };

  return { options, values, setValue, requestOptions, bindConversation };
}
