import { createEffect, createMemo, createSignal } from "solid-js";

import type { ContextWindowData } from "@/components/ContextWindowIndicator";
import { apiFetch } from "@/lib/api";
import type { ChatTranscriptItem } from "@/lib/chatStreamStore";
import type { PendingAttachment } from "@/lib/chatTypes";
import type { AgentCard, AgentsPayload, ThreadUsagePayload } from "@/types";

export interface UseContextWindowParams {
  getCurrentThreadId: () => string;
  getStreaming: () => boolean;
  getSelectedCard: () => AgentCard | undefined;
  getData: () => AgentsPayload | undefined;
  getProvider: () => string;
  getModel: () => string;
  getAttachments: () => PendingAttachment[];
  getItems: () => ChatTranscriptItem[];
}

export function useContextWindow(params: UseContextWindowParams) {
  const {
    getCurrentThreadId,
    getStreaming,
    getSelectedCard,
    getData,
    getProvider,
    getModel,
    getAttachments,
    getItems,
  } = params;

  const [threadUsage, setThreadUsage] = createSignal<ThreadUsagePayload | null>(null);

  const fetchThreadUsage = async (threadId: string) => {
    if (!threadId) {
      setThreadUsage(null);
      return;
    }
    try {
      const data = await apiFetch<ThreadUsagePayload>(
        `/dashboard-api/usage/thread/${encodeURIComponent(threadId)}`,
      );
      setThreadUsage(data);
    } catch (err) {
      console.error("Failed to fetch thread usage:", err);
    }
  };

  createEffect(() => {
    const threadId = getCurrentThreadId();
    const isStreaming = getStreaming();
    if (threadId && !isStreaming) {
      void fetchThreadUsage(threadId);
    } else if (!threadId) {
      setThreadUsage(null);
    }
  });

  const contextWindowData = createMemo<ContextWindowData>(() => {
    const card = getSelectedCard();
    const usage = threadUsage();
    const payload = getData();

    let maxTokens = 128000;
    if (payload) {
      const activeProvider = getProvider() || card?.provider || "default";
      const activeModel = getModel() || card?.model || "";
      const resolvedProv = activeProvider === "default" ? payload.default_provider : activeProvider;
      const catalog = payload.model_catalogs?.find((c) => c.provider === resolvedProv);
      const resolvedMod = (activeModel === "" || activeModel === "provider default")
        ? (activeProvider === "default" ? payload.default_model : (catalog?.default_model || "default"))
        : activeModel;
      const modelOption = catalog?.models?.find((m) => m.id === resolvedMod);
      if (modelOption && typeof modelOption.context_window === "number") {
        maxTokens = modelOption.context_window;
      }
    }

    const totalTokens = usage?.total_tokens || 0;
    const inputTokens = usage?.input_tokens || 0;
    const outputTokens = usage?.output_tokens || 0;

    const totalPercent = maxTokens > 0 ? Math.min(100, (totalTokens / maxTokens) * 100) : 0;

    const reservedTokens = Math.min(8192, Math.floor(maxTokens * 0.04));
    const reservedPercent = maxTokens > 0 ? (reservedTokens / maxTokens) * 100 : 0;

    const systemPrompt = card?.system_prompt || "";
    const systemTokens = Math.max(100, Math.floor(systemPrompt.length / 3.8));
    const systemPercent = maxTokens > 0 ? (systemTokens / maxTokens) * 100 : 0;

    const tools = card?.tools || [];
    const toolTokens = tools.length * 420;
    const toolPercent = maxTokens > 0 ? (toolTokens / maxTokens) * 100 : 0;

    let fileCount = getAttachments().length;
    const activeItems = getItems();
    if (Array.isArray(activeItems)) {
      activeItems.forEach((item) => {
        if (item.type === "message" && Array.isArray(item.attachments)) {
          fileCount += item.attachments.length;
        }
      });
    }
    const fileTokens = fileCount * 1500;
    const filePercent = maxTokens > 0 ? (fileTokens / maxTokens) * 100 : 0;

    const messagesTokens = Math.max(0, inputTokens - systemTokens - toolTokens - fileTokens);
    const messagesPercent = maxTokens > 0 ? (messagesTokens / maxTokens) * 100 : 0;

    const formatNumber = (num: number): string => {
      if (num >= 1000000) {
        return (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
      }
      if (num >= 1000) {
        return (num / 1000).toFixed(1).replace(/\.0$/, "") + "K";
      }
      return num.toString();
    };

    const formatPercent = (p: number, tokensVal: number): string => {
      if (tokensVal > 0 && p < 0.1) return "0.1%";
      return p.toFixed(1).replace(/\.0$/, "") + "%";
    };

    return {
      maxTokens,
      totalTokens,
      inputTokens,
      outputTokens,
      totalPercent,
      reservedPercent,
      systemPercent: formatPercent(systemPercent, systemTokens),
      toolPercent: formatPercent(toolPercent, toolTokens),
      messagesPercent: formatPercent(messagesPercent, messagesTokens),
      filePercent: formatPercent(filePercent, fileTokens),
      formattedUsed: formatNumber(totalTokens),
      formattedMax: formatNumber(maxTokens),
    };
  });

  return { contextWindowData };
}
