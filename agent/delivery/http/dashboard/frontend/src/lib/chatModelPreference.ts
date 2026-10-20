export type ChatModelPreference = { agentName?: string; provider: string; model: string; customModel?: true };

const storageKey = "k41.dashboard.lastChatModel";

export function readChatModelPreference(): ChatModelPreference | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    if (
      !value
      || typeof value.provider !== "string"
      || typeof value.model !== "string"
      || !value.provider.trim()
      || !value.model.trim()
    ) {
      return null;
    }
    const agentName = typeof value.agentName === "string" ? value.agentName.trim() : "";
    return {
      provider: value.provider.trim(),
      model: value.model.trim(),
      ...(agentName ? { agentName } : {}),
      ...(value.customModel === true ? { customModel: true as const } : {}),
    };
  } catch {
    return null;
  }
}

export function writeChatModelPreference(selection: ChatModelPreference): void {
  const provider = selection.provider.trim();
  const model = selection.model.trim();
  const agentName = selection.agentName?.trim();
  if (!provider || !model) return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify({
      provider, model,
      ...(agentName ? { agentName } : {}),
      ...(selection.customModel === true ? { customModel: true } : {}),
    }));
  } catch {
    // Chat remains usable when browser storage is unavailable.
  }
}
