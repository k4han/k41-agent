import { createEffect, createMemo, createSignal, untrack } from "solid-js";

import type { ContextWindowData } from "@/components/ContextWindowIndicator";
import { apiFetch } from "@/lib/api";
import { getOrCreateStreamSignals, persistedStreams, type ContextUsage } from "@/lib/chatStreamStore";
import type { AgentCard, AgentChatPayload, ThreadUsagePayload } from "@/types";
import { CONTEXT_CATEGORIES, parseContextBreakdown, type ContextCategory } from "@/types";

const CATEGORY_LABELS: Record<ContextCategory, string> = {
  system_prompt: "System prompt",
  system_tools: "System tools",
  skills: "Skills",
  subagents: "Subagents",
  user_messages: "User messages",
  agent_responses: "Agent responses",
  tool_calls: "Tool calls",
};

export interface UseContextWindowParams {
  getCurrentThreadId: () => string;
  getStreaming: () => boolean;
  getSelectedCard: () => AgentCard | undefined;
  getData: () => AgentChatPayload | undefined;
  getProvider: () => string;
  getModel: () => string;
}

export function useContextWindow(params: UseContextWindowParams) {
  const {
    getCurrentThreadId,
    getStreaming,
    getSelectedCard,
    getData,
    getProvider,
    getModel,
  } = params;

  const [threadUsage, setThreadUsage] = createSignal<ThreadUsagePayload | null>(null);
  const [localReportedUsage, setLocalReportedUsage] = createSignal<{
    threadId: string;
    usage: ContextUsage;
  } | null>(null);

  const getReportedUsage = (threadId: string) => {
    const persisted = persistedStreams.get(threadId)?.reportedContextUsage[0]();
    const local = localReportedUsage();
    return persisted ?? (local?.threadId === threadId ? local.usage : null);
  };

  const updateContextUsage = (usage: ContextUsage, threadId: string) => {
    getOrCreateStreamSignals(threadId).reportedContextUsage[1](usage);
    if (threadId === getCurrentThreadId()) {
      setLocalReportedUsage({ threadId, usage });
    }
  };

  const clearContextUsage = (threadId: string) => {
    persistedStreams.get(threadId)?.reportedContextUsage[1](null);
    if (threadId === getCurrentThreadId()) {
      setLocalReportedUsage(null);
    }
    const current = threadUsage();
    if (current?.thread_id === threadId) {
      setThreadUsage(null);
    }
  };

  const updateCompactedContextUsage = (tokens: number | undefined, threadId: string, breakdown?: unknown) => {
    const contextWindow = contextWindowData().maxTokens;
    clearContextUsage(threadId);
    if (typeof tokens === "number" && Number.isSafeInteger(tokens) && tokens >= 0) {
      const usage: ContextUsage = {
        current_context_tokens: tokens,
        context_window: contextWindow,
        estimated: true,
        context_breakdown: parseContextBreakdown(breakdown),
      };
      // Creating an empty stream here would hide the loaded transcript.
      persistedStreams.get(threadId)?.reportedContextUsage[1](usage);
      if (threadId === getCurrentThreadId()) {
        setLocalReportedUsage({ threadId, usage });
      }
    }
  };

  const fetchThreadUsage = async (threadId: string) => {
    if (!threadId) {
      setThreadUsage(null);
      return;
    }
    const reportAtStart = getReportedUsage(threadId);
    try {
      const data = await apiFetch<ThreadUsagePayload>(
        `/dashboard-api/usage/thread/${encodeURIComponent(threadId)}`,
      );
      if (threadId !== getCurrentThreadId() || getStreaming()
        || getReportedUsage(threadId) !== reportAtStart) {
        return;
      }
      setThreadUsage(data);
      const compactedBreakdown = parseContextBreakdown(data.context_breakdown);
      if (reportAtStart?.estimated && data.context_estimated === true && compactedBreakdown) {
        const usage = { ...reportAtStart, context_breakdown: compactedBreakdown };
        if (typeof data.current_context_tokens === "number"
          && Number.isSafeInteger(data.current_context_tokens) && data.current_context_tokens >= 0) {
          usage.current_context_tokens = data.current_context_tokens;
        }
        persistedStreams.get(threadId)?.reportedContextUsage[1](usage);
        setLocalReportedUsage({ threadId, usage });
      }
      if (reportAtStart?.estimated && data.has_context_usage === true
        && typeof data.latest_input_tokens === "number") {
        const usage: ContextUsage = {
          current_context_tokens: data.latest_input_tokens + (data.latest_output_tokens ?? 0),
          context_window: reportAtStart.context_window,
          input_tokens: data.latest_input_tokens,
          output_tokens: data.latest_output_tokens ?? 0,
          context_breakdown: parseContextBreakdown(data.context_breakdown),
        };
        persistedStreams.get(threadId)?.reportedContextUsage[1](usage);
        setLocalReportedUsage({ threadId, usage });
      }
    } catch (err) {
      console.error("Failed to fetch thread usage:", err);
    }
  };

  createEffect(() => {
    const threadId = getCurrentThreadId();
    const isStreaming = getStreaming();
    if (threadId && !isStreaming) {
      untrack(() => void fetchThreadUsage(threadId));
    } else if (!threadId) {
      setThreadUsage(null);
      setLocalReportedUsage(null);
    }
  });

  const contextWindowData = createMemo<ContextWindowData>(() => {
    const card = getSelectedCard();
    const threadId = getCurrentThreadId();
    const fetchedUsage = threadUsage();
    const usage = fetchedUsage?.thread_id === threadId ? fetchedUsage : null;
    const reportedUsage = getReportedUsage(threadId);
    const payload = getData();

    let maxTokens = 128000;
    let catalogMaxTokens: number | null = null;
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
        catalogMaxTokens = modelOption.context_window;
      }
    }

    // Prefer the catalog for the currently selected model. A stream report
    // carries the window of the model used for that call, which goes stale
    // as soon as the user switches models before the next call.
    if (catalogMaxTokens === null) {
      maxTokens = reportedUsage?.context_window ?? maxTokens;
    }

    const reportedTokens = reportedUsage?.current_context_tokens
      ?? (usage?.has_context_usage !== false
        && typeof usage?.latest_input_tokens === "number"
        ? usage.latest_input_tokens + (usage.latest_output_tokens ?? 0) : undefined);
    const estimatedTokens = usage?.context_estimated === true
      && typeof usage.current_context_tokens === "number"
      && Number.isSafeInteger(usage.current_context_tokens) && usage.current_context_tokens >= 0
      ? usage.current_context_tokens : undefined;
    const estimated = reportedUsage ? reportedUsage.estimated === true
      : typeof reportedTokens !== "number" && estimatedTokens !== undefined;
    const hasReportedUsage = typeof reportedTokens === "number" && !estimated;
    const inputTokens = reportedUsage
      ? reportedUsage.input_tokens ?? reportedUsage.current_context_tokens
      : usage?.latest_input_tokens ?? 0;
    const outputTokens = reportedUsage
      ? reportedUsage.output_tokens ?? 0
      : usage?.latest_output_tokens ?? 0;
    const totalTokens = reportedTokens ?? estimatedTokens ?? 0;

    const totalPercent = maxTokens > 0 ? Math.min(100, (totalTokens / maxTokens) * 100) : 0;

    const reservedTokens = Math.min(8192, Math.floor(maxTokens * 0.04));
    const reservedPercent = maxTokens > 0 ? (reservedTokens / maxTokens) * 100 : 0;

    // The breakdown must describe the same model call or compaction snapshot.
    const breakdown = hasReportedUsage || estimated ? parseContextBreakdown(
      reportedUsage ? reportedUsage.context_breakdown : usage?.context_breakdown,
    ) : undefined;

    const formatNumber = (num: number): string => {
      if (num >= 1000000) {
        return (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
      }
      if (num >= 1000) {
        return (num / 1000).toFixed(1).replace(/\.0$/, "") + "K";
      }
      return num.toString();
    };

    const categories = CONTEXT_CATEGORIES.map((key) => {
      const tokens = breakdown?.[key] ?? null;
      const percent = tokens !== null && maxTokens > 0 ? (tokens / maxTokens) * 100 : 0;
      return {
        key,
        label: CATEGORY_LABELS[key],
        tokens,
        formattedValue: tokens === null ? "—" : `${tokens < 1000 ? tokens.toString() : `${(tokens / 1000).toFixed(1)}k`} tokens (${percent.toFixed(1)}%)`,
      };
    });

    return {
      maxTokens,
      totalTokens,
      inputTokens,
      outputTokens,
      totalPercent,
      reservedPercent,
      categories,
      formattedUsed: estimated ? `~${formatNumber(totalTokens)}`
        : hasReportedUsage ? formatNumber(totalTokens) : "—",
      formattedMax: formatNumber(maxTokens),
      hasReportedUsage,
      estimated,
    };
  });

  return {
    contextWindowData,
    refreshThreadUsage: fetchThreadUsage,
    updateContextUsage,
    clearContextUsage,
    updateCompactedContextUsage,
  };
}
